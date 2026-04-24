---
name: dart-semantic-search
description: "DART 공시 의미 검색 — Qdrant 벡터 DB 에 색인된 한국 공시 본문을 자연어 쿼리로 검색해 관련 청크 리콜"
license: "Apache-2.0"
---

# DART 공시 의미 검색

DART 전자공시 본문에 대해 **의미 기반(semantic) 검색**을 수행한다.
키워드 일치가 아니라 문맥·의미 유사도로 관련 공시 청크를 리콜한다.

## 언제 이 스킬을 쓰는가

아래 유형의 질문에 적합:

- "**최근** XX 업종에 관한 **회사채 발행** 이슈"
- "삼성전자 **메모리 반도체 수요 전망**"
- "**ESG 관련 위험** 언급된 공시"
- "특정 회사의 **지배구조 변경** 관련 공시"
- 사용자가 정확한 공시 종류/제목을 모르고 **주제/의미**로만 물을 때

## 언제 안 쓰는가 (기존 DART 도구 사용)

- 특정 회사의 **공시 목록 전체** 조회 → `get_dart_disclosures` (kr_dart)
- 재무제표 원본 수치 → `get_dart_financials` (kr_dart)
- **대량보유 변동** 추적 → `get_dart_major_shareholders` (kr_dart)
- corp_code / stock_code 조회 → `search_dart_corp` (kr_dart)

## 도구

- **kr_rag MCP**:
  - `search_korean_filings(query, ticker?, corp_name?, filing_type?, date_from?, date_to?, top_k?)` — 주력
  - `get_filing_chunks(rcept_no, limit?)` — 단일 공시 전체 청크 순서대로 조회
- **kr_dart MCP**: 보조 — 의미 검색으로 찾은 rcept_no 의 메타데이터를 재확인하거나 재무제표 등 구조화 데이터를 병행할 때

## 워크플로우

### Step 1: 쿼리 설계

사용자 질문을 **도메인 용어 + 맥락 키워드** 조합으로 재구성.

- 나쁨: `"삼성전자 좋은 일"` (모호)
- 좋음: `"삼성전자 메모리 반도체 실적 개선 영업이익 증가"`

### Step 2: 의미 검색 실행

```python
result = search_korean_filings(
    query="반도체 업황 부진으로 인한 실적 악화",
    ticker="005930",          # 선택: 특정 기업으로 좁힐 때
    date_from="2024-01-01",   # 선택: 기간 필터
    top_k=10,
)
```

반환: 유사도 점수 높은 순으로 청크 리스트.

### Step 3: 결과 평가 및 드릴다운

- 점수 0.75 이상: 거의 확실한 관련 청크 → 답변에 인용
- 점수 0.6 ~ 0.75: 주변부 — 원문 보강 확인 후 판단
- 점수 0.6 미만: 잘못 매칭된 가능성 ↑ → 쿼리 재작성

> **점수 기준 주의**: 위 수치는 `text-embedding-3-small` 기준 초기 권장값.
> 모델 / 도메인 / 언어에 따라 적절한 임계가 달라지므로, 실제 운영 시 상위
> K 결과의 점수 분포를 관찰하며 임계값을 조정할 것. 임베딩 모델을 교체한
> 경우 반드시 재측정.

특정 공시의 **전체 맥락**이 필요하면:

```python
chunks = get_filing_chunks(rcept_no="20240814000123")
# chunk_index 순서대로 원문 전체를 재구성
```

### Step 4: 답변 생성 (출처 명시)

모든 답변은 **출처 링크**를 포함한다:

- `rcept_no` → DART 공시 URL: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`
- `filing_date`, `filing_type`, `corp_name` 을 함께 표기

## 품질 체크리스트

- [ ] 검색 결과 Top 5 중 3개 이상이 실제 관련인가? (아니면 쿼리 재설계)
- [ ] 답변에 인용된 청크의 `rcept_no` 와 `filing_date` 가 명시됐는가?
- [ ] 하나의 공시에 과도하게 의존하지 않았는가? (복수 공시 교차 확인 권장)

## 한계와 보완

- 색인 대상: 정기공시 + 주요사항보고서 중심. **모든 공시가 색인돼 있지 않음**
- 최신성: 색인 크론 주기에 따라 24시간 지연 가능 → 실시간성이 중요하면
  `get_dart_disclosures` 로 당일 공시 목록을 병행 조회
- 임베딩 품질: 일반 한국어 임베딩 사용 — 금융/회계 전문 용어의 뉘앙스가
  완전히 반영되지 않을 수 있음. 중요 의사결정엔 반드시 원문 검토
- **payload 스키마 제약** (`src/data_client/korean/rag_ingest.py` 인제스트 기준):
  - `source_url` 은 `rcept_no` 로부터 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`
    템플릿으로 계산된 값. DART 원문 URL 이 바뀌면 재색인 필요.
  - `section` 은 현재 `None` 으로 고정 — 공시 문서 내부 섹션(목차 레벨)은
    아직 파싱하지 않는다. 문서 레벨 보다 좁은 단위가 필요하면 `get_filing_chunks`
    로 전체 청크를 받아 `chunk_index` 근접 항목을 묶어 사용.

## 출력 예시

사용자: "현대차 전기차 사업 관련 최근 리스크 언급"

답변:
> 현대차 전기차 사업 관련 공시에서 다음 리스크가 언급됩니다:
>
> 1. **원가 상승 압력** (2024년 반기보고서, rcept_no 20240814000xxx)
>    - 원자재 가격 변동에 따른 수익성 변동 가능성 …
>
> 2. **보조금 정책 변화** (2024년 3분기 보고서, rcept_no 20241114000xxx)
>    - 주요 수출국의 전기차 보조금 축소 가능성 …
>
> 자세한 맥락은 각 rcept_no 의 원문 확인 권장.
