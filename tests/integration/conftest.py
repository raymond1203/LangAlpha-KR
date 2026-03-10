"""Fixtures for integration tests.

Provides:
- Database fixtures for integration tests against real PostgreSQL
- Singleton reset fixtures for MCP clients
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


# ---------------------------------------------------------------------------
# MCP singleton teardown (pre-existing)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _reset_fmp_singleton():
    """Close and reset the FMP client singleton after each test."""
    yield
    from data_client.fmp import close_fmp_client
    await close_fmp_client()


@pytest_asyncio.fixture(autouse=True)
async def _reset_ginlix_singleton():
    """Close and reset the ginlix-data httpx client after each test."""
    yield
    try:
        import mcp_servers.price_data_mcp_server as mod
        if hasattr(mod, "_ginlix_http") and mod._ginlix_http is not None:
            await mod._ginlix_http.aclose()
            mod._ginlix_http = None
    except (ImportError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Database integration fixtures
# ---------------------------------------------------------------------------

# Tables in dependency (FK) order -- children first so TRUNCATE CASCADE is safe
_ALL_TABLES = [
    "automation_executions",
    "automations",
    "conversation_feedback",
    "conversation_usages",
    "conversation_responses",
    "conversation_queries",
    "conversation_threads",
    "workspace_files",
    "watchlist_items",
    "watchlists",
    "user_portfolios",
    "user_api_keys",
    "user_preferences",
    "workspaces",
    "users",
]


def _build_db_uri() -> str:
    """Build PostgreSQL connection string from env vars (CI-compatible defaults)."""
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "langalpha_test")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    sslmode = "require" if "supabase.com" in host else "disable"
    return f"postgresql://{user}:{password}@{host}:{port}/{name}?sslmode={sslmode}"


# SQL that creates the full schema from scratch (the "golden" DDL).
# This mirrors the final state after all migrations have been applied.
_SCHEMA_SQL = """
-- Utility function used by updated_at triggers
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Enable pgcrypto for encryption functions (api_keys tests)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ======== users ========
CREATE TABLE IF NOT EXISTS users (
    user_id             VARCHAR(255) PRIMARY KEY,
    email               VARCHAR(255),
    name                VARCHAR(255),
    avatar_url          TEXT,
    timezone            VARCHAR(100),
    locale              VARCHAR(50),
    onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE,
    auth_provider       VARCHAR(50),
    byok_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    membership_id       UUID,
    last_login_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== user_preferences ========
CREATE TABLE IF NOT EXISTS user_preferences (
    user_preference_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             VARCHAR(255) NOT NULL UNIQUE
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    risk_preference     JSONB,
    investment_preference JSONB,
    agent_preference    JSONB,
    other_preference    JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== workspaces ========
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             VARCHAR(255) NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    sandbox_id          VARCHAR(255),
    status              VARCHAR(20) NOT NULL DEFAULT 'creating'
                            CHECK (status IN ('creating','running','stopping','stopped','error','deleted','flash')),
    config              JSONB DEFAULT '{}'::jsonb,
    last_activity_at    TIMESTAMPTZ,
    stopped_at          TIMESTAMPTZ,
    is_pinned           BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== conversation_threads ========
CREATE TABLE IF NOT EXISTS conversation_threads (
    conversation_thread_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID NOT NULL
                            REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    current_status      VARCHAR(50) NOT NULL DEFAULT 'in_progress'
                            CHECK (current_status IN ('in_progress','interrupted','completed','error','cancelled')),
    msg_type            VARCHAR(20)
                            CHECK (msg_type IN ('flash','ptc','interrupted','task')),
    thread_index        INTEGER NOT NULL DEFAULT 0,
    title               TEXT,
    latest_checkpoint_id TEXT,
    share_token         VARCHAR(32) UNIQUE,
    is_shared           BOOLEAN NOT NULL DEFAULT FALSE,
    share_permissions   JSONB NOT NULL DEFAULT '{}',
    shared_at           TIMESTAMPTZ,
    external_id         VARCHAR(255),
    platform            VARCHAR(50),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_thread_index_per_workspace UNIQUE (workspace_id, thread_index)
);

-- ======== conversation_queries ========
CREATE TABLE IF NOT EXISTS conversation_queries (
    conversation_query_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_thread_id UUID NOT NULL
                            REFERENCES conversation_threads(conversation_thread_id) ON DELETE CASCADE,
    turn_index          INTEGER NOT NULL,
    content             TEXT NOT NULL,
    type                VARCHAR(50) NOT NULL
                            CHECK (type IN ('initial','follow_up','resume_feedback')),
    feedback_action     TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_pair_index_per_thread_query UNIQUE (conversation_thread_id, turn_index)
);

-- ======== conversation_responses ========
CREATE TABLE IF NOT EXISTS conversation_responses (
    conversation_response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_thread_id UUID NOT NULL
                            REFERENCES conversation_threads(conversation_thread_id) ON DELETE CASCADE,
    turn_index          INTEGER NOT NULL,
    status              VARCHAR(50) NOT NULL
                            CHECK (status IN ('in_progress','interrupted','completed','error','cancelled')),
    interrupt_reason    TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    warnings            TEXT[],
    errors              TEXT[],
    execution_time      FLOAT,
    sse_events          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_pair_index_per_thread_response UNIQUE (conversation_thread_id, turn_index)
);

-- ======== conversation_usages (audit ledger, no FKs) ========
CREATE TABLE IF NOT EXISTS conversation_usages (
    conversation_usage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_thread_id UUID,
    conversation_response_id UUID,
    workspace_id        UUID,
    user_id             VARCHAR(255),
    msg_type            VARCHAR(20),
    status              VARCHAR(50),
    token_usage         JSONB DEFAULT '{}'::jsonb,
    credits_used        NUMERIC(10,4),
    infrastructure_usage JSONB DEFAULT '{}'::jsonb,
    is_byok             BOOLEAN NOT NULL DEFAULT FALSE,
    credit_exempt       BOOLEAN NOT NULL DEFAULT FALSE,
    credit_exempt_reason VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== watchlists ========
CREATE TABLE IF NOT EXISTS watchlists (
    watchlist_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             VARCHAR(255) NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    is_default          BOOLEAN NOT NULL DEFAULT FALSE,
    display_order       INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_watchlist_name_per_user UNIQUE (user_id, name)
);

-- ======== watchlist_items ========
CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_item_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id        UUID NOT NULL
                            REFERENCES watchlists(watchlist_id) ON DELETE CASCADE,
    user_id             VARCHAR(255) NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    symbol              VARCHAR(50) NOT NULL,
    instrument_type     VARCHAR(50) NOT NULL,
    exchange            VARCHAR(50),
    name                VARCHAR(255),
    notes               TEXT,
    alert_settings      JSONB DEFAULT '{}'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== user_portfolios ========
CREATE TABLE IF NOT EXISTS user_portfolios (
    user_portfolio_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             VARCHAR(255) NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    symbol              VARCHAR(50) NOT NULL,
    instrument_type     VARCHAR(50) NOT NULL,
    exchange            VARCHAR(50),
    name                VARCHAR(255),
    quantity            NUMERIC(20,8) NOT NULL,
    average_cost        NUMERIC(20,8),
    currency            VARCHAR(10) NOT NULL DEFAULT 'USD',
    account_name        VARCHAR(255),
    notes               TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    first_purchased_at  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== user_api_keys ========
CREATE TABLE IF NOT EXISTS user_api_keys (
    user_id             VARCHAR(255) NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    provider            VARCHAR(100) NOT NULL,
    api_key             BYTEA NOT NULL,
    base_url            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, provider)
);

-- ======== workspace_files ========
CREATE TABLE IF NOT EXISTS workspace_files (
    workspace_file_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID NOT NULL
                            REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    file_path           VARCHAR(1024) NOT NULL,
    file_name           VARCHAR(255) NOT NULL,
    file_size           BIGINT NOT NULL DEFAULT 0,
    content_hash        VARCHAR(64),
    content_text        TEXT,
    content_binary      BYTEA,
    mime_type           VARCHAR(255),
    is_binary           BOOLEAN NOT NULL DEFAULT FALSE,
    permissions         VARCHAR(10),
    sandbox_modified_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_file_per_workspace UNIQUE (workspace_id, file_path)
);

-- ======== automations ========
CREATE TABLE IF NOT EXISTS automations (
    automation_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             VARCHAR(255) NOT NULL
                            REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE,
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    trigger_type        VARCHAR(20) NOT NULL
                            CHECK (trigger_type IN ('cron', 'once')),
    cron_expression     VARCHAR(100),
    timezone            VARCHAR(100) NOT NULL DEFAULT 'UTC',
    trigger_config      JSONB DEFAULT '{}'::jsonb,
    next_run_at         TIMESTAMPTZ,
    last_run_at         TIMESTAMPTZ,
    agent_mode          VARCHAR(20) NOT NULL DEFAULT 'flash'
                            CHECK (agent_mode IN ('ptc', 'flash')),
    instruction         TEXT NOT NULL,
    workspace_id        UUID
                            REFERENCES workspaces(workspace_id) ON DELETE SET NULL,
    llm_model           VARCHAR(100),
    additional_context  JSONB,
    thread_strategy     VARCHAR(20) NOT NULL DEFAULT 'new'
                            CHECK (thread_strategy IN ('new', 'continue')),
    conversation_thread_id UUID,
    status              VARCHAR(20) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'paused', 'completed', 'disabled')),
    max_failures        INT NOT NULL DEFAULT 3,
    failure_count       INT NOT NULL DEFAULT 0,
    delivery_config     JSONB DEFAULT '{}'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== automation_executions ========
CREATE TABLE IF NOT EXISTS automation_executions (
    automation_execution_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_id       UUID NOT NULL
                            REFERENCES automations(automation_id) ON DELETE CASCADE,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'completed', 'failed', 'timeout')),
    conversation_thread_id UUID,
    scheduled_at        TIMESTAMPTZ NOT NULL,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    error_message       TEXT,
    server_id           VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ======== conversation_feedback ========
CREATE TABLE IF NOT EXISTS conversation_feedback (
    conversation_feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_response_id UUID NOT NULL
        REFERENCES conversation_responses(conversation_response_id)
        ON DELETE CASCADE,
    user_id             VARCHAR(255) NOT NULL,
    rating              VARCHAR(20) NOT NULL
        CHECK (rating IN ('thumbs_up', 'thumbs_down')),
    issue_categories    TEXT[],
    comment             TEXT,
    consent_human_review BOOLEAN NOT NULL DEFAULT FALSE,
    review_status       VARCHAR(50)
        CHECK (review_status IN ('pending', 'confirmed', 'rejected')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_feedback_per_response_user
        UNIQUE (conversation_response_id, user_id)
);

-- ======== market_insights ========
CREATE TABLE IF NOT EXISTS market_insights (
    market_insight_id   UUID PRIMARY KEY,
    user_id             UUID,
    type                VARCHAR(30) NOT NULL DEFAULT 'daily_brief',
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    headline            TEXT,
    summary             TEXT,
    content             JSONB,
    topics              JSONB,
    sources             JSONB,
    model               VARCHAR(10),
    error_message       TEXT,
    generation_time_ms  INTEGER,
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
"""


@pytest.fixture(scope="session")
def test_db_uri() -> str:
    """Build and return the test database URI."""
    return _build_db_uri()


@pytest_asyncio.fixture(scope="session")
async def test_db_pool(test_db_uri):
    """Session-scoped async connection pool for integration tests.

    Creates all tables from the golden schema DDL, yields the pool,
    then tears down by dropping all tables.
    """
    import psycopg

    # Use a direct connection (not pooled) for schema DDL -- psycopg's
    # execute() on a pipeline-free connection handles multi-statement SQL
    # when autocommit is off and prepare_threshold is None (the default).
    async with await psycopg.AsyncConnection.connect(
        test_db_uri, autocommit=False
    ) as conn:
        async with conn.cursor() as cur:
            # Drop all tables first to ensure clean schema (handles existing DBs)
            for table in _ALL_TABLES:
                await cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            # Also drop tables not in _ALL_TABLES but in schema DDL
            for extra in ["market_insights"]:
                await cur.execute(f"DROP TABLE IF EXISTS {extra} CASCADE")
            await cur.execute(_SCHEMA_SQL)
        await conn.commit()

    # Now create the pool for actual test operations
    pool = AsyncConnectionPool(
        conninfo=test_db_uri,
        min_size=1,
        max_size=5,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=False,
    )
    await pool.open()
    await pool.wait()

    yield pool

    # Teardown: truncate (not drop) so the schema remains for debugging
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for table in _ALL_TABLES:
                await cur.execute(f"TRUNCATE TABLE {table} CASCADE")

    await pool.close()


@pytest_asyncio.fixture
async def cleanup_tables(test_db_pool):
    """Truncate all tables after each test for isolation.

    NOT autouse — only activated when a test explicitly requests it
    (or transitively via seed_user/seed_workspace).
    """
    yield
    async with test_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            for table in _ALL_TABLES:
                await cur.execute(f"TRUNCATE TABLE {table} CASCADE")


@pytest_asyncio.fixture
async def db_conn(test_db_pool):
    """Yield a single async connection from the test pool.

    The connection is used in autocommit mode (pool default).
    """
    async with test_db_pool.connection() as conn:
        yield conn


@pytest_asyncio.fixture
async def patched_get_db_connection(test_db_pool):
    """Patch get_db_connection to use the test pool instead of production.

    This allows database module functions (workspace.py, user.py, etc.)
    to transparently use the test database.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_get_db_connection():
        async with test_db_pool.connection() as conn:
            yield conn

    with patch(
        "src.server.database.conversation.get_db_connection",
        _test_get_db_connection,
    ):
        # Also patch the re-export in every database module that imports it
        with patch(
            "src.server.database.workspace.get_db_connection",
            _test_get_db_connection,
        ), patch(
            "src.server.database.user.get_db_connection",
            _test_get_db_connection,
        ), patch(
            "src.server.database.watchlist.get_db_connection",
            _test_get_db_connection,
        ), patch(
            "src.server.database.portfolio.get_db_connection",
            _test_get_db_connection,
        ), patch(
            "src.server.database.api_keys.get_db_connection",
            _test_get_db_connection,
        ), patch(
            "src.server.database.automation.get_db_connection",
            _test_get_db_connection,
        ):
            yield _test_get_db_connection


@pytest.fixture
def test_user_id() -> str:
    """Deterministic test user ID."""
    return "test-user-integration-001"


@pytest_asyncio.fixture
async def seed_user(patched_get_db_connection, cleanup_tables, test_user_id):
    """Insert a test user and return the user dict.

    Most database modules require a user row to exist (FK constraints).
    """
    from src.server.database.user import create_user

    user = await create_user(
        user_id=test_user_id,
        email="test@example.com",
        name="Test User",
    )
    return user


@pytest_asyncio.fixture
async def seed_workspace(seed_user, patched_get_db_connection):
    """Insert a test workspace and return its dict.

    Depends on seed_user to satisfy FK constraints.
    """
    from src.server.database.workspace import create_workspace

    ws = await create_workspace(
        user_id=seed_user["user_id"],
        name="Test Workspace",
        description="Integration test workspace",
        status="running",
    )
    return ws
