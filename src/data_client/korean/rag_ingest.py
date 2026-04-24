# FORK: DART 공시 RAG 인제스트 파이프라인 (DART → 청킹 → OpenAI 임베딩 → Qdrant)
"""DART 공시 본문을 수집, 청킹, 임베딩해 Qdrant에 색인한다.

파이프라인:
    1. OpenDartReader 로 대상 기업의 공시 목록 조회
    2. rcept_no 별로 본문(document) 가져와 HTML/공백 정리
    3. 문자 단위 오버래핑 청킹 (문단·문장 경계 우선)
    4. OpenAI text-embedding-3-small 배치 호출
    5. Qdrant upsert (UUID5 기반 idempotent point id)

환경변수: OPENAI_API_KEY, QDRANT_URL, QDRANT_API_KEY, DART_API_KEY
"""

from __future__ import annotations

import html
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------

DEFAULT_COLLECTION = "dart_filings"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIM = 1536
DEFAULT_CHUNK_SIZE = 500   # 문자 수 (한국어 기준 대략 150~250 토큰)
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_BATCH_SIZE = 64    # OpenAI 배치당 청크 수
# Qdrant 는 기본적으로 단일 HTTP 요청 payload 를 32MB 로 제한한다.
# 1536-dim float32 × 4bytes + payload metadata ≈ 8KB/point 기준으로
# 256 points = ~2MB → 한도 대비 15배 여유. 대량 사업보고서 (수천 청크) 도
# 여러 upsert call 로 쪼개져 안전하게 적재.
DEFAULT_UPSERT_BATCH_SIZE = 256

# OpenAI 임베딩 모델별 기본 출력 차원.
# IngestConfig.__post_init__ 에서 model → dim 정합성 자동 조정에 사용.
KNOWN_EMBEDDING_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# DART 공시 원문 URL 템플릿. payload 에 담아 에이전트 응답에서 출처 링크로 사용.
DART_FILING_URL_TEMPLATE = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 청킹 경계 우선순위 — 자연스러운 단락·문장 경계 선호
CHUNK_SEPARATORS = ("\n\n", "\n", "。 ", ". ", "! ", "? ", " ")

# 결정론적 UUID5 생성용 네임스페이스. 동일 (rcept_no, chunk_idx) → 동일 ID
# 로 재수집 시 중복 방지.
_DART_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class IngestConfig:
    collection: str = DEFAULT_COLLECTION
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    batch_size: int = DEFAULT_BATCH_SIZE
    upsert_batch_size: int = DEFAULT_UPSERT_BATCH_SIZE
    openai_api_key: Optional[str] = None
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    dart_api_key: Optional[str] = None

    def __post_init__(self) -> None:
        """Config 정합성 검증.

        1. upsert_batch_size > 0 (0 또는 음수면 `_batches` 가 range(...,0) 으로
           ValueError 를 던지는 지점까지 가기 전에 fail-fast)
        2. 알려진 OpenAI 임베딩 모델이면 embedding_dim 자동 정합 / 충돌 검증
        """
        if not isinstance(self.upsert_batch_size, int) or self.upsert_batch_size <= 0:
            raise ValueError(
                f"upsert_batch_size 는 양의 정수여야 합니다 (현재: "
                f"{self.upsert_batch_size!r})"
            )

        known = KNOWN_EMBEDDING_DIMS.get(self.embedding_model)
        if known is None:
            return  # 알 수 없는 모델 — 사용자 설정 그대로 사용
        if self.embedding_dim == DEFAULT_EMBEDDING_DIM and known != DEFAULT_EMBEDDING_DIM:
            # 사용자가 기본값을 두고 모델만 변경한 경우 — dim 자동 조정
            self.embedding_dim = known
        elif self.embedding_dim != known:
            # 사용자가 명시한 dim 이 모델 기본값과 다름 — 실수 가능성
            raise ValueError(
                f"embedding_dim={self.embedding_dim} 이 {self.embedding_model} "
                f"의 기본 출력 차원 {known} 과 다릅니다. 모델을 바꾸거나 "
                f"embedding_dim 을 {known} 으로 설정하세요."
            )


@dataclass
class IngestStats:
    corps_processed: int = 0
    disclosures_seen: int = 0
    disclosures_ingested: int = 0
    disclosures_skipped_empty: int = 0
    chunks_uploaded: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "corps_processed": self.corps_processed,
            "disclosures_seen": self.disclosures_seen,
            "disclosures_ingested": self.disclosures_ingested,
            "disclosures_skipped_empty": self.disclosures_skipped_empty,
            "chunks_uploaded": self.chunks_uploaded,
            "failure_count": len(self.failures),
        }


# ----------------------------------------------------------------------
# 텍스트 처리
# ----------------------------------------------------------------------


def clean_dart_text(raw: str | None) -> str:
    """DART 원문에서 script/style 블록 제거, HTML 태그 제거, 엔티티 디코드.

    BeautifulSoup 의존성 추가 대신 정규식 기반으로 처리하되,
    naive 버전 대비 두 가지를 보완:
    1. ``<script>``/``<style>`` 태그와 **내부 텍스트** 까지 제거
    2. ``html.unescape`` 로 named + numeric HTML entity 모두 디코드
    """
    if not raw:
        return ""
    # script/style 블록 자체 제거 (태그 사이 내용 포함)
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 나머지 HTML 태그 제거
    text = re.sub(r"<[^>]+>", " ", text)
    # HTML entity 디코드 (named: &nbsp; numeric: &#123; &#x7B;)
    text = html.unescape(text)
    # 공백 정규화
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """오버랩 기반 청킹 — 가능한 한 문단/문장 경계에서 자름.

    Naive 문자 기반. 토크나이저 의존성 추가 없이 동작하되 한국어 평균
    3~4 chars/token 기준으로 ``chunk_size=500`` 이면 150~250 토큰.
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be in [0, chunk_size)")
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    min_break = chunk_size // 2  # 너무 짧은 청크 방지

    while start < n:
        end = min(start + chunk_size, n)
        if end >= n:
            tail = text[start:].strip()
            if tail:
                chunks.append(tail)
            break

        best_end = end
        # 자연스러운 경계 탐색 (현재 윈도우 하위 절반 구간에서 뒤쪽부터)
        search_lo = start + min_break
        search_hi = end
        for sep in CHUNK_SEPARATORS:
            idx = text.rfind(sep, search_lo, search_hi)
            if idx != -1:
                best_end = idx + len(sep)
                break

        chunk = text[start:best_end].strip()
        if chunk:
            chunks.append(chunk)
        # overlap 을 위해 뒤로 이동
        next_start = best_end - chunk_overlap
        if next_start <= start:
            next_start = best_end  # 진전 없을 때 강제 전진
        start = next_start

    return chunks


# ----------------------------------------------------------------------
# 클라이언트 팩토리
# ----------------------------------------------------------------------


def _get_dart_client(api_key: Optional[str] = None):
    import OpenDartReader

    key = api_key or os.getenv("DART_API_KEY")
    if not key:
        raise RuntimeError(
            "DART_API_KEY 환경변수 누락. https://opendart.fss.or.kr/ 에서 "
            "무료 발급 후 .env 에 추가하세요.",
        )
    return OpenDartReader(key)


def _get_openai_client(api_key: Optional[str] = None):
    from openai import OpenAI

    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY 환경변수 누락")
    return OpenAI(api_key=key)


def _get_qdrant_client(url: Optional[str] = None, api_key: Optional[str] = None):
    from qdrant_client import QdrantClient

    resolved_url = url or os.getenv("QDRANT_URL")
    resolved_key = api_key or os.getenv("QDRANT_API_KEY")
    if not resolved_url:
        raise RuntimeError("QDRANT_URL 환경변수 누락")
    return QdrantClient(
        url=resolved_url,
        api_key=resolved_key,
        prefer_grpc=False,
        timeout=30,
    )


# ----------------------------------------------------------------------
# Qdrant 스키마
# ----------------------------------------------------------------------


def ensure_collection(qclient, collection: str, dim: int) -> bool:
    """컬렉션이 없으면 생성. 생성했으면 True, 이미 있었으면 False."""
    from qdrant_client.http import models as qm

    existing = {c.name for c in qclient.get_collections().collections}
    if collection in existing:
        return False

    qclient.create_collection(
        collection_name=collection,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )

    # 필터링 인덱스 (payload 기반 쿼리 속도)
    index_specs = (
        ("ticker", "keyword"),
        ("corp_name", "keyword"),
        ("filing_type", "keyword"),
        # rcept_no 는 get_filing_chunks 의 equality 필터에 사용 — 인덱스 없이
        # 전체 스캔이면 청크 수가 늘수록 비용 선형 증가.
        ("rcept_no", "keyword"),
        # ISO-8601 (YYYY-MM-DD) 을 datetime 인덱스로 올려 DatetimeRange 필터 지원.
        ("filing_date", "datetime"),
    )
    for field_name, schema in index_specs:
        try:
            qclient.create_payload_index(
                collection_name=collection,
                field_name=field_name,
                field_schema=schema,
            )
        except Exception as e:
            logger.warning("payload index 생성 실패 (%s): %s", field_name, e)

    logger.info("Qdrant 컬렉션 생성 완료: %s (dim=%d)", collection, dim)
    return True


def point_id_for(rcept_no: str, chunk_index: int) -> str:
    """(rcept_no, chunk_index) → UUID5 결정론적 ID 문자열."""
    return str(uuid.uuid5(_DART_NAMESPACE, f"{rcept_no}_{chunk_index}"))


# ----------------------------------------------------------------------
# 임베딩 배치 호출
# ----------------------------------------------------------------------


def embed_batch(
    openai_client,
    texts: list[str],
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[list[float]]:
    if not texts:
        return []
    resp = openai_client.embeddings.create(model=model, input=texts)
    return [d.embedding for d in resp.data]


def _batches(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ----------------------------------------------------------------------
# 본문 수집
# ----------------------------------------------------------------------


def fetch_disclosure_body(dart, rcept_no: str) -> str:
    """OpenDartReader 로 공시 본문 조회 후 정제된 문자열 반환.

    본문 조회 실패나 빈 응답은 빈 문자열을 돌려줘서 호출자가 skip 할 수 있게 한다.
    """
    try:
        doc = dart.document(rcept_no)
    except Exception as e:  # noqa: BLE001
        logger.warning("dart.document 실패 rcept_no=%s: %s", rcept_no, e)
        return ""

    if isinstance(doc, str):
        return clean_dart_text(doc)
    if isinstance(doc, dict):
        joined = "\n\n".join(str(v) for v in doc.values() if v)
        return clean_dart_text(joined)
    if isinstance(doc, (list, tuple)):
        joined = "\n\n".join(str(v) for v in doc if v)
        return clean_dart_text(joined)
    return ""


# ----------------------------------------------------------------------
# 단일 공시 색인
# ----------------------------------------------------------------------


def ingest_disclosure(
    *,
    dart,
    openai_client,
    qclient,
    rcept_no: str,
    corp_name: str,
    ticker: str,
    filing_date: str,
    filing_type: str,
    config: IngestConfig,
) -> int:
    """한 공시를 수집·청킹·임베딩·업로드. 업로드된 청크 수 반환.

    본문이 비어있거나 청킹 결과가 0이면 0을 반환 (skip).
    """
    from qdrant_client.http import models as qm

    body = fetch_disclosure_body(dart, rcept_no)
    if not body:
        return 0
    chunks = chunk_text(body, config.chunk_size, config.chunk_overlap)
    if not chunks:
        return 0

    vectors: list[list[float]] = []
    for batch in _batches(chunks, config.batch_size):
        vectors.extend(embed_batch(openai_client, batch, config.embedding_model))

    source_url = DART_FILING_URL_TEMPLATE.format(rcept_no=rcept_no)
    points = []
    for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
        points.append(
            qm.PointStruct(
                id=point_id_for(rcept_no, idx),
                vector=vec,
                payload={
                    "rcept_no": rcept_no,
                    "corp_name": corp_name,
                    "ticker": ticker,
                    "filing_date": filing_date,
                    "filing_type": filing_type,
                    "chunk_index": idx,
                    "text": chunk,
                    # 에이전트 응답에서 출처 링크로 바로 사용 가능.
                    "source_url": source_url,
                    # 섹션 파싱은 추후 확장. 스키마 고정용 placeholder.
                    "section": None,
                },
            )
        )

    # Qdrant 기본 HTTP payload 한도(32MB)를 넘지 않도록 upsert 도 배치로 쪼갠다.
    # 단일 대형 사업보고서가 수천 청크를 만들 때 payload 가 40MB+ 되어 실패한 사례
    # (Issue #18) 방지.
    total_batches = (
        len(points) + config.upsert_batch_size - 1
    ) // config.upsert_batch_size
    for idx, upsert_chunk in enumerate(_batches(points, config.upsert_batch_size), 1):
        logger.debug(
            "qdrant upsert batch %d/%d (rcept_no=%s, size=%d)",
            idx,
            total_batches,
            rcept_no,
            len(upsert_chunk),
        )
        qclient.upsert(collection_name=config.collection, points=upsert_chunk)
    return len(points)


# ----------------------------------------------------------------------
# 기업 단위 / 전체 수집
# ----------------------------------------------------------------------


def _iter_disclosures(dart, corp: str, start: Optional[str], end: Optional[str], kind: str = ""):
    """corp 에 대한 공시 DataFrame 을 순회하며 필요한 필드만 추출."""
    kwargs: dict[str, Any] = {}
    if start:
        kwargs["start"] = start
    if end:
        kwargs["end"] = end
    if kind:
        kwargs["kind"] = kind

    df = dart.list(corp, **kwargs)
    if df is None:
        return
    try:
        rows = df.to_dict(orient="records")
    except Exception as e:  # noqa: BLE001
        # DataFrame 변환 실패 — DART 응답 포맷 변경 / 비정상 타입일 가능성.
        # 조용히 무시하지 않고 로그 + 스택으로 원인 추적 가능하게.
        logger.exception(
            "dart.list 결과 DataFrame 변환 실패 corp=%s err=%s", corp, e,
        )
        return
    for row in rows:
        yield {
            "rcept_no": str(row.get("rcept_no", "")).strip(),
            "corp_name": str(row.get("corp_name", "") or corp),
            "ticker": str(row.get("stock_code", "") or ""),
            "filing_date": str(row.get("rcept_dt", "") or "")[:10],
            "filing_type": str(row.get("report_nm", "") or ""),
        }


def ingest_corp(
    *,
    dart,
    openai_client,
    qclient,
    corp: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    kind: str = "",
    config: IngestConfig,
    stats: IngestStats,
    max_per_corp: Optional[int] = None,
) -> None:
    """한 기업의 공시 목록을 순회하며 색인.

    ``max_per_corp`` 는 **시도한 공시 수** 상한이다. 본문이 비어서 skip 된
    것도 한도에 포함해, 연속으로 빈 공시가 와도 드라이버가 영원히 돌지 않게
    한다.
    """
    attempts = 0
    for meta in _iter_disclosures(dart, corp, start, end, kind):
        if max_per_corp is not None and attempts >= max_per_corp:
            break
        attempts += 1
        stats.disclosures_seen += 1
        rcept_no = meta["rcept_no"]
        if not rcept_no:
            continue
        try:
            uploaded = ingest_disclosure(
                dart=dart,
                openai_client=openai_client,
                qclient=qclient,
                rcept_no=rcept_no,
                corp_name=meta["corp_name"],
                ticker=meta["ticker"],
                filing_date=meta["filing_date"],
                filing_type=meta["filing_type"],
                config=config,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("ingest 실패 rcept_no=%s", rcept_no)
            stats.failures.append(f"{rcept_no}: {e}")
            continue

        if uploaded == 0:
            stats.disclosures_skipped_empty += 1
        else:
            stats.disclosures_ingested += 1
            stats.chunks_uploaded += uploaded
    stats.corps_processed += 1


def ingest_corpus(
    corps: Iterable[str],
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    kind: str = "A",  # 기본: 정기공시만 (사업·반기·분기보고서)
    config: Optional[IngestConfig] = None,
    max_per_corp: Optional[int] = None,
) -> IngestStats:
    """여러 기업을 순회하며 전체 색인 수행.

    Args:
        corps: 티커 / 회사명 / corp_code 리스트
        start: YYYY-MM-DD
        end: YYYY-MM-DD
        kind: DART 공시 타입 (A=정기, B=주요사항, C=발행 …)
        config: 임베딩 / 청킹 설정 override
        max_per_corp: 기업당 색인 공시 수 상한 (테스트·비용 제한용)
    """
    cfg = config or IngestConfig()
    dart = _get_dart_client(cfg.dart_api_key)
    openai_client = _get_openai_client(cfg.openai_api_key)
    qclient = _get_qdrant_client(cfg.qdrant_url, cfg.qdrant_api_key)

    ensure_collection(qclient, cfg.collection, cfg.embedding_dim)

    stats = IngestStats()
    for corp in corps:
        try:
            ingest_corp(
                dart=dart,
                openai_client=openai_client,
                qclient=qclient,
                corp=corp,
                start=start,
                end=end,
                kind=kind,
                config=cfg,
                stats=stats,
                max_per_corp=max_per_corp,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("ingest_corp 실패 corp=%s", corp)
            stats.failures.append(f"{corp}: {e}")

    logger.info("ingest_corpus 완료: %s", stats.summary())
    return stats


__all__ = [
    "CHUNK_SEPARATORS",
    "DART_FILING_URL_TEMPLATE",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_COLLECTION",
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_UPSERT_BATCH_SIZE",
    "IngestConfig",
    "IngestStats",
    "KNOWN_EMBEDDING_DIMS",
    "chunk_text",
    "clean_dart_text",
    "embed_batch",
    "ensure_collection",
    "fetch_disclosure_body",
    "ingest_corp",
    "ingest_corpus",
    "ingest_disclosure",
    "point_id_for",
]
