"""Unit tests for secret redaction in public share endpoints.

Tests the read_shared_file and download_shared_file endpoints with
mocked DB and patched SecretRedactor.

Note: public.py lazily imports db_get_workspace, FilePersistenceService,
and WorkspaceManager inside each handler. We patch at source module level
for those. Top-level imports (_normalize_requested_path, get_redactor)
are patched in the public module namespace.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_test_app
from src.server.utils.secret_redactor import SecretRedactor

pytestmark = pytest.mark.asyncio

# Test data
_SECRET_NAME = "FMP_API_KEY"
_SECRET_VALUE = "sk_test_fmp_1234567890abcdef"
_SHARE_TOKEN = "share_abc123"
_WORKSPACE_ID = "ws-test-001"
_THREAD_ID = "thread-001"

# Patch targets
_THREAD_BY_TOKEN = "src.server.app.public.get_thread_by_share_token"
_DB_GET_WS = "src.server.database.workspace.get_workspace"
_FILE_SVC = "src.server.services.persistence.file.FilePersistenceService.get_file_content"
_NORM_PATH = "src.server.app.public._normalize_requested_path"
_GET_REDACTOR = "src.server.app.public.get_redactor"


def _make_thread(**overrides):
    thread = {
        "conversation_thread_id": _THREAD_ID,
        "workspace_id": _WORKSPACE_ID,
        "share_permissions": {"allow_files": True, "allow_download": True},
        "title": "Test Thread",
        "msg_type": "ptc",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "workspace_name": "Test Workspace",
    }
    thread.update(overrides)
    return thread


def _make_workspace(**overrides):
    ws = {
        "id": _WORKSPACE_ID,
        "user_id": "test-user-123",
        "workspace_id": _WORKSPACE_ID,
        "status": "running",
        "sandbox_id": "sb-123",
    }
    ws.update(overrides)
    return ws


def _make_file_record(content_text, **overrides):
    rec = {
        "content_text": content_text,
        "is_binary": False,
        "mime_type": "text/plain",
        "file_name": "test.txt",
    }
    rec.update(overrides)
    return rec


@pytest.fixture
def mock_redactor():
    r = SecretRedactor.__new__(SecretRedactor)
    r._secrets = [(_SECRET_NAME, _SECRET_VALUE)]
    return r


@pytest_asyncio.fixture
async def public_client():
    """httpx client wired to public router."""
    from src.server.app.public import router

    app = create_test_app(router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# TestReadSharedFileRedaction
# ---------------------------------------------------------------------------


class TestReadSharedFileRedaction:
    """Verify read_shared_file redacts secrets from DB file records."""

    async def test_read_redacts_secret_from_db(self, public_client, mock_redactor):
        content = f"API_KEY={_SECRET_VALUE}\nother=safe"

        with (
            patch(_THREAD_BY_TOKEN, AsyncMock(return_value=_make_thread())),
            patch(_DB_GET_WS, AsyncMock(return_value=_make_workspace())),
            patch(_NORM_PATH, return_value="data/test.txt"),
            patch(_FILE_SVC, AsyncMock(return_value=_make_file_record(content))),
            patch(_GET_REDACTOR, return_value=mock_redactor),
        ):
            resp = await public_client.get(
                f"/api/v1/public/shared/{_SHARE_TOKEN}/files/read",
                params={"path": "data/test.txt"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert _SECRET_VALUE not in body["content"]
        assert f"[REDACTED:{_SECRET_NAME}]" in body["content"]
        assert "other=safe" in body["content"]

    async def test_read_no_redaction_when_clean(self, public_client):
        """Content without secrets passes through unchanged."""
        content = "clean data only"
        empty_redactor = SecretRedactor.__new__(SecretRedactor)
        empty_redactor._secrets = []

        with (
            patch(_THREAD_BY_TOKEN, AsyncMock(return_value=_make_thread())),
            patch(_DB_GET_WS, AsyncMock(return_value=_make_workspace())),
            patch(_NORM_PATH, return_value="data/clean.txt"),
            patch(_FILE_SVC, AsyncMock(return_value=_make_file_record(content))),
            patch(_GET_REDACTOR, return_value=empty_redactor),
        ):
            resp = await public_client.get(
                f"/api/v1/public/shared/{_SHARE_TOKEN}/files/read",
                params={"path": "data/clean.txt"},
            )

        assert resp.status_code == 200
        assert resp.json()["content"] == content


# ---------------------------------------------------------------------------
# TestDownloadSharedFileRedaction
# ---------------------------------------------------------------------------


class TestDownloadSharedFileRedaction:
    """Verify download_shared_file redacts secrets from text files."""

    async def test_download_redacts_secret_from_text(self, public_client, mock_redactor):
        text = f"key={_SECRET_VALUE}"
        file_record = _make_file_record(
            content_text=text,
            mime_type="text/plain",
            file_name="config.txt",
        )

        with (
            patch(_THREAD_BY_TOKEN, AsyncMock(return_value=_make_thread())),
            patch(_DB_GET_WS, AsyncMock(return_value=_make_workspace())),
            patch(_NORM_PATH, return_value="config.txt"),
            patch(_FILE_SVC, AsyncMock(return_value=file_record)),
            patch(_GET_REDACTOR, return_value=mock_redactor),
        ):
            resp = await public_client.get(
                f"/api/v1/public/shared/{_SHARE_TOKEN}/files/download",
                params={"path": "config.txt"},
            )

        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert _SECRET_VALUE not in body
        assert f"[REDACTED:{_SECRET_NAME}]" in body

    async def test_download_skips_redaction_for_binary(self, public_client, mock_redactor):
        """Binary files are not redacted even if they contain secret bytes."""
        binary_content = b"\x89PNG" + _SECRET_VALUE.encode()
        file_record = {
            "content_text": None,
            "content_binary": binary_content,
            "is_binary": True,
            "mime_type": "image/png",
            "file_name": "chart.png",
        }

        with (
            patch(_THREAD_BY_TOKEN, AsyncMock(return_value=_make_thread())),
            patch(_DB_GET_WS, AsyncMock(return_value=_make_workspace())),
            patch(_NORM_PATH, return_value="chart.png"),
            patch(_FILE_SVC, AsyncMock(return_value=file_record)),
            patch(_GET_REDACTOR, return_value=mock_redactor),
        ):
            resp = await public_client.get(
                f"/api/v1/public/shared/{_SHARE_TOKEN}/files/download",
                params={"path": "chart.png"},
            )

        assert resp.status_code == 200
        # Binary should NOT be redacted
        assert _SECRET_VALUE.encode() in resp.content
