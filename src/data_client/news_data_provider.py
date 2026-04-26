"""Composite news data provider with sequential fallback.

지원: 옵션 ``region`` 파라미터로 지역 특화 소스 우선 라우팅. ``markets`` 가
``"all"`` 인 소스는 모든 region 에서 fallback 으로 사용 가능.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import NewsDataSource

logger = logging.getLogger(__name__)


class NewsDataProvider:
    """Tries each news source in order, falling back on failure.

    Sources may be registered with a ``markets`` set (e.g. ``{"kr"}`` or
    ``{"all"}``). When ``get_news`` is called with ``region``, only sources
    matching that region (또는 ``"all"``) are tried, in original order.
    """

    def __init__(
        self, sources: list[tuple[str, NewsDataSource, set[str]]]
    ) -> None:
        self._sources = sources

    def _sources_for(
        self, region: str | None
    ) -> list[tuple[str, NewsDataSource]]:
        """region 에 매칭되는 (name, source) 페어 반환.

        - region=None → markets 에 "all" 포함된 글로벌 소스만 (region 특화 소스가 leak 되지 않게).
        - region 지정 → "all" 또는 해당 region (lowercase) 포함 소스를 config 순서대로.
        """
        if region is None:
            return [
                (name, src)
                for name, src, markets in self._sources
                if "all" in markets
            ]
        region_lower = region.lower()
        return [
            (name, src)
            for name, src, markets in self._sources
            if "all" in markets or region_lower in markets
        ]

    async def get_news(
        self, region: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        candidates = self._sources_for(region)
        if not candidates:
            raise RuntimeError(
                f"No news source available for region={region!r}"
            )
        last_exc: Exception | None = None
        for name, source in candidates:
            try:
                return await source.get_news(**kwargs)
            except Exception as exc:
                logger.warning(
                    "news.fallback | source=%s region=%s err=%s",
                    name, region, exc,
                )
                last_exc = exc
        # candidates 가 비어있지 않은 한 위 루프에서 last_exc 가 채워지지만,
        # 명시적으로 분기해 None 인 가상 케이스도 안전하게 처리.
        if last_exc is None:
            raise RuntimeError(
                f"All news sources for region={region!r} failed without exception"
            )
        raise last_exc

    async def get_news_article(
        self, article_id: str, user_id: str | None = None
    ) -> dict[str, Any] | None:
        """Try each source until one returns the article."""
        for name, source, _markets in self._sources:
            try:
                result = await source.get_news_article(article_id, user_id=user_id)
                if result is not None:
                    return result
            except Exception as exc:
                logger.warning("news.article_fallback | source=%s err=%s", name, exc)
        return None

    async def close(self) -> None:
        for name, source, _markets in self._sources:
            try:
                await source.close()
            except Exception:
                logger.warning("news.close | source=%s failed", name, exc_info=True)

    @property
    def source_names(self) -> list[str]:
        return [name for name, _, _ in self._sources]
