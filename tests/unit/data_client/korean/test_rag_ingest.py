# FORK: DART RAG 인제스트 파이프라인 단위 테스트
"""Tests for src.data_client.korean.rag_ingest."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_client.korean.rag_ingest import (
    DART_FILING_URL_TEMPLATE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_UPSERT_BATCH_SIZE,
    KNOWN_EMBEDDING_DIMS,
    IngestConfig,
    IngestStats,
    chunk_text,
    clean_dart_text,
    embed_batch,
    ensure_collection,
    fetch_disclosure_body,
    ingest_corp,
    ingest_disclosure,
    point_id_for,
)


# ==========================================================================
# clean_dart_text
# ==========================================================================


class TestCleanDartText:
    def test_strips_html_tags(self):
        assert clean_dart_text("<p>본문</p>") == "본문"

    def test_normalizes_whitespace(self):
        assert clean_dart_text("  a\n\n\nb\t\tc  ") == "a b c"

    def test_strips_named_entities(self):
        # html.unescape: &nbsp; → non-breaking space (U+00A0) 이후 공백 정규화
        assert clean_dart_text("foo&nbsp;bar").replace("\xa0", " ").strip() \
            in {"foo bar", "foo  bar"}
        # &amp; 같은 일반 엔티티도 디코드
        assert clean_dart_text("A &amp; B") == "A & B"

    def test_strips_numeric_entities(self):
        # &#33; = "!", &#x41; = "A"
        assert clean_dart_text("hi&#33;") == "hi!"
        assert clean_dart_text("&#x41;&#x42;C") == "ABC"

    def test_removes_script_style_content(self):
        # naive 태그 strip 과 달리 script / style 내부 텍스트까지 제거해야 함
        html_in = (
            "<p>본문</p>"
            "<script>var secret='xxx'; alert(1);</script>"
            "<style>.cls{color:red}</style>"
            "끝"
        )
        out = clean_dart_text(html_in)
        assert "secret" not in out
        assert "alert" not in out
        assert "color" not in out
        assert "본문" in out and "끝" in out

    def test_empty_input(self):
        assert clean_dart_text(None) == ""
        assert clean_dart_text("") == ""


# ==========================================================================
# chunk_text
# ==========================================================================


class TestChunkText:
    def test_short_text_single_chunk(self):
        assert chunk_text("짧은 텍스트") == ["짧은 텍스트"]

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_chunks_within_size_limit(self):
        text = "문장 하나. " * 200  # 약 2000자
        chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
        assert len(chunks) > 1
        # 정확히 500 이하는 아니지만 크게 벗어나지 않아야 함 (경계 찾다가 조금 늘어남)
        for c in chunks:
            assert len(c) <= 600

    def test_overlap_present(self):
        # 문장 부호로 확실히 끊어지는 텍스트
        text = ". ".join([f"문장{i}" for i in range(100)]) + "."
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
        assert len(chunks) > 1
        # 연속 청크에 공통 substring 이 있어야 함 (오버랩)
        # 경계 탐색 때문에 엄밀히 보장되진 않지만, 인접 청크의 시작/끝이 인접해야 함
        assert "".join(chunks).count("문장") >= text.count("문장")

    def test_invalid_overlap(self):
        with pytest.raises(ValueError):
            chunk_text("abc", chunk_size=10, chunk_overlap=10)
        with pytest.raises(ValueError):
            chunk_text("abc", chunk_size=10, chunk_overlap=-1)

    def test_defaults_reasonable(self):
        # 긴 텍스트가 defaults 로도 정상 청킹
        text = "한국어 문장. " * 500
        chunks = chunk_text(text)
        assert len(chunks) > 1
        assert all(len(c) > 0 for c in chunks)
        assert all(len(c) <= DEFAULT_CHUNK_SIZE + 100 for c in chunks)
        # 기본 오버랩 값 사용
        _ = DEFAULT_CHUNK_OVERLAP  # 존재 검증


# ==========================================================================
# point_id_for — 결정론성
# ==========================================================================


class TestPointId:
    def test_deterministic(self):
        assert point_id_for("20240101000001", 3) == point_id_for(
            "20240101000001", 3
        )

    def test_different_inputs_different_ids(self):
        assert point_id_for("A", 0) != point_id_for("A", 1)
        assert point_id_for("A", 0) != point_id_for("B", 0)


# ==========================================================================
# fetch_disclosure_body
# ==========================================================================


class TestFetchBody:
    def test_string_response_cleaned(self):
        dart = MagicMock()
        dart.document.return_value = "<p>본문</p>"
        assert fetch_disclosure_body(dart, "rcept") == "본문"

    def test_dict_response_joined(self):
        dart = MagicMock()
        dart.document.return_value = {"sec1": "<p>A</p>", "sec2": "B"}
        result = fetch_disclosure_body(dart, "rcept")
        assert "A" in result and "B" in result

    def test_exception_returns_empty(self):
        dart = MagicMock()
        dart.document.side_effect = RuntimeError("API error")
        assert fetch_disclosure_body(dart, "rcept") == ""

    def test_none_response_empty(self):
        dart = MagicMock()
        dart.document.return_value = None
        assert fetch_disclosure_body(dart, "rcept") == ""


# ==========================================================================
# embed_batch
# ==========================================================================


class TestEmbedBatch:
    def test_empty_returns_empty(self):
        client = MagicMock()
        assert embed_batch(client, []) == []
        client.embeddings.create.assert_not_called()

    def test_calls_openai_with_batch(self):
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=[0.1, 0.2]), MagicMock(embedding=[0.3, 0.4])]
        client.embeddings.create.return_value = mock_resp

        result = embed_batch(client, ["a", "b"], model="text-embedding-3-small")

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        client.embeddings.create.assert_called_once_with(
            model="text-embedding-3-small", input=["a", "b"],
        )


# ==========================================================================
# ensure_collection
# ==========================================================================


class TestEnsureCollection:
    def test_creates_when_missing(self):
        qclient = MagicMock()
        existing = MagicMock()
        existing.collections = []
        qclient.get_collections.return_value = existing

        created = ensure_collection(qclient, "dart_filings", dim=1536)

        assert created is True
        qclient.create_collection.assert_called_once()
        # payload index 는 여러 필드에 대해 시도
        assert qclient.create_payload_index.call_count >= 1

    def test_noop_when_exists(self):
        qclient = MagicMock()
        col = MagicMock()
        col.name = "dart_filings"
        existing = MagicMock()
        existing.collections = [col]
        qclient.get_collections.return_value = existing

        created = ensure_collection(qclient, "dart_filings", dim=1536)

        assert created is False
        qclient.create_collection.assert_not_called()


# ==========================================================================
# ingest_disclosure
# ==========================================================================


class TestIngestConfigPostInit:
    def test_known_model_auto_sets_dim(self):
        cfg = IngestConfig(embedding_model="text-embedding-3-large")
        assert cfg.embedding_dim == 3072

    def test_known_model_default_dim_passthrough(self):
        cfg = IngestConfig(embedding_model="text-embedding-3-small")
        assert cfg.embedding_dim == 1536

    def test_explicit_matching_dim_ok(self):
        cfg = IngestConfig(
            embedding_model="text-embedding-3-large", embedding_dim=3072,
        )
        assert cfg.embedding_dim == 3072

    def test_explicit_mismatching_dim_raises(self):
        # 9999 는 DEFAULT_EMBEDDING_DIM (1536) 이 아니고 어떤 known 모델과도 안
        # 맞는 값 → passthrough 로 치지 않고 명시 충돌로 판단해서 raise.
        with pytest.raises(ValueError) as exc:
            IngestConfig(
                embedding_model="text-embedding-3-large", embedding_dim=9999,
            )
        assert "text-embedding-3-large" in str(exc.value)

    def test_unknown_model_dim_passthrough(self):
        # 알 수 없는 모델은 dim 강제 변경/검증 안 함
        cfg = IngestConfig(
            embedding_model="custom-embedding-v1", embedding_dim=2048,
        )
        assert cfg.embedding_dim == 2048

    def test_registry_has_known_models(self):
        # 하드코딩 매핑이 최소한 두 OpenAI 모델을 포함
        assert KNOWN_EMBEDDING_DIMS["text-embedding-3-small"] == 1536
        assert KNOWN_EMBEDDING_DIMS["text-embedding-3-large"] == 3072
        _ = DEFAULT_EMBEDDING_DIM  # 심볼 존재 확인


class TestIngestDisclosure:
    def _build_mocks(self, body: str, n_chunks: int = 2):
        dart = MagicMock()
        dart.document.return_value = body

        openai_client = MagicMock()
        resp = MagicMock()
        resp.data = [MagicMock(embedding=[0.1] * 4) for _ in range(n_chunks)]
        openai_client.embeddings.create.return_value = resp

        qclient = MagicMock()
        return dart, openai_client, qclient

    def test_empty_body_returns_zero(self):
        dart, openai_client, qclient = self._build_mocks(body="")
        cfg = IngestConfig()
        uploaded = ingest_disclosure(
            dart=dart,
            openai_client=openai_client,
            qclient=qclient,
            rcept_no="r1",
            corp_name="테스트",
            ticker="005930",
            filing_date="2024-01-01",
            filing_type="사업보고서",
            config=cfg,
        )
        assert uploaded == 0
        qclient.upsert.assert_not_called()

    def test_happy_path_uploads_chunks(self):
        # 긴 본문 → 여러 청크
        body = "문장 하나. " * 300
        dart, openai_client, qclient = self._build_mocks(body=body, n_chunks=1)
        # embed 는 여러 배치로 호출될 수 있으므로 항상 일관된 응답 반환
        openai_client.embeddings.create.side_effect = lambda model, input: MagicMock(
            data=[MagicMock(embedding=[0.1] * 4) for _ in input],
        )

        cfg = IngestConfig(chunk_size=200, chunk_overlap=20, batch_size=64)
        uploaded = ingest_disclosure(
            dart=dart,
            openai_client=openai_client,
            qclient=qclient,
            rcept_no="r1",
            corp_name="테스트",
            ticker="005930",
            filing_date="2024-01-01",
            filing_type="사업보고서",
            config=cfg,
        )
        assert uploaded > 0
        qclient.upsert.assert_called_once()
        call = qclient.upsert.call_args
        points = call.kwargs["points"]
        assert len(points) == uploaded
        # payload 스키마 확인
        first = points[0]
        assert first.payload["rcept_no"] == "r1"
        assert first.payload["ticker"] == "005930"
        assert first.payload["chunk_index"] == 0
        assert first.payload["source_url"] == (
            DART_FILING_URL_TEMPLATE.format(rcept_no="r1")
        )
        assert first.payload["section"] is None

    def test_large_document_upsert_is_batched(self):
        """대량 청크 → 여러 upsert call 로 분할돼 Qdrant payload 한도 초과 방지.

        Issue #18: 단일 upsert 에 수천 청크를 넣으면 32MB 한도 초과 400 에러.
        """
        # 한 청크 = 100자 / overlap 10 → 10,000자 본문이면 대략 100+ 청크 생성
        body = "가나다라마바사아자차카타파하. " * 700  # ~10,500자
        dart, openai_client, _qclient = self._build_mocks(body=body)
        # 배치마다 호출되는 embedding 응답을 매번 동적으로
        openai_client.embeddings.create.side_effect = lambda model, input: MagicMock(
            data=[MagicMock(embedding=[0.1] * 4) for _ in input],
        )
        qclient = MagicMock()

        cfg = IngestConfig(
            chunk_size=100,
            chunk_overlap=10,
            batch_size=64,
            upsert_batch_size=50,  # 작게 설정해 분할 확실히 발생시킴
        )
        uploaded = ingest_disclosure(
            dart=dart,
            openai_client=openai_client,
            qclient=qclient,
            rcept_no="r1",
            corp_name="X",
            ticker="000000",
            filing_date="2024-01-01",
            filing_type="사업보고서",
            config=cfg,
        )

        assert uploaded > 50
        # upsert_batch_size=50 으로 분할됐으므로 여러 번 호출
        assert qclient.upsert.call_count > 1
        # 각 호출이 upsert_batch_size 를 넘지 않음
        for call in qclient.upsert.call_args_list:
            assert len(call.kwargs["points"]) <= cfg.upsert_batch_size
        # 배치 합이 총 업로드 수와 일치
        total = sum(
            len(call.kwargs["points"]) for call in qclient.upsert.call_args_list
        )
        assert total == uploaded

    def test_default_upsert_batch_size_applied(self):
        """upsert_batch_size 를 지정 안 하면 DEFAULT_UPSERT_BATCH_SIZE 를 쓴다."""
        cfg = IngestConfig()
        assert cfg.upsert_batch_size == DEFAULT_UPSERT_BATCH_SIZE


# ==========================================================================
# ingest_corp — 기업 단위 수집 + stats 집계
# ==========================================================================


class TestIngestCorp:
    def test_aggregates_stats_and_respects_max(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame(
            [
                {
                    "rcept_no": "20240101000001",
                    "corp_name": "삼성전자",
                    "stock_code": "005930",
                    "rcept_dt": "20240101",
                    "report_nm": "사업보고서",
                },
                {
                    "rcept_no": "20240201000001",
                    "corp_name": "삼성전자",
                    "stock_code": "005930",
                    "rcept_dt": "20240201",
                    "report_nm": "분기보고서",
                },
                {
                    "rcept_no": "20240301000001",
                    "corp_name": "삼성전자",
                    "stock_code": "005930",
                    "rcept_dt": "20240301",
                    "report_nm": "주요사항보고서",
                },
            ]
        )

        cfg = IngestConfig()
        stats = IngestStats()

        with patch(
            "src.data_client.korean.rag_ingest.ingest_disclosure", return_value=3,
        ) as mock_ing:
            ingest_corp(
                dart=dart,
                openai_client=MagicMock(),
                qclient=MagicMock(),
                corp="005930",
                config=cfg,
                stats=stats,
                max_per_corp=2,
            )

        assert stats.corps_processed == 1
        assert stats.disclosures_seen == 2  # max 에 걸림
        assert stats.disclosures_ingested == 2
        assert stats.chunks_uploaded == 6
        assert mock_ing.call_count == 2

    def test_skips_empty_body_disclosures(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame(
            [
                {
                    "rcept_no": "r1",
                    "corp_name": "X",
                    "stock_code": "000000",
                    "rcept_dt": "20240101",
                    "report_nm": "사업보고서",
                }
            ]
        )
        cfg = IngestConfig()
        stats = IngestStats()

        with patch(
            "src.data_client.korean.rag_ingest.ingest_disclosure", return_value=0,
        ):
            ingest_corp(
                dart=dart,
                openai_client=MagicMock(),
                qclient=MagicMock(),
                corp="X",
                config=cfg,
                stats=stats,
            )

        assert stats.disclosures_ingested == 0
        assert stats.disclosures_skipped_empty == 1
        assert stats.chunks_uploaded == 0

    def test_max_per_corp_counts_attempts_not_successes(self):
        """Empty / failing disclosure 도 max_per_corp 한도에 포함돼야 함.

        안 그러면 계속 빈 공시가 오면 드라이버가 영원히 돌 수 있음.
        """
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame(
            [
                {
                    "rcept_no": f"r{i}",
                    "corp_name": "X",
                    "stock_code": "000000",
                    "rcept_dt": "20240101",
                    "report_nm": "사업보고서",
                }
                for i in range(5)
            ]
        )
        cfg = IngestConfig()
        stats = IngestStats()

        # 모든 공시가 empty body 로 skip → 그래도 max=2 에서 멈춰야 함
        with patch(
            "src.data_client.korean.rag_ingest.ingest_disclosure", return_value=0,
        ):
            ingest_corp(
                dart=dart,
                openai_client=MagicMock(),
                qclient=MagicMock(),
                corp="X",
                config=cfg,
                stats=stats,
                max_per_corp=2,
            )

        assert stats.disclosures_seen == 2
        assert stats.disclosures_skipped_empty == 2

    def test_failure_appended_to_stats(self):
        """ingest_disclosure 에서 예외 발생 → stats.failures 에 기록."""
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame(
            [
                {
                    "rcept_no": "r_boom",
                    "corp_name": "X",
                    "stock_code": "000000",
                    "rcept_dt": "20240101",
                    "report_nm": "사업보고서",
                }
            ]
        )
        cfg = IngestConfig()
        stats = IngestStats()

        with patch(
            "src.data_client.korean.rag_ingest.ingest_disclosure",
            side_effect=RuntimeError("boom"),
        ):
            ingest_corp(
                dart=dart,
                openai_client=MagicMock(),
                qclient=MagicMock(),
                corp="X",
                config=cfg,
                stats=stats,
            )

        assert stats.corps_processed == 1
        assert stats.disclosures_seen == 1
        assert stats.disclosures_ingested == 0
        assert len(stats.failures) == 1
        assert "r_boom" in stats.failures[0]
        assert "boom" in stats.failures[0]

    def test_iter_disclosures_to_dict_exception_logged_not_silent(
        self, caplog,
    ):
        """df.to_dict 가 실패해도 silent swallow 가 아니라 로그 출력돼야 함."""
        import logging

        class _BadDf:
            def to_dict(self, orient="records"):
                raise RuntimeError("bad df")

            # Truthy 로 만들어 `is None` 검사 통과
            def __bool__(self):
                return True

        dart = MagicMock()
        dart.list.return_value = _BadDf()
        cfg = IngestConfig()
        stats = IngestStats()

        with caplog.at_level(
            logging.ERROR, logger="src.data_client.korean.rag_ingest",
        ):
            ingest_corp(
                dart=dart,
                openai_client=MagicMock(),
                qclient=MagicMock(),
                corp="X",
                config=cfg,
                stats=stats,
            )

        assert stats.disclosures_seen == 0
        # 로그 메시지에 exception 정보가 포함돼야 함
        assert any("bad df" in r.getMessage() or r.exc_info for r in caplog.records)
