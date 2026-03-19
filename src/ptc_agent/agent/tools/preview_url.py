"""Get preview URLs for services running in the sandbox."""

from typing import Any

import structlog
from langchain_core.tools import BaseTool, tool

logger = structlog.get_logger(__name__)


def create_preview_url_tool(sandbox: Any) -> BaseTool:
    """Factory function to create GetPreviewUrl tool with injected dependencies.

    Args:
        sandbox: PTCSandbox instance for preview URL generation

    Returns:
        Configured GetPreviewUrl tool function
    """

    @tool(response_format="content_and_artifact")
    async def GetPreviewUrl(
        port: int,
        command: str,
        title: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Get a preview URL for a service running on the given port in the sandbox.

        This tool starts the given command in the background AND generates a preview URL.
        Always provide the command used to start the server — it will be persisted so the
        server can be restarted automatically when the user reopens the preview later.

        Args:
            port: Port number (3000-9999) the service is listening on
            command: The shell command to start the server (e.g. "python -m http.server 8080")
            title: Optional display title for the preview (default: "Port {port}")

        Returns:
            The signed preview URL that can be used to access the service
        """
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        except Exception:
            writer = None

        try:
            preview_info = await sandbox.start_and_get_preview_url(command, port)
            url = preview_info.url
            display_title = title or f"Port {port}"

            logger.info(
                "Generated preview URL",
                port=port,
                title=display_title,
                url=url[:80],
            )

            artifact = {
                "type": "preview_url",
                "url": url,
                "port": port,
                "title": display_title,
                "command": command,
            }

            # Emit SSE artifact so the frontend auto-opens the preview panel
            if writer:
                writer({
                    "artifact_type": "preview_url",
                    "artifact_id": f"preview_{port}",
                    "payload": artifact,
                })

            content = f"Preview URL for {display_title}: {url}"
            return content, artifact

        except NotImplementedError:
            return (
                "ERROR: Preview URLs are not supported by the current sandbox provider",
                {},
            )
        except Exception as e:
            error_msg = f"Failed to generate preview URL for port {port}: {e!s}"
            logger.error(error_msg, port=port, error=str(e), exc_info=True)
            return f"ERROR: {error_msg}", {}

    return GetPreviewUrl
