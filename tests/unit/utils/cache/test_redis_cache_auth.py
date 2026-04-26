"""
Tests for RedisCacheClient password fallback (issue #34).

URL 에 인증 정보가 없을 때 REDIS_PASSWORD env var 가 ConnectionPool 에
주입되는지 검증. URL 에 password 가 명시되면 ConnectionPool.from_url 의
URL 파싱이 우선이므로 env var 는 무시되어야 함 (redis-py 기본 동작).
"""

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_pool():
    """ConnectionPool.from_url 을 mock 해서 호출 인자만 캡처."""
    with patch("src.utils.cache.redis_cache.ConnectionPool") as mock_pool_cls:
        mock_pool_cls.from_url = MagicMock(return_value=MagicMock())
        yield mock_pool_cls


@pytest.fixture
def mock_redis_client():
    """Redis client 의 ping 을 mock 해서 connect() 의 health check 통과."""
    with patch("src.utils.cache.redis_cache.redis.Redis") as mock_redis_cls:
        instance = MagicMock()
        instance.ping = MagicMock(return_value=None)

        async def _ping():
            return True

        instance.ping = _ping
        mock_redis_cls.return_value = instance
        yield mock_redis_cls


@pytest.mark.asyncio
async def test_password_env_var_injected_when_url_has_no_auth(mock_pool, mock_redis_client, monkeypatch):
    """REDIS_PASSWORD env 가 있고 URL 에 password 없을 때 password 옵션 주입."""
    from src.utils.cache.redis_cache import RedisCacheClient

    monkeypatch.setenv("REDIS_PASSWORD", "secret123")

    client = RedisCacheClient(url="redis://localhost:6379/0")
    client.enabled = True
    await client.connect()

    mock_pool.from_url.assert_called_once()
    _, kwargs = mock_pool.from_url.call_args
    assert kwargs["password"] == "secret123"
    assert kwargs["max_connections"] == 50


@pytest.mark.asyncio
async def test_no_password_kwarg_when_env_unset(mock_pool, mock_redis_client, monkeypatch):
    """REDIS_PASSWORD env 가 없으면 password kwarg 자체를 안 넘겨야 (redis-py default)."""
    from src.utils.cache.redis_cache import RedisCacheClient

    monkeypatch.delenv("REDIS_PASSWORD", raising=False)

    client = RedisCacheClient(url="redis://localhost:6379/0")
    client.enabled = True
    await client.connect()

    _, kwargs = mock_pool.from_url.call_args
    assert "password" not in kwargs


@pytest.mark.asyncio
async def test_url_with_inline_password_takes_precedence(mock_pool, mock_redis_client, monkeypatch):
    """URL 에 password 명시 시 redis-py from_url 이 그걸 우선 — env var 는 fallback 으로만
    kwargs 에 들어가도 redis-py 가 URL 파싱을 우선. 본 테스트는 우리 코드가 'env 가
    있으면 무조건 kwargs 에 넣는다' 는 단순 contract 만 검증.
    """
    from src.utils.cache.redis_cache import RedisCacheClient

    monkeypatch.setenv("REDIS_PASSWORD", "env_pw")

    client = RedisCacheClient(url="redis://:url_pw@localhost:6379/0")
    client.enabled = True
    await client.connect()

    _, kwargs = mock_pool.from_url.call_args
    # 우리 코드는 env 만 보고 kwargs 에 항상 주입. URL 우선순위는 redis-py 가 결정.
    assert kwargs["password"] == "env_pw"
    args, _ = mock_pool.from_url.call_args
    assert args[0] == "redis://:url_pw@localhost:6379/0"


@pytest.mark.asyncio
async def test_disabled_cache_skips_connect(mock_pool, mock_redis_client):
    """enabled=False 일 때 ConnectionPool.from_url 호출 자체가 없어야."""
    from src.utils.cache.redis_cache import RedisCacheClient

    client = RedisCacheClient(url="redis://localhost:6379/0")
    client.enabled = False
    await client.connect()

    mock_pool.from_url.assert_not_called()
