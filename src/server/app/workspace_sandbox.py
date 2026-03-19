"""Workspace Sandbox API Router.

Provides sandbox resource stats, disk usage, installed packages,
and package installation for a workspace's Daytona sandbox.

Endpoints:
- GET    /api/v1/workspaces/{workspace_id}/sandbox/stats
- POST   /api/v1/workspaces/{workspace_id}/sandbox/packages
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import time
from typing import Any

import httpx

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.server.utils.api import CurrentUserId, require_workspace_owner
from src.server.database.workspace import get_workspace as db_get_workspace
from src.server.services.workspace_manager import WorkspaceManager
from src.ptc_agent.core.sandbox import PTCSandbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workspaces", tags=["Workspace Sandbox"])

# Regex for validating package names (allows version specifiers)
_PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+([<>=!~]+.*)?$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_sandbox(workspace_id: str, user_id: str) -> Any:
    """Validate workspace ownership, reject flash workspaces, and return the sandbox."""
    workspace = await db_get_workspace(workspace_id)
    require_workspace_owner(workspace, user_id=user_id)

    if workspace.get("status") == "flash":
        raise HTTPException(
            status_code=400, detail="Flash workspaces do not have a sandbox"
        )

    manager = WorkspaceManager.get_instance()
    try:
        session = await manager.get_session_for_workspace(workspace_id, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Sandbox not ready: {e}") from None

    sandbox = getattr(session, "sandbox", None)
    if sandbox is None:
        raise HTTPException(status_code=503, detail="Sandbox not available")

    return session, sandbox


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SandboxResources(BaseModel):
    cpu: float | None = None
    memory: float | None = None  # GiB
    disk: float | None = None  # GiB
    gpu: float | None = None


class DiskOverview(BaseModel):
    total: str  # e.g. "20G"
    used: str  # e.g. "3.2G"
    available: str  # e.g. "16.8G"
    use_percent: str  # e.g. "16%"


class DirectorySize(BaseModel):
    path: str  # e.g. "results/"
    size: str  # e.g. "1.2G"


class InstalledPackage(BaseModel):
    name: str
    version: str


class SkillInfo(BaseModel):
    name: str
    description: str | None = None


class SandboxStatsResponse(BaseModel):
    workspace_id: str
    sandbox_id: str | None = None
    state: str | None = None
    created_at: str | None = None
    auto_stop_interval: int | None = None
    resources: SandboxResources
    disk_usage: DiskOverview | None = None
    directory_breakdown: list[DirectorySize] = Field(default_factory=list)
    packages: list[InstalledPackage] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    skills: list[SkillInfo] = Field(default_factory=list)
    default_packages: list[str] = Field(default_factory=list)


class PackageInstallRequest(BaseModel):
    packages: list[str] = Field(..., min_length=1, max_length=50)


class PackageInstallResponse(BaseModel):
    success: bool
    installed: list[str]
    output: str
    error: str | None = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_df_output(stdout: str) -> DiskOverview | None:
    """Parse `df -h /home/workspace` output into a DiskOverview."""
    lines = stdout.strip().splitlines()
    if len(lines) < 2:
        return None
    # Header: Filesystem  Size  Used  Avail  Use%  Mounted on
    parts = lines[1].split()
    if len(parts) < 5:
        return None
    return DiskOverview(
        total=parts[1],
        used=parts[2],
        available=parts[3],
        use_percent=parts[4],
    )


def _parse_du_output(stdout: str, work_dir: str = "/home/workspace") -> list[DirectorySize]:
    """Parse `du -sh <work_dir>/*/` output into directory sizes."""
    work_dir_prefix = work_dir.rstrip("/") + "/"
    results: list[DirectorySize] = []
    for line in stdout.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        size, path = parts
        # Convert absolute path to relative display name
        stripped = path.rstrip("/")
        if stripped.startswith(work_dir_prefix):
            name = stripped[len(work_dir_prefix):]
        else:
            name = stripped.split(work_dir_prefix)[-1]
        if name:
            results.append(DirectorySize(path=name, size=size))
    return results


def _parse_pip_list(stdout: str) -> list[InstalledPackage]:
    """Parse `uv pip list --format json` output."""
    try:
        data = json.loads(stdout)
        return [InstalledPackage(name=p["name"], version=p["version"]) for p in data]
    except (json.JSONDecodeError, KeyError):
        return []


def _parse_skills_frontmatter(stdout: str) -> list[SkillInfo]:
    """Parse concatenated SKILL.md frontmatter blocks.

    Expected input format (one block per skill):
        === skill_dir_name ===
        ---
        name: foo
        description: bar
        ---
    """
    skills: list[SkillInfo] = []
    current_name: str | None = None
    current_desc: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("=== ") and line.endswith(" ==="):
            # Flush previous skill
            if current_name:
                skills.append(SkillInfo(name=current_name, description=current_desc))
            current_name = line[4:-4].strip()
            current_desc = None
        elif line.startswith("name:"):
            current_name = line[5:].strip()
        elif line.startswith("description:"):
            current_desc = line[12:].strip()

    # Flush last
    if current_name:
        skills.append(SkillInfo(name=current_name, description=current_desc))

    return skills


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{workspace_id}/sandbox/stats")
async def get_sandbox_stats(
    workspace_id: str,
    x_user_id: CurrentUserId,
) -> SandboxStatsResponse:
    """Get sandbox resource stats, disk usage, installed packages, and MCP servers.

    For running workspaces: returns full stats including disk, packages, MCP, skills.
    For stopped/archived workspaces: returns metadata only (state, resources, intervals)
    from Daytona API without starting the sandbox.
    """
    workspace = await db_get_workspace(workspace_id)
    require_workspace_owner(workspace, user_id=x_user_id)

    if workspace.get("status") == "flash":
        raise HTTPException(
            status_code=400, detail="Flash workspaces do not have a sandbox"
        )

    if workspace.get("status") == "running":
        return await _get_full_sandbox_stats(workspace_id, x_user_id, workspace)
    else:
        return await _get_offline_sandbox_stats(workspace_id, workspace)


async def _get_offline_sandbox_stats(
    workspace_id: str,
    workspace: dict[str, Any],
) -> SandboxStatsResponse:
    """Get sandbox metadata for stopped/archived workspaces via Daytona API (no start)."""
    sandbox_id = workspace.get("sandbox_id")
    if not sandbox_id:
        return SandboxStatsResponse(
            workspace_id=workspace_id,
            state=workspace.get("status", "unknown"),
            created_at=str(workspace.get("created_at", "")),
            resources=SandboxResources(),
        )

    from ptc_agent.core.sandbox.providers import create_provider

    manager = WorkspaceManager.get_instance()
    provider = None
    try:
        provider = create_provider(manager.config.to_core_config())
        runtime = await provider.get(sandbox_id)
        meta = await runtime.get_metadata()
        return SandboxStatsResponse(
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            state=meta.get("state"),
            created_at=str(meta["created_at"]) if meta.get("created_at") else None,
            auto_stop_interval=meta.get("auto_stop_interval"),
            resources=SandboxResources(
                cpu=meta.get("cpu"),
                memory=meta.get("memory"),
                disk=meta.get("disk"),
                gpu=meta.get("gpu"),
            ),
        )
    except Exception as e:
        logger.warning(f"Failed to query sandbox provider for {sandbox_id}: {e}")
        return SandboxStatsResponse(
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            state=workspace.get("status", "unknown"),
            created_at=str(workspace.get("created_at", "")),
            resources=SandboxResources(),
        )
    finally:
        if provider is not None:
            await provider.close()


async def _get_full_sandbox_stats(
    workspace_id: str,
    x_user_id: str,
    workspace: dict[str, Any],
) -> SandboxStatsResponse:
    """Get full sandbox stats for running workspaces (disk, packages, MCP, skills)."""
    session, sandbox = await _get_sandbox(workspace_id, x_user_id)

    # --- 1. Static properties from the runtime metadata ---
    resources = SandboxResources()
    state = None
    created_at = None
    auto_stop_interval = None
    sandbox_id = getattr(sandbox, "sandbox_id", None)

    runtime = getattr(sandbox, "runtime", None)
    if runtime is not None:
        try:
            meta = await runtime.get_metadata()
            resources = SandboxResources(
                cpu=meta.get("cpu"),
                memory=meta.get("memory"),
                disk=meta.get("disk"),
                gpu=meta.get("gpu"),
            )
            state = meta.get("state")
            raw_created = meta.get("created_at")
            if raw_created is not None:
                created_at = str(raw_created)
            auto_stop_interval = meta.get("auto_stop_interval")
        except Exception:
            pass

    # --- 2. Concurrent bash commands for disk & packages ---
    work_dir = sandbox.working_dir

    async def _get_disk_usage():
        try:
            result = await sandbox.execute_bash_command(
                f"df -h {work_dir}", timeout=10
            )
            if result.get("success"):
                return _parse_df_output(result.get("stdout", ""))
        except Exception as e:
            logger.debug(f"df command failed: {e}")
        return None

    async def _get_directory_breakdown():
        try:
            result = await sandbox.execute_bash_command(
                f"du -sh {work_dir}/*/ 2>/dev/null || true", timeout=15
            )
            if result.get("success"):
                return _parse_du_output(result.get("stdout", ""), work_dir)
        except Exception as e:
            logger.debug(f"du command failed: {e}")
        return []

    async def _get_packages():
        try:
            result = await sandbox.execute_bash_command(
                "uv pip list --format json 2>/dev/null || pip list --format json 2>/dev/null || echo '[]'",
                timeout=15,
            )
            if result.get("success"):
                return _parse_pip_list(result.get("stdout", ""))
        except Exception as e:
            logger.debug(f"pip list failed: {e}")
        return []

    async def _get_skills():
        try:
            # Read SKILL.md frontmatter from each skill directory
            cmd = (
                f"for d in {work_dir}/skills/*/; do "
                '  [ -f "$d/SKILL.md" ] && echo "=== $(basename "$d") ===" && head -5 "$d/SKILL.md"; '
                "done 2>/dev/null || true"
            )
            result = await sandbox.execute_bash_command(cmd, timeout=10)
            if result.get("success"):
                return _parse_skills_frontmatter(result.get("stdout", ""))
        except Exception as e:
            logger.debug(f"skills listing failed: {e}")
        return []

    disk_usage, directory_breakdown, packages, skills = await asyncio.gather(
        _get_disk_usage(),
        _get_directory_breakdown(),
        _get_packages(),
        _get_skills(),
    )

    # --- 3. MCP servers ---
    mcp_servers: list[str] = []
    try:
        registry = getattr(session, "mcp_registry", None)
        if registry is not None:
            mcp_servers = list(registry.connectors.keys())
    except Exception:
        pass

    default_packages = list(PTCSandbox.DEFAULT_DEPENDENCIES)

    return SandboxStatsResponse(
        workspace_id=workspace_id,
        sandbox_id=sandbox_id,
        state=state,
        created_at=created_at,
        auto_stop_interval=auto_stop_interval,
        resources=resources,
        disk_usage=disk_usage,
        directory_breakdown=directory_breakdown,
        packages=packages,
        mcp_servers=mcp_servers,
        skills=skills,
        default_packages=default_packages,
    )


@router.post("/{workspace_id}/sandbox/packages")
async def install_sandbox_packages(
    workspace_id: str,
    x_user_id: CurrentUserId,
    body: PackageInstallRequest,
) -> PackageInstallResponse:
    """Install pip packages in the workspace sandbox."""

    # Validate package names before touching the sandbox
    for pkg in body.packages:
        if not _PACKAGE_NAME_RE.match(pkg):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid package name: {pkg}",
            )

    _session, sandbox = await _get_sandbox(workspace_id, x_user_id)

    quoted = " ".join(shlex.quote(p) for p in body.packages)
    cmd = f"uv pip install {quoted}"

    try:
        result = await sandbox.execute_bash_command(cmd, timeout=120)
        success = result.get("success", False)

        return PackageInstallResponse(
            success=success,
            installed=body.packages if success else [],
            output=result.get("stdout", ""),
            error=result.get("stderr", "") if not success else None,
        )
    except Exception as e:
        logger.exception("Package install failed for workspace %s", workspace_id)
        return PackageInstallResponse(
            success=False,
            installed=[],
            output="",
            error=str(e),
        )


class PreviewUrlRequest(BaseModel):
    port: int = Field(..., ge=3000, le=9999)
    command: str | None = None
    expires_in: int = Field(default=3600, ge=60, le=86400)


class PreviewUrlResponse(BaseModel):
    url: str
    port: int
    expires_in: int


@router.post("/{workspace_id}/sandbox/preview-url")
async def get_sandbox_preview_url(
    workspace_id: str,
    x_user_id: CurrentUserId,
    body: PreviewUrlRequest,
) -> PreviewUrlResponse:
    """Get a signed preview URL for a service running in the workspace sandbox.

    If command is provided, starts the server process in background before generating the URL.
    """
    _session, sandbox = await _get_sandbox(workspace_id, x_user_id)

    try:
        if body.command:
            preview_info = await sandbox.start_and_get_preview_url(
                body.command, body.port, expires_in=body.expires_in,
            )
        else:
            preview_info = await sandbox.get_preview_url(body.port, expires_in=body.expires_in)
        return PreviewUrlResponse(
            url=preview_info.url,
            port=body.port,
            expires_in=body.expires_in,
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Preview URLs are not supported by the current sandbox provider",
        ) from None
    except Exception as e:
        logger.exception(
            "Failed to get preview URL for workspace %s port %d",
            workspace_id,
            body.port,
        )
        raise HTTPException(status_code=500, detail=str(e)) from None


class PreviewHealthRequest(BaseModel):
    url: str


class PreviewHealthResponse(BaseModel):
    reachable: bool
    checked_at: int


@router.post("/{workspace_id}/sandbox/preview-health")
async def check_preview_health(
    workspace_id: str,
    x_user_id: CurrentUserId,
    body: PreviewHealthRequest,
) -> PreviewHealthResponse:
    """Check if a preview URL is still reachable."""
    from urllib.parse import urlparse

    _session, sandbox = await _get_sandbox(workspace_id, x_user_id)

    # Validate URL scheme and domain
    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL scheme")

    proxy_host = getattr(sandbox, "proxy_domain", None)
    if not proxy_host or "." not in proxy_host:
        raise HTTPException(status_code=501, detail="Preview health checks not supported")

    # Extract shared domain suffix (toolbox: {sandbox_id}.{domain}, preview: {port}-{token}.{domain})
    proxy_suffix = proxy_host.split(".", 1)[1]
    if not parsed.hostname or not parsed.hostname.endswith(proxy_suffix):
        raise HTTPException(status_code=400, detail="Invalid preview URL for this workspace")

    checked_at = int(time.time())
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.head(body.url, follow_redirects=False)
            return PreviewHealthResponse(
                reachable=200 <= resp.status_code < 400,
                checked_at=checked_at,
            )
    except Exception:
        return PreviewHealthResponse(reachable=False, checked_at=checked_at)
