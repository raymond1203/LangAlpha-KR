"""KoreanNewsSource — 한국 금융 뉴스 RSS 어그리게이터.

매일경제 증권 + 연합뉴스 경제 RSS 를 병렬로 fetch, URL 기반 dedupe, published_at desc 정렬.
NewsDataSource Protocol (src/data_client/base.py) 준수.

설계 결정:
- 한국경제(hankyung.com) RSS 는 deprecated 되어 HTML 페이지로 redirect — 제외.
- 단일 RSS 가 죽어도 다른 피드로 결과 유지 (return_exceptions 대신 _fetch_one 내부 try/except).
- ticker 매핑은 RSS 메타데이터에 없어 v1 에서는 빈 리스트 — 후속에서 본문 NER 등으로 보강 가능.
- get_news_article 은 RSS 가 단방향 hash id 만 제공해 미지원 (None 반환).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET  # ParseError 타입 비교용 (parse 자체는 defusedxml)

import defusedxml.ElementTree as DefusedET  # FORK: 외부 RSS 의 XXE/외부 엔티티 방어
import httpx

logger = logging.getLogger(__name__)

# (source_name, RSS URL, publisher 표시명, favicon URL)
_RSS_FEEDS: list[tuple[str, str, str, str | None]] = [
    (
        "mk_stock",
        "https://www.mk.co.kr/rss/50200011/",
        "매일경제",
        "https://www.mk.co.kr/favicon.ico",
    ),
    (
        "yna_economy",
        "https://www.yna.co.kr/rss/economy.xml",
        "연합뉴스",
        "https://www.yna.co.kr/favicon.ico",
    ),
]

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)
_USER_AGENT = "LangAlphaKR/1.0 (+https://github.com/raymond1203/LangAlpha-KR)"
_MEDIA_NS = "{http://search.yahoo.com/mrss/}"


def _parse_pub_date(raw: str | None) -> str:
    """RFC 822 → ISO 8601. 빈 값/파싱 실패 → 빈 문자열."""
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return ""


def _stable_id(link: str) -> str:
    """URL 기반 안정 article id — 같은 기사면 동일 id (캐시 hit, frontend key 안정)."""
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def _parse_feed(
    xml_bytes: bytes, publisher: str, favicon: str | None
) -> list[dict[str, Any]]:
    """RSS 2.0 XML → 표준 article dict 리스트.

    defusedxml 로 파싱해 외부 엔티티 (XXE) / billion laughs 등을 차단.
    defusedxml 은 ET.ParseError 호환 예외를 raise 하므로 동일 except 로 처리.
    """
    try:
        root = DefusedET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning(
            "korean.news.rss.parse_failed | publisher=%s err=%s", publisher, exc
        )
        return []

    articles: list[dict[str, Any]] = []
    for item in root.iter("item"):
        title_elem = item.find("title")
        link_elem = item.find("link")
        if title_elem is None or link_elem is None:
            continue
        title = (title_elem.text or "").strip()
        link = (link_elem.text or "").strip()
        if not title or not link:
            continue

        pub_date_elem = item.find("pubDate")
        published = _parse_pub_date(
            pub_date_elem.text if pub_date_elem is not None else None
        )

        desc_elem = item.find("description")
        description = (
            (desc_elem.text or "").strip() if desc_elem is not None else None
        )

        image_url: str | None = None
        media = item.find(f"{_MEDIA_NS}content")
        if media is not None:
            image_url = media.attrib.get("url")

        homepage = ""
        try:
            parsed = urlparse(link)
            if parsed.scheme and parsed.netloc:
                homepage = f"{parsed.scheme}://{parsed.netloc}"
        except ValueError:
            homepage = ""

        articles.append(
            {
                "id": _stable_id(link),
                "title": title,
                "text": None,
                "article_url": link,
                "published_at": published,
                "source": {
                    "name": publisher,
                    "logo_url": None,
                    "homepage_url": homepage or None,
                    "favicon_url": favicon,
                },
                "tickers": [],
                "image_url": image_url,
                "author": publisher,
                "description": description,
                "keywords": [],
                "sentiments": [],
            }
        )
    return articles


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    publisher: str,
    favicon: str | None,
) -> list[dict[str, Any]]:
    """단일 RSS 의 fetch + parse — 어떤 단계에서 실패하더라도 빈 리스트로 graceful degradation.

    parse 도 try 안에 둬서 _parse_feed 가 ET.ParseError 외 예측 못한 예외를 raise 해도
    asyncio.gather 가 깨지지 않도록 보장 (단일 피드 실패 → 다른 피드 결과 유지 계약).
    """
    try:
        resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        return _parse_feed(resp.content, publisher, favicon)
    except Exception as exc:
        logger.warning("korean.news.fetch_failed | url=%s err=%s", url, exc)
        return []


class KoreanNewsSource:
    """NewsDataSource — 한국 금융 뉴스 RSS 어그리게이터."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT, follow_redirects=True
            )
        return self._client

    async def get_news(
        self,
        tickers: list[str] | None = None,
        limit: int = 20,
        published_after: str | None = None,
        published_before: str | None = None,
        cursor: str | None = None,
        order: str | None = None,
        sort: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        # tickers / cursor / 시간 필터는 v1 미지원 — RSS 메타데이터에 ticker 없음, RSS 는 단순 latest-N.
        # limit 가드 — FastAPI 라우터가 ge=1 검증하지만 본 클래스는 직접 호출도 가능 (테스트/에이전트 등).
        limit = max(0, limit)
        client = await self._get_client()
        feeds = await asyncio.gather(
            *(
                _fetch_one(client, url, publisher, favicon)
                for _name, url, publisher, favicon in _RSS_FEEDS
            )
        )

        # URL 기반 dedupe — 매경/연합이 동일 사건 다룬 경우 가장 먼저 등장한 것만 유지
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for batch in feeds:
            for art in batch:
                url = art.get("article_url")
                if not url or url in seen:
                    continue
                seen.add(url)
                merged.append(art)

        # 최신순 정렬 (빈 published_at 은 가장 뒤로)
        merged.sort(key=lambda a: a.get("published_at") or "", reverse=True)

        if len(merged) > limit:
            merged = merged[:limit]

        return {
            "results": merged,
            "count": len(merged),
            "next_cursor": None,
        }

    async def get_news_article(
        self,
        article_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        # RSS 는 article id → URL 단방향 hash 라 단건 lookup 미지원
        return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
