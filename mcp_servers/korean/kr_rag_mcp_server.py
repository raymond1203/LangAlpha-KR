# FORK: DART 공시 RAG 검색 MCP 서버 (Qdrant + OpenAI 임베딩)
"""DART 공시 의미 검색 MCP 서버.

기존 kr_dart_mcp_server 가 메타데이터 / 키워드 조회라면, 이 서버는
**의미 기반(semantic) 검색**을 제공한다. Qdrant 에 사전 색인된 공시 청크를
쿼리 임베딩으로 검색한다.

색인은 `src/data_client/korean/rag_ingest.py::ingest_corpus` 로 사전 수행.

환경변수: OPENAI_API_KEY, QDRANT_URL, QDRANT_API_KEY

도구:
- search_korean_filings: 자연어 쿼리로 관련 공시 청크 검색
- get_filing_chunks: 단일 공시의 모든 청크 조회
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP


logger = logging.getLogger(__name__)


DEFAULT_COLLECTION = "dart_filings"
# 임베딩 모델은 인제스트 시 사용한 값과 반드시 일치해야 한다
# (차원 / 벡터 공간이 달라지면 cosine 거리 의미가 없음).
# 호출자 / 운영자가 EMBEDDING_MODEL 환경변수로 명시하면 그것을, 아니면
# 기본값을 쓴다. `rag_ingest.IngestConfig.embedding_model` 과 같은 변수를
# 공유할 것을 권장.
DEFAULT_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
DEFAULT_TOP_K = 10
MAX_TOP_K = 50


mcp = FastMCP("KoreanRAGMCP")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_openai():
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다.",
        )
    return OpenAI(api_key=api_key)


def _get_qdrant():
    from qdrant_client import QdrantClient

    url = os.getenv("QDRANT_URL")
    api_key = os.getenv("QDRANT_API_KEY")
    if not url:
        raise RuntimeError(
            "QDRANT_URL 환경변수가 설정되지 않았습니다. Qdrant Cloud 대시보드 "
            "에서 발급 후 .env 에 추가하세요.",
        )
    return QdrantClient(url=url, api_key=api_key, prefer_grpc=False, timeout=30)


def _embed_query(text: str, model: Optional[str] = None) -> list[float]:
    """쿼리 임베딩. ``model`` 이 None 이면 모듈 기본값(환경변수 반영)을 사용."""
    effective_model = model or DEFAULT_EMBEDDING_MODEL
    client = _get_openai()
    resp = client.embeddings.create(model=effective_model, input=[text])
    return resp.data[0].embedding


def _make_response(data_type: str, data: Any, **extra: Any) -> dict:
    resp: dict[str, Any] = {
        "data_type": data_type,
        "source": "dart_rag",
        "data": data,
    }
    if isinstance(data, list):
        resp["count"] = len(data)
    resp.update(extra)
    return resp


def _make_error(msg: str) -> dict:
    return {"data_type": "error", "source": "dart_rag", "error": msg}


def _build_filter(
    ticker: Optional[str],
    corp_name: Optional[str],
    filing_type: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
):
    """payload 필터 구성. 빈 조건은 무시."""
    from qdrant_client.http import models as qm

    must: list[Any] = []
    if ticker:
        must.append(qm.FieldCondition(key="ticker", match=qm.MatchValue(value=ticker)))
    if corp_name:
        must.append(
            qm.FieldCondition(key="corp_name", match=qm.MatchValue(value=corp_name))
        )
    if filing_type:
        # 정확 일치 보단 keyword match. 부분 일치가 필요하면 별도 스캔 필요.
        must.append(
            qm.FieldCondition(
                key="filing_type", match=qm.MatchValue(value=filing_type),
            )
        )
    if date_from or date_to:
        rng_kwargs: dict[str, Any] = {}
        if date_from:
            rng_kwargs["gte"] = date_from
        if date_to:
            rng_kwargs["lte"] = date_to
        # filing_date 는 ISO-8601 (YYYY-MM-DD) 문자열이라 DatetimeRange 가 정확.
        # 숫자용 Range 는 string 입력을 거부함.
        must.append(
            qm.FieldCondition(
                key="filing_date", range=qm.DatetimeRange(**rng_kwargs),
            )
        )
    return qm.Filter(must=must) if must else None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_korean_filings(
    query: str,
    ticker: Optional[str] = None,
    corp_name: Optional[str] = None,
    filing_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    collection: str = DEFAULT_COLLECTION,
    embedding_model: Optional[str] = None,
) -> dict:
    """자연어 쿼리로 DART 공시 청크를 의미 검색한다.

    Args:
        query: 자연어 질문 (예: "삼성전자 메모리 반도체 전망", "회사채 발행 이슈")
        ticker: 6자리 종목코드 필터 (예: "005930")
        corp_name: 회사명 정확 일치 필터 (예: "삼성전자")
        filing_type: 공시명 정확 일치 필터 (예: "사업보고서")
        date_from: 접수일 하한 YYYY-MM-DD
        date_to: 접수일 상한 YYYY-MM-DD
        top_k: 반환 청크 수 (기본 10, 최대 50)
        collection: Qdrant 컬렉션명
        embedding_model: None 이면 EMBEDDING_MODEL 환경변수 / 모듈 기본값.
            인제스트 시 사용된 모델과 **반드시 동일**해야 한다.

    Returns:
        관련 청크 리스트. 각 요소에 점수, corp_name, ticker, filing_date,
        filing_type, rcept_no, chunk_index, text, source_url, section 포함.
    """
    try:
        if not query or not query.strip():
            return _make_error("query 가 비어있습니다")
        k = max(1, min(int(top_k), MAX_TOP_K))

        qdrant = _get_qdrant()
        vector = _embed_query(query.strip(), model=embedding_model)
        qfilter = _build_filter(
            ticker=ticker,
            corp_name=corp_name,
            filing_type=filing_type,
            date_from=date_from,
            date_to=date_to,
        )

        # query_points 는 qdrant-client 1.10+ 표준 API. search 는 deprecated.
        resp = qdrant.query_points(
            collection_name=collection,
            query=vector,
            query_filter=qfilter,
            limit=k,
            with_payload=True,
        )
        hits = resp.points

        data = []
        for h in hits:
            payload = h.payload or {}
            data.append(
                {
                    "score": round(float(h.score), 4),
                    "rcept_no": payload.get("rcept_no"),
                    "corp_name": payload.get("corp_name"),
                    "ticker": payload.get("ticker"),
                    "filing_date": payload.get("filing_date"),
                    "filing_type": payload.get("filing_type"),
                    "chunk_index": payload.get("chunk_index"),
                    "text": payload.get("text"),
                    "source_url": payload.get("source_url"),
                    "section": payload.get("section"),
                }
            )
        return _make_response(
            "dart_rag_search",
            data,
            query=query,
            top_k=k,
            collection=collection,
        )
    except Exception as e:  # noqa: BLE001
        # 전체 스택은 로그로 남기고, 사용자에겐 예외 타입만 노출해
        # 내부 경로 / 키 등 민감 정보 leak 차단.
        logger.exception("search_korean_filings 실패 query=%r", query)
        return _make_error(f"DART RAG 검색 실패 ({type(e).__name__})")


@mcp.tool()
def get_filing_chunks(
    rcept_no: str,
    limit: int = 100,
    collection: str = DEFAULT_COLLECTION,
) -> dict:
    """단일 공시의 모든 청크를 chunk_index 순서로 반환한다.

    Args:
        rcept_no: 공시 접수번호 (14자리)
        limit: 반환 상한 (기본 100)
        collection: Qdrant 컬렉션명

    Returns:
        청크 리스트 (chunk_index 오름차순).
    """
    try:
        from qdrant_client.http import models as qm

        if not rcept_no or not rcept_no.strip():
            return _make_error("rcept_no 가 비어있습니다")
        qdrant = _get_qdrant()
        lim = max(1, min(int(limit), 500))

        qfilter = qm.Filter(
            must=[
                qm.FieldCondition(
                    key="rcept_no", match=qm.MatchValue(value=rcept_no.strip()),
                )
            ]
        )
        records, _ = qdrant.scroll(
            collection_name=collection,
            scroll_filter=qfilter,
            limit=lim,
            with_payload=True,
            with_vectors=False,
        )

        data = []
        for r in records:
            payload = r.payload or {}
            data.append(
                {
                    "rcept_no": payload.get("rcept_no"),
                    "chunk_index": payload.get("chunk_index"),
                    "corp_name": payload.get("corp_name"),
                    "filing_type": payload.get("filing_type"),
                    "text": payload.get("text"),
                    "source_url": payload.get("source_url"),
                    "section": payload.get("section"),
                }
            )
        data.sort(key=lambda x: (x.get("chunk_index") or 0))
        return _make_response("dart_rag_chunks", data, rcept_no=rcept_no)
    except Exception as e:  # noqa: BLE001
        # 사용자 응답엔 타입만, 상세 스택은 로그로.
        logger.exception("get_filing_chunks 실패 rcept_no=%r", rcept_no)
        return _make_error(f"공시 청크 조회 실패 ({type(e).__name__})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
