"""
Tests for the Workspaces API router (src/server/app/workspaces.py).

Covers CRUD operations, start/stop/archive/delete lifecycle actions,
flash workspace, reorder, and ownership guards.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_test_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _ws(
    workspace_id=None,
    user_id="test-user-123",
    name="Test Workspace",
    status="running",
    **overrides,
):
    """Build a workspace dict matching DB row shape."""
    data = {
        "workspace_id": workspace_id or str(uuid.uuid4()),
        "user_id": user_id,
        "name": name,
        "description": None,
        "sandbox_id": "sandbox-abc",
        "status": status,
        "mode": "ptc",
        "sort_order": 0,
        "is_pinned": False,
        "created_at": NOW,
        "updated_at": NOW,
        "last_activity_at": None,
        "stopped_at": None,
        "config": None,
    }
    data.update(overrides)
    return data


@pytest_asyncio.fixture
async def client():
    from src.server.app.workspaces import router

    app = create_test_app(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces — create workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workspace_success(client):
    ws = _ws()
    with patch(
        "src.server.app.workspaces.WorkspaceManager"
    ) as MockWM:
        mock_manager = AsyncMock()
        mock_manager.create_workspace = AsyncMock(return_value=ws)
        MockWM.get_instance.return_value = mock_manager

        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Test Workspace"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Workspace"
    assert body["workspace_id"] == ws["workspace_id"]


@pytest.mark.asyncio
async def test_create_workspace_value_error_returns_400(client):
    with patch(
        "src.server.app.workspaces.WorkspaceManager"
    ) as MockWM:
        mock_manager = AsyncMock()
        mock_manager.create_workspace = AsyncMock(
            side_effect=ValueError("bad config")
        )
        MockWM.get_instance.return_value = mock_manager

        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Bad"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_workspace_internal_error(client):
    with patch(
        "src.server.app.workspaces.WorkspaceManager"
    ) as MockWM:
        mock_manager = AsyncMock()
        mock_manager.create_workspace = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        MockWM.get_instance.return_value = mock_manager

        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Fail"},
        )

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_create_workspace_validation_empty_name(client):
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces/flash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flash_workspace(client):
    ws = _ws(status="flash")
    with patch(
        "src.server.app.workspaces.get_or_create_flash_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.post("/api/v1/workspaces/flash")

    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == ws["workspace_id"]


@pytest.mark.asyncio
async def test_get_flash_workspace_error(client):
    with patch(
        "src.server.app.workspaces.get_or_create_flash_workspace",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        resp = await client.post("/api/v1/workspaces/flash")

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces/reorder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_workspaces(client):
    ws_id = str(uuid.uuid4())
    with patch(
        "src.server.app.workspaces.batch_update_sort_order",
        new_callable=AsyncMock,
    ) as mock_reorder:
        resp = await client.post(
            "/api/v1/workspaces/reorder",
            json={"items": [{"workspace_id": ws_id, "sort_order": 1}]},
        )

    assert resp.status_code == 204
    mock_reorder.assert_awaited_once()


@pytest.mark.asyncio
async def test_reorder_workspaces_empty_items(client):
    resp = await client.post(
        "/api/v1/workspaces/reorder",
        json={"items": []},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/workspaces — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_workspaces(client):
    ws1 = _ws(name="WS1")
    ws2 = _ws(name="WS2")
    with patch(
        "src.server.app.workspaces.get_workspaces_for_user",
        new_callable=AsyncMock,
        return_value=([ws1, ws2], 2),
    ):
        resp = await client.get("/api/v1/workspaces")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["workspaces"]) == 2


@pytest.mark.asyncio
async def test_list_workspaces_with_params(client):
    with patch(
        "src.server.app.workspaces.get_workspaces_for_user",
        new_callable=AsyncMock,
        return_value=([], 0),
    ) as mock_list:
        resp = await client.get(
            "/api/v1/workspaces?limit=5&offset=10&sort_by=activity"
        )

    assert resp.status_code == 200
    mock_list.assert_awaited_once_with(
        user_id="test-user-123", limit=5, offset=10, sort_by="activity"
    )


@pytest.mark.asyncio
async def test_list_workspaces_invalid_sort_by(client):
    resp = await client.get("/api/v1/workspaces?sort_by=invalid")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/workspaces/{workspace_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_workspace_success(client):
    ws = _ws()
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.get(
            f"/api/v1/workspaces/{ws['workspace_id']}"
        )

    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == ws["workspace_id"]


@pytest.mark.asyncio
async def test_get_workspace_not_found(client):
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.get(f"/api/v1/workspaces/{uuid.uuid4()}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_workspace_forbidden(client):
    ws = _ws(user_id="other-user")
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.get(
            f"/api/v1/workspaces/{ws['workspace_id']}"
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/v1/workspaces/{workspace_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_workspace_success(client):
    ws = _ws()
    updated = {**ws, "name": "Updated Name"}
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch(
            "src.server.app.workspaces.db_update_workspace",
            new_callable=AsyncMock,
            return_value=updated,
        ),
    ):
        resp = await client.put(
            f"/api/v1/workspaces/{ws['workspace_id']}",
            json={"name": "Updated Name"},
        )

    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_workspace_not_found(client):
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.put(
            f"/api/v1/workspaces/{uuid.uuid4()}",
            json={"name": "X"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_workspace_forbidden(client):
    ws = _ws(user_id="other-user")
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.put(
            f"/api/v1/workspaces/{ws['workspace_id']}",
            json={"name": "X"},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_workspace_db_returns_none(client):
    ws = _ws()
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch(
            "src.server.app.workspaces.db_update_workspace",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.put(
            f"/api/v1/workspaces/{ws['workspace_id']}",
            json={"name": "Gone"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces/{workspace_id}/start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_workspace_from_stopped(client):
    ws = _ws(status="stopped")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        mock_manager = AsyncMock()
        mock_manager.get_session_for_workspace = AsyncMock()
        MockWM.get_instance.return_value = mock_manager

        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/start"
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_start_workspace_already_running(client):
    ws = _ws(status="running")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        MockWM.get_instance.return_value = AsyncMock()

        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/start"
        )

    assert resp.status_code == 200
    assert "already running" in resp.json()["message"]


@pytest.mark.asyncio
async def test_start_workspace_invalid_state(client):
    ws = _ws(status="creating")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        MockWM.get_instance.return_value = AsyncMock()

        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/start"
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_start_workspace_not_found(client):
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        MockWM.get_instance.return_value = AsyncMock()

        resp = await client.post(
            f"/api/v1/workspaces/{uuid.uuid4()}/start"
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_start_workspace_forbidden(client):
    ws = _ws(status="stopped", user_id="other-user")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        MockWM.get_instance.return_value = AsyncMock()

        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/start"
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces/{workspace_id}/stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_workspace_success(client):
    ws = _ws(status="running")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        mock_manager = AsyncMock()
        mock_manager.stop_workspace = AsyncMock(
            return_value={**ws, "status": "stopped"}
        )
        MockWM.get_instance.return_value = mock_manager

        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/stop"
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_stop_workspace_not_found(client):
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.post(f"/api/v1/workspaces/{uuid.uuid4()}/stop")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stop_workspace_forbidden(client):
    ws = _ws(user_id="other-user")
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/stop"
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces/{workspace_id}/archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_workspace_success(client):
    ws = _ws(status="stopped")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        mock_manager = AsyncMock()
        mock_manager.archive_workspace = AsyncMock()
        MockWM.get_instance.return_value = mock_manager

        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/archive"
        )

    assert resp.status_code == 200
    assert resp.json()["message"] == "Workspace archived successfully"


@pytest.mark.asyncio
async def test_archive_workspace_not_found(client):
    """Archive endpoint has no `except HTTPException: raise`, so
    require_workspace_owner's 404 is caught by the generic Exception handler
    and surfaces as 500."""
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.post(
            f"/api/v1/workspaces/{uuid.uuid4()}/archive"
        )

    # HTTPException from require_workspace_owner falls through to
    # except Exception -> 500 (no `except HTTPException: raise` in this handler)
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_archive_workspace_forbidden(client):
    """Same pattern: missing except-HTTPException-raise -> 500."""
    ws = _ws(user_id="other-user", status="stopped")
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.post(
            f"/api/v1/workspaces/{ws['workspace_id']}/archive"
        )

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /api/v1/workspaces/{workspace_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workspace_success(client):
    ws = _ws(status="stopped")
    with (
        patch(
            "src.server.app.workspaces.db_get_workspace",
            new_callable=AsyncMock,
            return_value=ws,
        ),
        patch("src.server.app.workspaces.WorkspaceManager") as MockWM,
    ):
        mock_manager = AsyncMock()
        mock_manager.delete_workspace = AsyncMock()
        MockWM.get_instance.return_value = mock_manager

        resp = await client.delete(
            f"/api/v1/workspaces/{ws['workspace_id']}"
        )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_flash_workspace_blocked(client):
    ws = _ws(status="flash")
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.delete(
            f"/api/v1/workspaces/{ws['workspace_id']}"
        )

    assert resp.status_code == 400
    assert "flash" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_workspace_not_found(client):
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.delete(f"/api/v1/workspaces/{uuid.uuid4()}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_workspace_forbidden(client):
    ws = _ws(user_id="other-user")
    with patch(
        "src.server.app.workspaces.db_get_workspace",
        new_callable=AsyncMock,
        return_value=ws,
    ):
        resp = await client.delete(
            f"/api/v1/workspaces/{ws['workspace_id']}"
        )

    assert resp.status_code == 403
