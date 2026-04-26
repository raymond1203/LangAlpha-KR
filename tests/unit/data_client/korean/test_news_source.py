"""Tests for KoreanNewsSource — RSS aggregator for Korean financial news."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.data_client.korean.news_source import (
    _RSS_FEEDS,
    KoreanNewsSource,
    _parse_feed,
    _parse_pub_date,
    _stable_id,
)


def _url_for(name: str) -> str:
    """_RSS_FEEDS 에서 source name 으로 URL 조회 — 테스트가 feed 순서/추가에 둔감해지도록."""
    for feed_name, url, _publisher, _favicon in _RSS_FEEDS:
        if feed_name == name:
            return url
    raise KeyError(f"unknown feed name: {name}")


def _make_url_dispatch(responses: dict[str, bytes | Exception]) -> object:
    """url → response/exception 매핑 dispatcher. 등록되지 않은 URL 은 ValueError."""
    def _dispatch(url: str, *_, **__):
        if url not in responses:
            raise ValueError(f"unmocked URL: {url}")
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        resp = MagicMock(spec=httpx.Response)
        resp.content = value
        resp.raise_for_status = MagicMock()
        return resp
    return _dispatch

# ---------------------------------------------------------------------------
# RSS sample fixtures (real-world payload structure)
# ---------------------------------------------------------------------------

_MK_SAMPLE = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/"
     version="2.0">
<channel>
  <title>매일경제 : 증권</title>
  <item>
    <title>삼성전자 4분기 실적 발표</title>
    <link>https://www.mk.co.kr/news/stock/12027724</link>
    <pubDate>Sun, 26 Apr 2026 17:45:33 +0900</pubDate>
    <description>4분기 영업이익 8.5조원 기록</description>
    <media:content medium="image" url="https://pimg.mk.co.kr/news/cms/test.jpg" />
  </item>
  <item>
    <title>SK하이닉스 신규 투자 발표</title>
    <link>https://www.mk.co.kr/news/stock/12027725</link>
    <pubDate>Sun, 26 Apr 2026 17:30:00 +0900</pubDate>
    <description>HBM3E 양산 본격화</description>
  </item>
</channel>
</rss>
""".encode("utf-8")

_YNA_SAMPLE = """<?xml version='1.0' encoding='UTF-8'?>
<rss version="2.0">
<channel>
  <title>연합뉴스 경제</title>
  <item>
    <title>한은 기준금리 동결</title>
    <link>https://www.yna.co.kr/view/AKR20260426001</link>
    <pubDate>Sun, 26 Apr 2026 16:00:00 +0900</pubDate>
    <description>금융통화위원회 결과</description>
  </item>
</channel>
</rss>
""".encode("utf-8")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParsePubDate:
    def test_rfc822_to_iso(self):
        result = _parse_pub_date("Sun, 26 Apr 2026 17:45:33 +0900")
        assert result.startswith("2026-04-26T17:45:33")

    def test_empty_returns_empty_string(self):
        assert _parse_pub_date("") == ""
        assert _parse_pub_date(None) == ""

    def test_garbage_returns_empty_string(self):
        # parsedate_to_datetime 가 garbage 에 대해 ValueError/TypeError 양쪽 모두 가능
        assert _parse_pub_date("not a date at all xyz") == ""


class TestStableId:
    def test_same_url_same_id(self):
        url = "https://www.mk.co.kr/news/stock/123"
        assert _stable_id(url) == _stable_id(url)

    def test_different_url_different_id(self):
        assert _stable_id("a") != _stable_id("b")

    def test_id_is_16_chars(self):
        assert len(_stable_id("https://example.com/article/1")) == 16


# ---------------------------------------------------------------------------
# RSS parser
# ---------------------------------------------------------------------------


class TestParseFeed:
    def test_parses_mk_feed(self):
        articles = _parse_feed(_MK_SAMPLE, "매일경제", "https://www.mk.co.kr/favicon.ico")
        assert len(articles) == 2
        assert articles[0]["title"] == "삼성전자 4분기 실적 발표"
        assert articles[0]["article_url"] == "https://www.mk.co.kr/news/stock/12027724"
        assert articles[0]["source"]["name"] == "매일경제"
        assert articles[0]["source"]["favicon_url"] == "https://www.mk.co.kr/favicon.ico"
        assert articles[0]["source"]["homepage_url"] == "https://www.mk.co.kr"
        assert articles[0]["image_url"] == "https://pimg.mk.co.kr/news/cms/test.jpg"
        assert articles[0]["published_at"].startswith("2026-04-26T17:45:33")
        assert articles[0]["tickers"] == []

    def test_skips_items_without_title_or_link(self):
        broken = b"""<?xml version='1.0'?>
<rss version="2.0"><channel>
  <item><title>No link here</title></item>
  <item><link>https://x.com/no-title</link></item>
  <item><title>Both</title><link>https://x.com/ok</link></item>
</channel></rss>"""
        articles = _parse_feed(broken, "Test", None)
        assert len(articles) == 1
        assert articles[0]["title"] == "Both"

    def test_invalid_xml_returns_empty(self):
        assert _parse_feed(b"not xml at all", "Test", None) == []


# ---------------------------------------------------------------------------
# get_news integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_news_aggregates_and_sorts():
    """매경 + 연합 결과를 합쳐 최신순으로 정렬."""
    source = KoreanNewsSource()

    def make_response(content: bytes) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.content = content
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = [
        make_response(_MK_SAMPLE),
        make_response(_YNA_SAMPLE),
    ]

    with patch.object(source, "_get_client", return_value=mock_client):
        result = await source.get_news(limit=10)

    assert result["count"] == 3
    # 최신순: MK 17:45:33 > MK 17:30:00 > YNA 16:00:00
    titles = [a["title"] for a in result["results"]]
    assert titles == [
        "삼성전자 4분기 실적 발표",
        "SK하이닉스 신규 투자 발표",
        "한은 기준금리 동결",
    ]
    assert result["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_news_dedupes_by_url():
    """동일 URL 이 두 피드에 등장하면 첫 번째만 유지."""
    source = KoreanNewsSource()
    dup_xml = b"""<?xml version='1.0'?>
<rss version="2.0"><channel>
  <item>
    <title>Same article</title>
    <link>https://www.mk.co.kr/dup/1</link>
    <pubDate>Sun, 26 Apr 2026 17:00:00 +0900</pubDate>
  </item>
</channel></rss>"""

    def make_response(content: bytes) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.content = content
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = [
        make_response(dup_xml),
        make_response(dup_xml),
    ]

    with patch.object(source, "_get_client", return_value=mock_client):
        result = await source.get_news(limit=10)

    assert result["count"] == 1


@pytest.mark.asyncio
async def test_get_news_respects_limit():
    source = KoreanNewsSource()

    def make_response(content: bytes) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.content = content
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = [
        make_response(_MK_SAMPLE),
        make_response(_YNA_SAMPLE),
    ]

    with patch.object(source, "_get_client", return_value=mock_client):
        result = await source.get_news(limit=2)

    assert result["count"] == 2


@pytest.mark.asyncio
async def test_get_news_one_feed_failure_does_not_break_aggregation():
    """매경이 실패해도 연합 결과는 유지. URL 기반 dispatch 라 _RSS_FEEDS 순서가 바뀌어도 안정."""
    source = KoreanNewsSource()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = _make_url_dispatch({
        _url_for("mk_stock"): httpx.HTTPError("매경 down"),
        _url_for("yna_economy"): _YNA_SAMPLE,
    })

    with patch.object(source, "_get_client", return_value=mock_client):
        result = await source.get_news(limit=10)

    assert result["count"] == 1
    assert result["results"][0]["title"] == "한은 기준금리 동결"


@pytest.mark.asyncio
async def test_get_news_article_returns_none():
    source = KoreanNewsSource()
    assert await source.get_news_article("anything") is None


@pytest.mark.asyncio
async def test_close_releases_client():
    source = KoreanNewsSource()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    source._client = mock_client
    await source.close()
    mock_client.aclose.assert_awaited_once()
    assert source._client is None
