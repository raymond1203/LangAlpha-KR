---
name: dart-analysis
description: "DART 전자공시 분석: 공시 목록 조회, 주요사항보고서 해석, 대량보유 변동 추적, 투자 시사점 도출"
license: "Apache-2.0"
---

# DART 공시 분석

DART 전자공시 데이터를 활용하여 한국 기업의 공시 내역을 분석하고 투자 시사점을 도출한다.

## 트리거

사용자가 다음을 요청할 때:
- "삼성전자 최근 공시 분석해줘"
- "SK하이닉스 대량보유 변동 확인"
- "카카오 주요사항보고서 요약"
- "현대차 사업보고서 핵심 내용"
- "최근 공시에서 중요한 변동 사항"

## 도구

- **kr_dart MCP**: `search_dart_corp`, `get_dart_disclosures`, `get_dart_major_shareholders`, `get_dart_company_info`, `get_dart_financials`
- **kr_price MCP**: `search_kr_ticker`, `get_kr_stock_ohlcv` (공시 전후 주가 반응 분석)
- **WebSearch / WebFetch**: 뉴스, 애널리스트 코멘트 보조

## 워크플로우

### Step 1: 기업 식별

```python
from tools.kr_dart import search_dart_corp
result = search_dart_corp(query="삼성전자")
# corp_code, stock_code 확인
```

### Step 2: 공시 목록 조회

```python
from tools.kr_dart import get_dart_disclosures

# 전체 공시
all_disclosures = get_dart_disclosures(corp="005930", start="2024-01-01")

# 종류별 필터
regular = get_dart_disclosures(corp="005930", kind="A")  # 정기공시
major = get_dart_disclosures(corp="005930", kind="B")    # 주요사항
equity = get_dart_disclosures(corp="005930", kind="D")   # 지분공시
```

### Step 3: 핵심 공시 분석

**정기공시 (kind="A")**: 사업보고서, 분기보고서
- 재무제표 핵심 지표 변동 (매출, 영업이익, 순이익)
- 전년 동기 대비 증감률
- 애널리스트 컨센서스 대비 서프라이즈

**주요사항공시 (kind="B")**: 유상증자, 전환사채, 합병, 분할 등
- 기존 주주 희석 영향
- 자금 사용 목적
- 주가 영향 방향성

**지분공시 (kind="D")**: 대량보유, 임원 매매
- 5% 룰 변동 (신규/변경/해소)
- 기관투자자 포지션 변화
- 내부자 매매 패턴

### Step 4: 대량보유 분석

```python
from tools.kr_dart import get_dart_major_shareholders
shareholders = get_dart_major_shareholders(corp="삼성전자")
```

분석 포인트:
- 국민연금, 외국인 투자자 등 주요 기관의 지분 변동
- 신규 5% 보고 (적극적 매수 신호)
- 보유 목적 변경 (단순투자 → 경영참여)

### Step 5: 공시 전후 주가 반응

```python
from tools.kr_price import get_kr_stock_ohlcv
# 공시일 전후 10거래일 주가 확인
ohlcv = get_kr_stock_ohlcv(ticker="005930", from_date="...", to_date="...")
```

### Step 6: 투자 시사점 종합

출력 형식:
1. **공시 요약 테이블**: 최근 주요 공시 목록 (날짜, 종류, 제목)
2. **핵심 변동 사항**: 투자 판단에 영향을 미치는 공시 분석
3. **대량보유 현황**: 주요 주주 변동 추이
4. **주가 반응**: 공시 전후 주가 변동 차트
5. **투자 시사점**: 긍정/부정/중립 판단과 근거

## 주의사항

- DART 공시는 법적 서류이므로 정확한 인용 필요
- 공시 지연(공시일과 실제 이벤트 날짜 차이) 주의
- 일일 API 한도 10,000건 — 불필요한 반복 호출 자제
