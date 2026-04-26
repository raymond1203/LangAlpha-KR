"""Simple Redis TTL cache for news articles."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.utils.cache.redis_cache import get_cache_client

logger = logging.getLogger(__name__)

# TTLs in seconds
_GENERAL_TTL = 300  # 5 min for general news
_TICKER_TTL = 180  # 3 min for ticker-specific news


def _cache_key(
    tickers: list[str] | None, limit: int, region: str | None = None
) -> str:
    # FORK: region 을 키에 포함 → KR/US 캐시 분리 (region 미지정 시 기존 키 호환)
    region_part = f":r:{region.lower()}" if region else ""
    if tickers:
        tag = ",".join(sorted(t.upper() for t in tickers))
        return f"news:tickers:{tag}:{limit}{region_part}"
    return f"news:general:{limit}{region_part}"


class NewsCacheService:
    _instance: NewsCacheService | None = None

    def __new__(cls) -> NewsCacheService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get(
        self,
        tickers: list[str] | None = None,
        limit: int = 20,
        region: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            cache = get_cache_client()
            key = _cache_key(tickers, limit, region)
            raw = await cache.get(key)
            if raw is not None:
                return json.loads(raw)
        except Exception:
            logger.debug("news_cache.get.miss", exc_info=True)
        return None

    async def get_article_by_id(self, article_id: str) -> dict[str, Any] | None:
        """Scan all cached news lists for an article matching the given ID."""
        try:
            cache = get_cache_client()
            keys = await cache.keys("news:*")
            for key in keys:
                raw = await cache.get(key)
                if raw:
                    data = json.loads(raw)
                    for article in data.get("results", []):
                        if article.get("id") == article_id:
                            return article
        except Exception:
            logger.debug("news_cache.get_article_by_id.failed", exc_info=True)
        return None

    async def set(
        self,
        data: dict[str, Any],
        tickers: list[str] | None = None,
        limit: int = 20,
        region: str | None = None,
    ) -> None:
        try:
            cache = get_cache_client()
            key = _cache_key(tickers, limit, region)
            ttl = _TICKER_TTL if tickers else _GENERAL_TTL
            await cache.set(key, json.dumps(data), ttl=ttl)
        except Exception:
            logger.debug("news_cache.set.failed", exc_info=True)
