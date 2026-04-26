"""Tests for NewsDataProvider region-aware routing + fallback."""

from __future__ import annotations

from typing import Any

import pytest

from src.data_client.news_data_provider import NewsDataProvider


class FakeSource:
    """Test double — records calls and returns canned data or raises."""

    def __init__(
        self,
        name: str,
        articles: list[dict[str, Any]] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._articles = articles or []
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def get_news(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._raise:
            raise self._raise
        return {
            "results": self._articles,
            "count": len(self._articles),
            "next_cursor": None,
        }

    async def get_news_article(self, article_id: str, user_id: str | None = None):
        return None

    async def close(self) -> None:
        pass


def _provider(*entries: tuple[str, FakeSource, set[str]]) -> NewsDataProvider:
    return NewsDataProvider(list(entries))


# ---------------------------------------------------------------------------
# region routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_region_kr_picks_kr_source_first():
    kr = FakeSource("kr", [{"title": "한국 뉴스"}])
    glob = FakeSource("glob", [{"title": "global"}])

    provider = _provider(("korean", kr, {"kr"}), ("yfinance", glob, {"all"}))
    result = await provider.get_news(region="kr")

    assert result["results"][0]["title"] == "한국 뉴스"
    assert kr.calls and not glob.calls


@pytest.mark.asyncio
async def test_region_kr_falls_back_to_global_when_kr_fails():
    kr = FakeSource("kr", raise_exc=RuntimeError("RSS down"))
    glob = FakeSource("glob", [{"title": "global"}])

    provider = _provider(("korean", kr, {"kr"}), ("yfinance", glob, {"all"}))
    result = await provider.get_news(region="kr")

    assert result["results"][0]["title"] == "global"
    assert kr.calls and glob.calls


@pytest.mark.asyncio
async def test_region_us_skips_kr_only_source():
    kr = FakeSource("kr", [{"title": "한국"}])
    glob = FakeSource("glob", [{"title": "global"}])

    provider = _provider(("korean", kr, {"kr"}), ("yfinance", glob, {"all"}))
    result = await provider.get_news(region="us")

    # kr-only source 는 us region 에 매칭되지 않아 호출 안 됨
    assert not kr.calls
    assert glob.calls
    assert result["results"][0]["title"] == "global"


@pytest.mark.asyncio
async def test_region_none_skips_region_specific_sources():
    """region=None → 글로벌 소스만 사용 (kr-only 같은 region 특화 소스가 leak 되면 안 됨)."""
    kr = FakeSource("kr", [{"title": "한국"}])
    glob = FakeSource("glob", [{"title": "global"}])

    provider = _provider(("korean", kr, {"kr"}), ("yfinance", glob, {"all"}))
    result = await provider.get_news(region=None)

    # KR-only 소스는 region 미지정 시 호출되지 않음 (한국 뉴스가 en-US 사용자에게 노출되는 회귀 방지)
    assert not kr.calls
    assert glob.calls
    assert result["results"][0]["title"] == "global"


@pytest.mark.asyncio
async def test_region_none_uses_all_global_sources_in_order():
    """region=None 일 때 markets=[all] 소스들은 config 순서대로 fallback."""
    glob1 = FakeSource("glob1", raise_exc=RuntimeError("first down"))
    glob2 = FakeSource("glob2", [{"title": "second"}])

    provider = _provider(("a", glob1, {"all"}), ("b", glob2, {"all"}))
    result = await provider.get_news(region=None)

    assert glob1.calls and glob2.calls
    assert result["results"][0]["title"] == "second"


@pytest.mark.asyncio
async def test_region_case_insensitive():
    kr = FakeSource("kr", [{"title": "한국"}])
    glob = FakeSource("glob", [{"title": "global"}])

    provider = _provider(("korean", kr, {"kr"}), ("yfinance", glob, {"all"}))
    result = await provider.get_news(region="KR")

    assert result["results"][0]["title"] == "한국"


@pytest.mark.asyncio
async def test_region_with_no_matching_source_raises():
    glob = FakeSource("glob", [{"title": "global"}])
    provider = _provider(("yfinance", glob, {"us"}))

    with pytest.raises(RuntimeError, match="No news source"):
        await provider.get_news(region="kr")


# ---------------------------------------------------------------------------
# kwargs forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_region_kwarg_not_forwarded_to_source():
    """region 은 라우팅 메타데이터 — 실제 소스 get_news 에는 전달되지 않음."""
    kr = FakeSource("kr", [{"title": "x"}])
    provider = _provider(("korean", kr, {"kr"}))

    await provider.get_news(region="kr", limit=5, tickers=None)

    assert kr.calls == [{"limit": 5, "tickers": None}]


# ---------------------------------------------------------------------------
# article fallback (region 무관 — 모든 소스 시도)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_news_article_tries_all_sources():
    class ArticleSource:
        def __init__(self, returns: dict | None) -> None:
            self._returns = returns
            self.called = False

        async def get_news(self, **kwargs):
            return {"results": [], "count": 0, "next_cursor": None}

        async def get_news_article(self, article_id: str, user_id: str | None = None):
            self.called = True
            return self._returns

        async def close(self) -> None:
            pass

    src1 = ArticleSource(None)
    src2 = ArticleSource({"id": "abc", "title": "found"})
    provider = NewsDataProvider([("a", src1, {"all"}), ("b", src2, {"all"})])

    article = await provider.get_news_article("abc")

    assert src1.called and src2.called
    assert article == {"id": "abc", "title": "found"}
