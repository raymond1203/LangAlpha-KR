"""
Tests for the Users API router (src/server/app/users.py).

Covers user CRUD, preferences CRUD, and delete-preferences (reset onboarding).
The auth-sync endpoint is NOT tested here because it depends on
get_current_auth_info (a different dependency not overridden in create_test_app).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_test_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
PREF_ID = str(uuid.uuid4())


def _user(user_id="test-user-123", **overrides):
    data = {
        "user_id": user_id,
        "email": "test@example.com",
        "name": "Test User",
        "avatar_url": None,
        "timezone": "America/New_York",
        "locale": "en-US",
        "onboarding_completed": False,
        "personalization_completed": False,
        "has_api_key": False,
        "has_oauth_token": False,
        "auth_provider": "google",
        "created_at": NOW,
        "updated_at": NOW,
        "last_login_at": None,
    }
    data.update(overrides)
    return data


def _prefs(user_id="test-user-123"):
    return {
        "user_preference_id": PREF_ID,
        "user_id": user_id,
        "risk_preference": {"risk_tolerance": "moderate"},
        "investment_preference": {},
        "agent_preference": {},
        "other_preference": {},
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest_asyncio.fixture
async def client():
    from src.server.app.users import router

    app = create_test_app(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


DB = "src.server.app.users"


# ---------------------------------------------------------------------------
# POST /api/v1/users — create user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user(client):
    user = _user()
    with patch(
        f"{DB}.db_create_user",
        new_callable=AsyncMock,
        return_value=user,
    ):
        resp = await client.post(
            "/api/v1/users",
            json={"email": "test@example.com", "name": "Test User"},
        )

    assert resp.status_code == 201
    assert resp.json()["user_id"] == "test-user-123"


@pytest.mark.asyncio
async def test_create_user_duplicate_409(client):
    with patch(
        f"{DB}.db_create_user",
        new_callable=AsyncMock,
        side_effect=ValueError("User already exists"),
    ):
        resp = await client.post(
            "/api/v1/users",
            json={"email": "test@example.com"},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/v1/users/me — get current user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user(client):
    result = {"user": _user(), "preferences": _prefs()}
    with patch(
        f"{DB}.get_user_with_preferences",
        new_callable=AsyncMock,
        return_value=result,
    ):
        resp = await client.get("/api/v1/users/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["user_id"] == "test-user-123"
    assert body["preferences"] is not None


@pytest.mark.asyncio
async def test_get_current_user_no_preferences(client):
    result = {"user": _user(), "preferences": None}
    with patch(
        f"{DB}.get_user_with_preferences",
        new_callable=AsyncMock,
        return_value=result,
    ):
        resp = await client.get("/api/v1/users/me")

    assert resp.status_code == 200
    assert resp.json()["preferences"] is None


@pytest.mark.asyncio
async def test_get_current_user_not_found(client):
    with patch(
        f"{DB}.get_user_with_preferences",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.get("/api/v1/users/me")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/v1/users/me — update current user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_current_user(client):
    user = _user()
    updated = {**user, "name": "New Name"}
    with (
        patch(
            f"{DB}.db_get_user",
            new_callable=AsyncMock,
            return_value=user,
        ),
        patch(
            f"{DB}.db_update_user",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch(
            f"{DB}.db_get_user_preferences",
            new_callable=AsyncMock,
            return_value=_prefs(),
        ),
    ):
        resp = await client.put(
            "/api/v1/users/me",
            json={"name": "New Name"},
        )

    assert resp.status_code == 200
    assert resp.json()["user"]["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_current_user_not_found(client):
    with patch(
        f"{DB}.db_get_user",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.put(
            "/api/v1/users/me",
            json={"name": "X"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_current_user_db_returns_none(client):
    user = _user()
    with (
        patch(
            f"{DB}.db_get_user",
            new_callable=AsyncMock,
            return_value=user,
        ),
        patch(
            f"{DB}.db_update_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.put(
            "/api/v1/users/me",
            json={"name": "X"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/users/me/preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_preferences(client):
    user = _user()
    prefs = _prefs()
    with (
        patch(
            f"{DB}.db_get_user",
            new_callable=AsyncMock,
            return_value=user,
        ),
        patch(
            f"{DB}.db_get_user_preferences",
            new_callable=AsyncMock,
            return_value=prefs,
        ),
    ):
        resp = await client.get("/api/v1/users/me/preferences")

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "test-user-123"


@pytest.mark.asyncio
async def test_get_preferences_user_not_found(client):
    with patch(
        f"{DB}.db_get_user",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.get("/api/v1/users/me/preferences")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_preferences_prefs_not_found(client):
    user = _user()
    with (
        patch(
            f"{DB}.db_get_user",
            new_callable=AsyncMock,
            return_value=user,
        ),
        patch(
            f"{DB}.db_get_user_preferences",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = await client.get("/api/v1/users/me/preferences")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/v1/users/me/preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_preferences(client):
    user = _user()
    prefs = _prefs()
    with (
        patch(
            f"{DB}.db_get_user",
            new_callable=AsyncMock,
            return_value=user,
        ),
        patch(
            f"{DB}.upsert_user_preferences",
            new_callable=AsyncMock,
            return_value=prefs,
        ),
        patch(
            f"{DB}.maybe_complete_onboarding",
            new_callable=AsyncMock,
        ),
    ):
        resp = await client.put(
            "/api/v1/users/me/preferences",
            json={
                "risk_preference": {"risk_tolerance": "moderate"},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "test-user-123"


@pytest.mark.asyncio
async def test_update_preferences_user_not_found(client):
    with patch(
        f"{DB}.db_get_user",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.put(
            "/api/v1/users/me/preferences",
            json={},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/users/me/preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_preferences(client):
    user = _user()
    with (
        patch(
            f"{DB}.db_get_user",
            new_callable=AsyncMock,
            return_value=user,
        ),
        patch(
            f"{DB}.db_delete_user_preferences",
            new_callable=AsyncMock,
        ),
        patch(
            f"{DB}.db_update_user",
            new_callable=AsyncMock,
        ),
    ):
        resp = await client.delete("/api/v1/users/me/preferences")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True


@pytest.mark.asyncio
async def test_delete_preferences_user_not_found(client):
    with patch(
        f"{DB}.db_get_user",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.delete("/api/v1/users/me/preferences")

    assert resp.status_code == 404
