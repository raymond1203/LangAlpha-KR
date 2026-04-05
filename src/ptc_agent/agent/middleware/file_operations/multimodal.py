"""Multimodal middleware for injecting images and PDFs into LLM conversations.

This middleware intercepts read_file tool calls for image/PDF paths and URLs,
downloading the content and injecting it as a HumanMessage content block
for multimodal models.

Architecture:
- Intercepts read_file tool calls that match image/PDF patterns
- Downloads content from sandbox or URL
- Injects as HumanMessage using LangGraph's Command pattern
- Passes through non-visual read_file calls unchanged

Supported formats:
- Images: PNG, JPG, JPEG, GIF, WebP
- Documents: PDF
"""

import base64
import contextvars
import io
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command
from PIL import Image

from src.llms.llm import get_input_modalities

logger = logging.getLogger(__name__)

# Supported image extensions
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# Supported document extensions
DOCUMENT_EXTENSIONS = frozenset({".pdf"})

# Combined visual extensions
VISUAL_EXTENSIONS = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS

# Active model for the current async context (set in awrap_model_call, read in awrap_tool_call)
_active_model: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'multimodal_model', default=None
)


def _strip_unsupported_content_blocks(
    messages: list, has_image: bool, has_pdf: bool
) -> list:
    """Replace unsupported image/file content blocks with text placeholders.

    Returns the original list if no changes needed (avoids unnecessary copies).
    Does not mutate the original messages (checkpoint integrity).
    """
    modified = False
    result = []

    for msg in messages:
        content = msg.content if hasattr(msg, "content") else None
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_blocks = []
        msg_modified = False
        for block in content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue

            block_type = block.get("type", "")

            # Check image_url blocks
            if block_type == "image_url" and not has_image:
                new_blocks.append({
                    "type": "text",
                    "text": "[Image attached in prior turn — not visible to current model]"
                })
                msg_modified = True
                continue

            # Check file blocks (PDF)
            if block_type == "file" and block.get("mime_type", "").startswith("application/pdf") and not has_pdf:
                new_blocks.append({
                    "type": "text",
                    "text": "[PDF attached in prior turn — not visible to current model]"
                })
                msg_modified = True
                continue

            new_blocks.append(block)

        if msg_modified:
            modified = True
            # Create a copy of the message with new content
            if hasattr(msg, "model_copy"):
                new_msg = msg.model_copy(update={"content": new_blocks})
            else:
                new_msg = type(msg)(content=new_blocks, **{
                    k: v for k, v in (msg.__dict__ if hasattr(msg, "__dict__") else {}).items()
                    if k != "content"
                })
            result.append(new_msg)
        else:
            result.append(msg)

    return result if modified else messages


def _is_visual_request(file_path: str) -> bool:
    """Check if the file_path is a visual file (image or PDF - URL or file extension).

    Args:
        file_path: Path or URL to check.

    Returns:
        True if this is a visual file request, False otherwise.
    """
    # Check for URLs (could be image or PDF)
    if file_path.startswith(("http://", "https://")):
        return True

    # Check for visual file extensions (images + documents)
    suffix = Path(file_path).suffix.lower()
    return suffix in VISUAL_EXTENSIONS


class MultimodalMiddleware(AgentMiddleware):
    """Middleware that intercepts read_file for images/PDFs and injects them as HumanMessage.

    When read_file is called with an image/PDF path or URL, this middleware:
    1. Executes the tool to get the acknowledgment message
    2. Downloads the content (from URL or sandbox)
    3. Converts to base64 for universal LLM provider compatibility
    4. Returns a Command that injects both:
       - The ToolMessage (for tool call completion)
       - A HumanMessage with the base64 content (for multimodal model processing)

    Non-visual read_file calls pass through unchanged.

    Note: Content is always downloaded and converted to base64 because many LLM
    providers (like Anthropic) cannot fetch external URLs directly.

    Supported formats:
    - Images: PNG, JPG, JPEG, GIF, WebP (using image_url content block)
    - Documents: PDF (using LangChain's file content block)

    Attributes:
        sandbox: PTCSandbox instance for reading files from sandbox paths
    """

    TOOL_NAME = "read_file"

    # MIME type mapping for supported extensions
    MIME_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }

    def __init__(
        self,
        *,
        sandbox: Any | None = None,
        model_name: str | None = None,
        custom_modalities: list[str] | None = None,
    ) -> None:
        """Initialize the MultimodalMiddleware.

        Args:
            sandbox: PTCSandbox instance for reading files from sandbox paths.
                    Required for local file support.
            model_name: LLM model name for capability checking (from models.json).
                       Used to determine which input modalities the model supports.
            custom_modalities: Override modalities for custom models (from user
                              preferences). When set, bypasses the models.json lookup.
        """
        super().__init__()
        self.sandbox = sandbox
        self.model_name = model_name
        self.custom_modalities = custom_modalities

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        """Synchronous wrapper - delegates to async implementation.

        Note: File handling requires async, so this sync wrapper is limited.
        For production use, prefer async execution via awrap_tool_call.
        """
        tool_call = request.tool_call
        tool_name = tool_call.get("name")

        # Pass through non-target tools
        if tool_name != self.TOOL_NAME:
            return handler(request)

        # Check if this is a visual request
        tool_args = tool_call.get("args", {})
        file_path = tool_args.get("file_path", "")

        if not _is_visual_request(file_path):
            return handler(request)

        # For sync execution, just run the tool without content injection
        logger.warning(
            "[MULTIMODAL] Sync execution detected. Visual content will not be injected. "
            "Use async execution for full functionality."
        )
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """Strip unsupported content blocks from historical messages for text-only models.

        When a user switches from a vision model to a text-only model mid-thread,
        the checkpoint contains image/PDF content blocks that would cause 400 errors.
        This method replaces those blocks with text placeholders.
        """
        model = self.model_name
        token = _active_model.set(model)
        try:
            modalities = get_input_modalities(model, custom_modalities=self.custom_modalities) if model else ["text"]
            has_image = "image" in modalities
            has_pdf = "pdf" in modalities

            # Fast path: model supports all visual types
            if has_image and has_pdf:
                return await handler(request)

            # Strip unsupported content blocks from messages
            sanitized = _strip_unsupported_content_blocks(request.messages, has_image, has_pdf)
            if sanitized is not request.messages:
                return await handler(request.override(messages=sanitized))
            return await handler(request)
        finally:
            _active_model.reset(token)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """Async wrapper that intercepts read_file for images/PDFs and injects as HumanMessage.

        Args:
            request: Tool call request containing tool_call dict with name, args, id
            handler: Next handler in middleware chain

        Returns:
            Command with updated messages (ToolMessage + HumanMessage with content),
            or the original result if not a visual request or on error
        """
        tool_call = request.tool_call
        tool_name = tool_call.get("name")

        # Pass through non-target tools
        if tool_name != self.TOOL_NAME:
            return await handler(request)

        tool_call_id = tool_call.get("id", "unknown")
        tool_args = tool_call.get("args", {})
        file_path = tool_args.get("file_path", "")

        # Pass through non-visual requests
        if not _is_visual_request(file_path):
            return await handler(request)

        # Check model capabilities — skip content injection for unsupported modalities
        model = _active_model.get() or self.model_name
        modalities = get_input_modalities(model, custom_modalities=self.custom_modalities) if model else ["text"]

        # Determine file extension (from local path or URL path)
        if file_path.startswith(("http://", "https://")):
            ext = Path(urlparse(file_path).path).suffix.lower()
        else:
            ext = Path(file_path).suffix.lower()

        _unsupported_note = (
            "\n\n<system-reminder>"
            "You cannot view this file directly because the current model does not support "
            "this input type. Be transparent with the user about this limitation and suggest "
            "they try switching to a model that supports image/PDF input. "
            "Work in best effort to answer their query."
            "</system-reminder>"
        )
        if ext in IMAGE_EXTENSIONS and "image" not in modalities:
            # Model can't view images — run the tool normally, append a note
            result = await handler(request)
            result_content = result.content if hasattr(result, "content") else str(result)
            return ToolMessage(
                content=result_content + _unsupported_note,
                tool_call_id=tool_call_id,
            )
        if ext in DOCUMENT_EXTENSIONS and "pdf" not in modalities:
            result = await handler(request)
            result_content = result.content if hasattr(result, "content") else str(result)
            return ToolMessage(
                content=result_content + _unsupported_note,
                tool_call_id=tool_call_id,
            )
        # URLs without recognizable extension: block if model is text-only
        if ext not in VISUAL_EXTENSIONS and "image" not in modalities and "pdf" not in modalities:
            result = await handler(request)
            result_content = result.content if hasattr(result, "content") else str(result)
            return ToolMessage(
                content=result_content + _unsupported_note,
                tool_call_id=tool_call_id,
            )

        logger.debug(f"[MULTIMODAL] Intercepting read_file for visual content: {file_path}")

        # Execute the tool to get the acknowledgment message
        result = await handler(request)

        # Check if the tool returned an error
        result_content = result.content if hasattr(result, "content") else str(result)
        if result_content.startswith("ERROR:"):
            return result

        # Handle URL content (images or PDFs)
        if file_path.startswith(("http://", "https://")):
            return await self._handle_url_content(file_path, result, tool_call_id)

        # Handle local sandbox files
        return await self._handle_sandbox_content(file_path, result, tool_call_id)

    def _build_content_blocks(
        self,
        b64_string: str,
        file_path: str,
        mime_type: str,
    ) -> list[dict[str, Any]]:
        """Build appropriate content blocks based on file type.

        Args:
            b64_string: Base64-encoded file content
            file_path: Original file path (for display name)
            mime_type: MIME type of the content

        Returns:
            List of content block dicts for HumanMessage
        """
        if mime_type.startswith("image/"):
            # Image: use data URI with image_url block
            data_uri = f"data:{mime_type};base64,{b64_string}"
            return [
                {"type": "text", "text": "[Viewing image]"},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        elif mime_type == "application/pdf":
            # PDF: use LangChain's file content block format (converts to Anthropic's document format)
            filename = Path(file_path).name
            return [
                {"type": "text", "text": f"[Viewing PDF: {filename}]"},
                {"type": "file", "base64": b64_string, "mime_type": mime_type, "filename": filename},
            ]
        return []

    async def _handle_url_content(
        self,
        url: str,
        tool_result: Any,
        tool_call_id: str,
    ) -> Any:
        """Handle URL content (image or PDF) by downloading and injecting as base64 HumanMessage.

        Downloads the content and converts to base64 to ensure compatibility with
        all LLM providers. Many providers (like Anthropic) cannot fetch external
        URLs directly, especially from private S3 buckets.

        Args:
            url: Content URL to download
            tool_result: Original tool result (acknowledgment message)
            tool_call_id: Tool call ID for error messages

        Returns:
            Command with ToolMessage + HumanMessage, or ToolMessage with error
        """
        try:
            # Download the content
            async with httpx.AsyncClient(http2=True, timeout=30.0) as client:
                response = await client.get(url, follow_redirects=True)

                if response.status_code != 200:
                    logger.warning(f"[MULTIMODAL] Failed to download content: {url} (status {response.status_code})")
                    return ToolMessage(
                        content=f"ERROR: Could not download content (HTTP {response.status_code}): {url}",
                        tool_call_id=tool_call_id,
                    )

                content_bytes = response.content
                if not content_bytes:
                    logger.warning(f"[MULTIMODAL] Empty content from URL: {url}")
                    return ToolMessage(
                        content=f"ERROR: Empty content from URL: {url}",
                        tool_call_id=tool_call_id,
                    )

            # Detect type from URL extension
            suffix = Path(urlparse(url).path).suffix.lower()

            if suffix in DOCUMENT_EXTENSIONS:
                # PDF: validate magic bytes
                if not content_bytes.startswith(b"%PDF"):
                    logger.warning(f"[MULTIMODAL] Invalid PDF data from URL {url}")
                    return ToolMessage(
                        content=f"ERROR: Invalid PDF file from URL: {url}",
                        tool_call_id=tool_call_id,
                    )
                mime_type = "application/pdf"
            else:
                # Image: validate and detect format using PIL
                try:
                    img = Image.open(io.BytesIO(content_bytes))
                    img.verify()
                    pil_format = img.format
                except Exception as e:
                    logger.warning(f"[MULTIMODAL] Invalid image data from URL {url}: {e}")
                    return ToolMessage(
                        content=f"ERROR: Invalid image data from URL: {url}",
                        tool_call_id=tool_call_id,
                    )

                # Map PIL format to MIME type
                format_to_mime = {
                    "PNG": "image/png",
                    "JPEG": "image/jpeg",
                    "GIF": "image/gif",
                    "WEBP": "image/webp",
                }
                mime_type = format_to_mime.get(pil_format, "image/png")

            # Encode as base64
            b64_string = base64.b64encode(content_bytes).decode("utf-8")

            # Build content blocks based on type
            content_blocks = self._build_content_blocks(b64_string, url, mime_type)
            if not content_blocks:
                return ToolMessage(
                    content=f"ERROR: Unsupported content type: {mime_type}",
                    tool_call_id=tool_call_id,
                )

            human_message = HumanMessage(content=content_blocks)  # type: ignore[arg-type]

            logger.info(
                f"[MULTIMODAL] Injecting URL content as base64 HumanMessage: {url} "
                f"({len(content_bytes)} bytes, {mime_type})"
            )

            return Command(
                update={
                    "messages": [
                        tool_result,
                        human_message,
                    ]
                }
            )

        except httpx.TimeoutException:
            logger.warning(f"[MULTIMODAL] Timeout downloading content: {url}")
            return ToolMessage(
                content=f"ERROR: Timeout downloading content: {url}",
                tool_call_id=tool_call_id,
            )
        except httpx.RequestError as e:
            logger.warning(f"[MULTIMODAL] Network error downloading content {url}: {e}")
            return ToolMessage(
                content=f"ERROR: Network error downloading content: {e}",
                tool_call_id=tool_call_id,
            )
        except Exception as e:
            logger.warning(f"[MULTIMODAL] Unexpected error handling URL content {url}: {e}")
            return ToolMessage(
                content=f"ERROR: Failed to load content: {e}",
                tool_call_id=tool_call_id,
            )

    async def _handle_sandbox_content(
        self,
        file_path: str,
        tool_result: Any,
        tool_call_id: str,
    ) -> Any:
        """Handle sandbox file (image or PDF) by downloading and injecting as base64 HumanMessage.

        Args:
            file_path: Sandbox path to file
            tool_result: Original tool result (acknowledgment message)
            tool_call_id: Tool call ID for error messages

        Returns:
            Command with ToolMessage + HumanMessage, or ToolMessage with error
        """
        if not self.sandbox:
            logger.warning("[MULTIMODAL] No sandbox available for local file reading")
            return ToolMessage(
                content=f"ERROR: Cannot read local file without sandbox: {file_path}",
                tool_call_id=tool_call_id,
            )

        try:
            # Download file bytes from sandbox
            file_bytes = await self.sandbox.adownload_file_bytes(file_path)
            if not file_bytes:
                logger.warning(f"[MULTIMODAL] Failed to download file: {file_path}")
                return ToolMessage(
                    content=f"ERROR: Could not read file: {file_path}",
                    tool_call_id=tool_call_id,
                )

            # Determine MIME type from extension
            ext = Path(file_path).suffix.lower()
            mime_type = self.MIME_TYPES.get(ext)

            if not mime_type:
                return ToolMessage(
                    content=f"ERROR: Unsupported file type: {ext}",
                    tool_call_id=tool_call_id,
                )

            # Validate PDF magic bytes
            if mime_type == "application/pdf" and not file_bytes.startswith(b"%PDF"):
                logger.warning(f"[MULTIMODAL] Invalid PDF file: {file_path}")
                return ToolMessage(
                    content=f"ERROR: Invalid PDF file: {file_path}",
                    tool_call_id=tool_call_id,
                )

            # Encode as base64
            b64_string = base64.b64encode(file_bytes).decode("utf-8")

            # Build content blocks based on type
            content_blocks = self._build_content_blocks(b64_string, file_path, mime_type)
            if not content_blocks:
                return ToolMessage(
                    content=f"ERROR: Unsupported content type: {mime_type}",
                    tool_call_id=tool_call_id,
                )

            human_message = HumanMessage(content=content_blocks)  # type: ignore[arg-type]

            logger.info(
                f"[MULTIMODAL] Injecting sandbox file as HumanMessage: {file_path} "
                f"({len(file_bytes)} bytes, {mime_type})"
            )

            return Command(
                update={
                    "messages": [
                        tool_result,
                        human_message,
                    ]
                }
            )

        except (OSError, ValueError) as e:
            logger.warning(f"[MULTIMODAL] Error loading sandbox file {file_path}: {e}")
            return ToolMessage(
                content=f"ERROR: Failed to load file: {e}",
                tool_call_id=tool_call_id,
            )


__all__ = ["MultimodalMiddleware"]
