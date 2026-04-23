---
name: kr-earnings-analysis
description: "K-IFRS 기반 실적 분석: 분기 실적 서프라이즈, 컨센서스 비교, 재무제표 트렌드, 투자 판단"
license: "Apache-2.0"
---

# 한국 기업 실적 분석

한국 기업의 분기/연간 실적을 K-IFRS 재무제표 기반으로 분석한다. DART 공시 데이터와 시장 데이터를 결합하여 실적 서프라이즈, 트렌드, 투자 시사점을 도출한다.

## 트리거

사용자가 다음을 요청할 때:
- "삼성전자 2024년 3분기 실적 분석"
- "SK하이닉스 분기 실적 어때?"
- "카카오 연간 실적 트렌드"
- "현대차 실적 서프라이즈 분석"
- "NAVER 매출/영업이익 추이"

## 도구

- **kr_dart MCP**: `get_dart_financials`, `get_dart_financials_all`, `search_dart_corp`, `get_dart_disclosures`
- **kr_price MCP**: `get_kr_stock_ohlcv`, `get_kr_fundamental`, `get_kr_market_cap`
- **WebSearch / WebFetch**: 애널리스트 컨센서스, 실적 발표 뉴스
- **xlsx 스킬**: Excel 모델 생성 (선택)

## 워크플로우

### Step 1: 기업 식별 및 데이터 수집

```python
from tools.kr_dart import search_dart_corp, get_dart_financials
from tools.kr_price import get_kr_stock_ohlcv, get_kr_fundamental

# 기업 식별
corp = search_dart_corp(query="삼성전자")

# 최근 4분기 + 직전 연도 재무제표
q1 = get_dart_financials(corp="005930", year=2024, reprt_code="11013")  # 1Q
q2 = get_dart_financials(corp="005930", year=2024, reprt_code="11012")  # 반기
q3 = get_dart_financials(corp="005930", year=2024, reprt_code="11014")  # 3Q
annual = get_dart_financials(corp="005930", year=2023)                   # 사업보고서

# 전체 재무제표 (상세 분석 필요 시)
fs_all = get_dart_financials_all(corp="005930", year=2024, reprt_code="11014")
```

### Step 2: 핵심 지표 추출

K-IFRS 재무제표에서 추출할 핵심 항목:

**손익계산서**:
- 매출액 (Revenue)
- 영업이익 (Operating Profit)
- 당기순이익 (Net Income)
- 영업이익률 (OPM)
- 순이익률 (NPM)

**재무상태표**:
- 총자산
- 총부채
- 자본총계
- 부채비율

**현금흐름표**:
- 영업활동 현금흐름
- 투자활동 현금흐름
- CAPEX

### Step 3: QoQ / YoY 비교

| 항목 | 당분기 | 전분기(QoQ) | 전년동기(YoY) |
|------|--------|------------|--------------|
| 매출액 | xxx | +x% | +x% |
| 영업이익 | xxx | +x% | +x% |
| 영업이익률 | x% | +xpp | +xpp |

- **QoQ**(분기 대비): 계절성 제거 어려움, 트렌드 확인용
- **YoY**(전년 동기 대비): 계절성 제거, 성장성 판단의 핵심

### Step 4: 컨센서스 비교 (서프라이즈 분석)

웹 검색으로 애널리스트 컨센서스 확보:
- FnGuide, CompanyGuide, 네이버 금융 등에서 컨센서스 데이터
- 실적 발표 전 시장 기대치 vs 실제 실적

```
서프라이즈율 = (실적 - 컨센서스) / 컨센서스 × 100
```

- **Positive Surprise** (>3%): 실적이 기대 초과
- **In-line** (-3% ~ +3%): 기대 부합
- **Negative Surprise** (<-3%): 실적이 기대 미달

### Step 5: 실적 발표 전후 주가 반응

```python
from tools.kr_price import get_kr_stock_ohlcv

# 실적 발표일 전후 20거래일 주가
ohlcv = get_kr_stock_ohlcv(
    ticker="005930",
    from_date="...",  # 발표일 -20일
    to_date="...",    # 발표일 +20일
)
```

분석:
- 발표 당일 등락률
- 발표 후 5일 누적 수익률 (단기 반응)
- 발표 전 drift (정보 유출 가능성)

### Step 6: 트렌드 분석

최근 8분기(2년) 데이터로:
- 매출 성장률 추이 (가속/감속)
- 영업이익률 추이 (확대/축소)
- 분기별 계절성 패턴
- 전체 재무제표에서 세그먼트별 매출 분해 (가능한 경우)

### Step 7: 투자 판단

출력 형식:
1. **실적 요약 테이블**: 핵심 지표 QoQ/YoY 비교
2. **서프라이즈 분석**: 컨센서스 대비 서프라이즈율
3. **트렌드 차트**: 분기별 매출/영업이익 추이 (matplotlib)
4. **주가 반응 차트**: 실적 발표 전후 주가
5. **투자 시사점**: 긍정/부정 요인, 향후 전망

## 주의사항

- K-IFRS 연결재무제표를 기본으로 사용
- DART reprt_code 구분: `11013`(1Q), `11012`(반기누적), `11014`(3Q누적), `11011`(연간)
  - 반기/3Q는 **누적** 수치 — 해당 분기만의 수치는 직접 차감 계산 필요
  - 예: 2Q 단독 = 반기누적 - 1Q
- 실적 발표 시점과 DART 공시 시점은 다를 수 있음 (잠정실적 → 확정실적)
- 애널리스트 컨센서스는 웹 검색에 의존하므로 정확도 한계 있음
