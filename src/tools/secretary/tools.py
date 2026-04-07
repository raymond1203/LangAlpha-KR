"""
Secretary tools: workspace management, PTC dispatch, agent monitoring, thread management.

These tools use interrupt() to pause the graph and wait for user approval
via the frontend, following the same HITL pattern as onboarding tools.
"""

import json
import logging
import os
import uuid
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import Command, interrupt

try:
    from langchain.tools import InjectedToolCallId
except ImportError:
    from langchain_core.tools import InjectedToolCallId

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared HITL helper
# ---------------------------------------------------------------------------


def _hitl_confirm(
    action_type: str, payload: dict[str, Any]
) -> tuple[bool, dict]:
    """Pause the graph for user confirmation via interrupt().

    Args:
        action_type: The action type string (e.g. "create_workspace")
        payload: Additional data to include in the action request

    Returns:
        Tuple of (approved, response_dict)
    """
    response = interrupt(
        {"action_requests": [{"type": action_type, **payload}]}
    )

    approved = False
    if isinstance(response, dict):
        decisions = response.get("decisions", [])
        if decisions and decisions[0].get("type") == "approve":
            approved = True

    return approved, response if isinstance(response, dict) else {}


def _decline_command(message: str, tool_call_id: str) -> Command:
    """Return a Command for a declined HITL action."""
    return Command(
        update={
            "messages": [
                ToolMessage(content=message, tool_call_id=tool_call_id),
            ],
        }
    )


def _success_command(data: dict[str, Any], tool_call_id: str) -> Command:
    """Return a Command with JSON-serialized success data."""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(data), tool_call_id=tool_call_id
                ),
            ],
        }
    )


def _error_command(error: str, tool_call_id: str) -> Command:
    """Return a Command with a JSON error response."""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps({"success": False, "error": error}),
                    tool_call_id=tool_call_id,
                ),
            ],
        }
    )


# ---------------------------------------------------------------------------
# Tool 1: manage_workspaces
# ---------------------------------------------------------------------------


@tool("manage_workspaces")
async def manage_workspaces(
    action: str,
    config: RunnableConfig,
    name: str | None = None,
    description: str | None = None,
    workspace_id: str | None = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Manage user workspaces: list, create, delete, or stop.

    Args:
        action: One of "list", "create", "delete", "stop"
        name: Workspace name (required for "create")
        description: Workspace description (optional, for "create")
        workspace_id: Workspace ID (required for "delete" and "stop")
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return _error_command("user_id not found in config", tool_call_id)

    if action == "list":
        return await _workspaces_list(user_id, tool_call_id)
    elif action == "create":
        return await _workspaces_create(
            user_id, name, description, tool_call_id
        )
    elif action == "delete":
        return await _workspaces_delete(user_id, workspace_id, tool_call_id)
    elif action == "stop":
        return await _workspaces_stop(user_id, workspace_id, tool_call_id)
    else:
        return _error_command(
            f"Unknown action: {action}. Use list, create, delete, or stop.",
            tool_call_id,
        )


async def _workspaces_list(user_id: str, tool_call_id: str) -> Command:
    """List workspaces for the user."""
    try:
        from src.server.database.workspace import get_workspaces_for_user

        workspaces, total = await get_workspaces_for_user(
            user_id=user_id, limit=20
        )
        content = json.dumps(
            {"success": True, "workspaces": workspaces, "total": total},
            default=str,
        )
    except Exception as e:
        logger.error(f"Failed to list workspaces: {e}")
        content = json.dumps({"success": False, "error": str(e)})

    return Command(
        update={
            "messages": [
                ToolMessage(content=content, tool_call_id=tool_call_id),
            ],
        }
    )


async def _workspaces_create(
    user_id: str,
    name: str | None,
    description: str | None,
    tool_call_id: str,
) -> Command:
    """Create a new workspace with HITL confirmation."""
    if not name:
        return _error_command(
            "name is required for create action", tool_call_id
        )

    approved, _ = _hitl_confirm(
        "create_workspace",
        {"workspace_name": name, "workspace_description": description or ""},
    )

    if not approved:
        return _decline_command(
            "User declined workspace creation.", tool_call_id
        )

    try:
        from src.server.services.workspace_manager import WorkspaceManager

        workspace_manager = WorkspaceManager.get_instance()
        workspace = await workspace_manager.create_workspace(
            user_id=user_id,
            name=name,
            description=description,
        )

        workspace_id = str(workspace["workspace_id"])
        return _success_command(
            {
                "success": True,
                "workspace_id": workspace_id,
                "workspace_name": name,
            },
            tool_call_id,
        )
    except Exception as e:
        logger.error(f"Failed to create workspace: {e}")
        return _error_command(str(e), tool_call_id)


async def _workspaces_delete(
    user_id: str, workspace_id: str | None, tool_call_id: str
) -> Command:
    """Delete a workspace with HITL confirmation."""
    if not workspace_id:
        return _error_command(
            "workspace_id is required for delete action", tool_call_id
        )

    # Verify ownership
    from src.server.database.workspace import get_workspace

    ws = await get_workspace(workspace_id)
    if not ws or str(ws.get("user_id")) != user_id:
        return _error_command("workspace not found", tool_call_id)

    approved, _ = _hitl_confirm(
        "delete_workspace",
        {"workspace_id": workspace_id},
    )

    if not approved:
        return _decline_command(
            "User declined workspace deletion.", tool_call_id
        )

    try:
        from src.server.services.workspace_manager import WorkspaceManager

        workspace_manager = WorkspaceManager.get_instance()
        await workspace_manager.delete_workspace(workspace_id)
        return _success_command(
            {"success": True, "workspace_id": workspace_id},
            tool_call_id,
        )
    except Exception as e:
        logger.error(f"Failed to delete workspace: {e}")
        return _error_command(str(e), tool_call_id)


async def _workspaces_stop(
    user_id: str, workspace_id: str | None, tool_call_id: str
) -> Command:
    """Stop a workspace with HITL confirmation."""
    if not workspace_id:
        return _error_command(
            "workspace_id is required for stop action", tool_call_id
        )

    # Verify ownership
    from src.server.database.workspace import get_workspace

    ws = await get_workspace(workspace_id)
    if not ws or str(ws.get("user_id")) != user_id:
        return _error_command("workspace not found", tool_call_id)

    approved, _ = _hitl_confirm(
        "stop_workspace",
        {"workspace_id": workspace_id},
    )

    if not approved:
        return _decline_command(
            "User declined workspace stop.", tool_call_id
        )

    try:
        from src.server.services.workspace_manager import WorkspaceManager

        workspace_manager = WorkspaceManager.get_instance()
        await workspace_manager.stop_workspace(workspace_id)
        return _success_command(
            {"success": True, "workspace_id": workspace_id},
            tool_call_id,
        )
    except Exception as e:
        logger.error(f"Failed to stop workspace: {e}")
        return _error_command(str(e), tool_call_id)


# ---------------------------------------------------------------------------
# Tool 2: ptc_agent
# ---------------------------------------------------------------------------


@tool("ptc_agent")
async def ptc_agent(
    question: str,
    config: RunnableConfig,
    workspace_id: str | None = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Dispatch a research question to a PTC agent in a workspace.

    Creates a new workspace if workspace_id is not provided, generates a
    thread, and dispatches the question via an internal HTTP call. The PTC
    agent runs asynchronously — use agent_output to retrieve results later.

    Args:
        question: The research question to send to the PTC agent
        workspace_id: Optional workspace ID to use. If None, a new workspace is created.
    """
    import aiohttp

    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return _error_command("user_id not found in config", tool_call_id)

    # Generate workspace name from question if no workspace_id
    workspace_name = question[:50].strip() if not workspace_id else None

    approved, _ = _hitl_confirm(
        "ptc_agent",
        {
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "question": question,
        },
    )

    if not approved:
        return _decline_command(
            "User declined PTC agent dispatch.", tool_call_id
        )

    # Create workspace or verify ownership
    if workspace_id is None:
        try:
            from src.server.services.workspace_manager import WorkspaceManager

            workspace_manager = WorkspaceManager.get_instance()
            workspace = await workspace_manager.create_workspace(
                user_id=user_id,
                name=workspace_name or "Research",
                description=f"Auto-created for: {question[:100]}",
            )
            workspace_id = str(workspace["workspace_id"])
        except Exception as e:
            logger.error(f"Failed to create workspace for PTC dispatch: {e}")
            return _error_command(f"workspace_creation_failed: {e}", tool_call_id)
    else:
        # Verify user owns the target workspace
        from src.server.database.workspace import get_workspace

        ws = await get_workspace(workspace_id)
        if not ws or str(ws.get("user_id")) != user_id:
            return _error_command("workspace not found", tool_call_id)

    # Generate a new thread ID
    thread_id = str(uuid.uuid4())

    # Dispatch via internal HTTP call
    self_base_url = os.environ.get("GINLIXFLOW_BASE_URL", "http://localhost:8000")
    service_token = os.environ.get("INTERNAL_SERVICE_TOKEN", "")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self_base_url}/api/v1/threads/{thread_id}/messages",
                json={
                    "messages": [{"role": "user", "content": question}],
                    "agent_mode": "ptc",
                    "workspace_id": workspace_id,
                },
                headers={
                    "X-Service-Token": service_token,
                    "X-User-Id": user_id,
                },
                timeout=aiohttp.ClientTimeout(connect=10, sock_read=5),
            ) as resp:
                # Only check status — don't read body (it's an SSE stream)
                if resp.status >= 400:
                    return _error_command(
                        f"dispatch_failed: HTTP {resp.status}",
                        tool_call_id,
                    )
    except aiohttp.ClientError as e:
        logger.error(f"PTC dispatch HTTP error: {e}")
        return _error_command("dispatch_failed", tool_call_id)
    except TimeoutError:
        logger.error("PTC dispatch timed out")
        return _error_command("dispatch_timeout", tool_call_id)

    return _success_command(
        {
            "success": True,
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "status": "dispatched",
        },
        tool_call_id,
    )


# ---------------------------------------------------------------------------
# Tool 3: agent_output
# ---------------------------------------------------------------------------


@tool("agent_output")
async def agent_output(
    thread_id: str,
    config: RunnableConfig,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Retrieve the text output of a running or completed PTC agent thread.

    Use this to check on the progress or results of a dispatched PTC agent.

    Args:
        thread_id: The thread ID to retrieve output from
    """
    from src.server.database.conversation import get_thread_owner_id

    from src.tools.secretary.utils import extract_text_from_thread

    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return _error_command("user_id not found in config", tool_call_id)

    # Verify ownership
    try:
        owner_id = await get_thread_owner_id(thread_id)
        if owner_id != user_id:
            return _error_command(
                "thread not found or not owned by user", tool_call_id
            )
    except Exception as e:
        logger.error(f"Failed to verify thread ownership: {e}")
        return _error_command(f"ownership_check_failed: {e}", tool_call_id)

    # Extract text
    try:
        result = await extract_text_from_thread(thread_id)
    except Exception as e:
        logger.error(f"Failed to extract text from thread {thread_id}: {e}")
        return _error_command(str(e), tool_call_id)

    return _success_command(result, tool_call_id)


# ---------------------------------------------------------------------------
# Tool 4: manage_threads
# ---------------------------------------------------------------------------


@tool("manage_threads")
async def manage_threads(
    action: str,
    config: RunnableConfig,
    workspace_id: str | None = None,
    thread_id: str | None = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Manage conversation threads: list, get output, or delete.

    Args:
        action: One of "list", "get_output", "delete"
        workspace_id: Optional workspace ID to filter threads (for "list")
        thread_id: Thread ID (required for "get_output" and "delete")
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return _error_command("user_id not found in config", tool_call_id)

    if action == "list":
        return await _threads_list(user_id, workspace_id, tool_call_id)
    elif action == "get_output":
        return await _threads_get_output(user_id, thread_id, tool_call_id)
    elif action == "delete":
        return await _threads_delete(user_id, thread_id, tool_call_id)
    else:
        return _error_command(
            f"Unknown action: {action}. Use list, get_output, or delete.",
            tool_call_id,
        )


async def _threads_list(
    user_id: str, workspace_id: str | None, tool_call_id: str
) -> Command:
    """List threads, optionally filtered by workspace."""
    try:
        if workspace_id:
            # Verify user owns the workspace before listing its threads
            from src.server.database.workspace import get_workspace

            ws = await get_workspace(workspace_id)
            if not ws or str(ws.get("user_id")) != user_id:
                return _error_command("workspace not found", tool_call_id)

            from src.server.database.conversation import get_workspace_threads

            threads, total = await get_workspace_threads(
                workspace_id=workspace_id, limit=20
            )
        else:
            from src.server.database.conversation import get_threads_for_user

            threads, total = await get_threads_for_user(
                user_id=user_id, limit=20
            )

        content = json.dumps(
            {"success": True, "threads": threads, "total": total},
            default=str,
        )
    except Exception as e:
        logger.error(f"Failed to list threads: {e}")
        content = json.dumps({"success": False, "error": str(e)})

    return Command(
        update={
            "messages": [
                ToolMessage(content=content, tool_call_id=tool_call_id),
            ],
        }
    )


async def _threads_get_output(
    user_id: str, thread_id: str | None, tool_call_id: str
) -> Command:
    """Get output from a specific thread."""
    if not thread_id:
        return _error_command(
            "thread_id is required for get_output action", tool_call_id
        )

    from src.server.database.conversation import get_thread_owner_id

    from src.tools.secretary.utils import extract_text_from_thread

    # Verify ownership
    try:
        owner_id = await get_thread_owner_id(thread_id)
        if owner_id != user_id:
            return _error_command(
                "thread not found or not owned by user", tool_call_id
            )
    except Exception as e:
        logger.error(f"Failed to verify thread ownership: {e}")
        return _error_command(f"ownership_check_failed: {e}", tool_call_id)

    try:
        result = await extract_text_from_thread(thread_id)
    except Exception as e:
        logger.error(f"Failed to get thread output: {e}")
        return _error_command(str(e), tool_call_id)

    return _success_command(result, tool_call_id)


async def _threads_delete(
    user_id: str, thread_id: str | None, tool_call_id: str
) -> Command:
    """Delete a thread with HITL confirmation."""
    if not thread_id:
        return _error_command(
            "thread_id is required for delete action", tool_call_id
        )

    from src.server.database.conversation import get_thread_owner_id

    # Verify ownership before even asking for confirmation
    try:
        owner_id = await get_thread_owner_id(thread_id)
        if owner_id != user_id:
            return _error_command(
                "thread not found or not owned by user", tool_call_id
            )
    except Exception as e:
        logger.error(f"Failed to verify thread ownership: {e}")
        return _error_command(f"ownership_check_failed: {e}", tool_call_id)

    approved, _ = _hitl_confirm(
        "delete_thread",
        {"thread_id": thread_id},
    )

    if not approved:
        return _decline_command(
            "User declined thread deletion.", tool_call_id
        )

    try:
        from src.server.database.conversation import delete_thread

        await delete_thread(thread_id)
        return _success_command(
            {"success": True, "thread_id": thread_id},
            tool_call_id,
        )
    except Exception as e:
        logger.error(f"Failed to delete thread: {e}")
        return _error_command(str(e), tool_call_id)
