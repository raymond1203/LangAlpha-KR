---
name: kr-dcf-model
description: "K-IFRS 기반 DCF 밸류에이션: DART 재무제표, 한은 기준금리, 한국 시장 리스크 프리미엄으로 한국 기업 적정가치 산출"
license: "Apache-2.0"
---

# 한국 기업 DCF 모델

한국 기업 맞춤 DCF(Discounted Cash Flow) 밸류에이션을 수행한다. K-IFRS 재무제표, 한국은행 기준금리, 한국 시장 리스크 프리미엄을 사용한다.

## 트리거

사용자가 다음을 요청할 때:
- "삼성전자 DCF 밸류에이션"
- "SK하이닉스 적정주가 산출"
- "카카오 내재가치 분석"
- "한국 기업 DCF 모델 만들어줘"

## 도구

- **kr_dart MCP**: `get_dart_financials`, `get_dart_financials_all`, `search_dart_corp`
- **kr_price MCP**: `get_kr_stock_ohlcv`, `get_kr_market_cap`, `get_kr_fundamental`
- **WebSearch / WebFetch**: 한국은행 기준금리, 시장 리스크 프리미엄, 베타 자료
- **xlsx 스킬**: Excel 모델 생성 (openpyxl)

## 한국 시장 DCF 특수 사항

### WACC 구성요소 (한국 기준)

| 항목 | 미국 DCF | 한국 DCF |
|------|---------|---------|
| 무위험수익률 | US 10Y Treasury | 한국 국고채 10년물 (또는 한은 기준금리 + 스프레드) |
| 시장 리스크 프리미엄 | S&P 500 ERP (약 5-6%) | KOSPI ERP (약 6-8%, Damodaran 한국 데이터) |
| 베타 | Yahoo Finance / Bloomberg | KRX 기반 또는 FnGuide/CompanyGuide |
| 부채비용 | Company's bond yield | 회사채 수익률 (AA-/A+ 등급별) |
| 법인세율 | 21% (US federal) | 22-27.5% (한국 법인세 누진세율) |

### K-IFRS 재무제표 주의사항

- **연결/별도 구분**: K-IFRS는 연결재무제표가 기본. 별도 재무제표도 공시됨.
- **계정과목명**: `매출액`, `영업이익`, `당기순이익` 등 한글 계정과목
- **통화**: 원화(KRW) 단위. 대형주는 조 단위.

## 워크플로우

### Step 1: 데이터 수집

```python
from tools.kr_dart import search_dart_corp, get_dart_financials, get_dart_financials_all
from tools.kr_price import get_kr_stock_ohlcv, get_kr_market_cap, get_kr_fundamental

# 1. 기업 식별
corp = search_dart_corp(query="삼성전자")

# 2. K-IFRS 재무제표 (최근 3-5년)
fs_2023 = get_dart_financials(corp="005930", year=2023)
fs_2022 = get_dart_financials(corp="005930", year=2022)
fs_2021 = get_dart_financials(corp="005930", year=2021)

# 3. 전체 재무제표 (상세 항목)
fs_all = get_dart_financials_all(corp="005930", year=2023)

# 4. 시가총액 및 밸류에이션
mktcap = get_kr_market_cap(ticker="005930", from_date="2024-01-01", to_date="2024-12-31")
fund = get_kr_fundamental(ticker="005930", from_date="2024-01-01", to_date="2024-12-31")
```

### Step 2: 과거 분석 (3-5년)

- 매출 성장률 추이
- 영업이익률 추이
- CAPEX / 감가상각 비율
- 운전자본 변동
- FCFF(Free Cash Flow to Firm) 산출

### Step 3: WACC 산출

1. **무위험수익률**: 한국 국고채 10년물 (웹 검색으로 최신 수치 확보)
2. **시장 리스크 프리미엄**: Damodaran 한국 ERP 또는 KOSPI 장기 평균 초과수익률
3. **베타**: KRX 일별 수익률 기반 KOSPI 대비 베타 (kr_price OHLCV 데이터로 직접 계산)
4. **자기자본비용**: CAPM = Rf + β × ERP
5. **부채비용**: 이자비용 / 차입금 (재무제표에서 추출)
6. **법인세율**: 한국 법인세 실효세율 (재무제표에서 추출 또는 22-27.5%)
7. **WACC**: E/(D+E) × Ke + D/(D+E) × Kd × (1-t)

### Step 4: FCF 추정 (5-10년)

- 매출 성장률: 애널리스트 컨센서스 + 업종 성장률
- 영업이익률: 과거 추세 + 구조적 변화 반영
- CAPEX: 과거 CAPEX/매출 비율 기반
- 운전자본: 과거 WC/매출 비율 기반
- FCFF = NOPAT + D&A - CAPEX - ΔWC

### Step 5: 터미널 밸류

- 영구성장률: 한국 장기 GDP 성장률 (2-3%)
- Gordon Growth: TV = FCF × (1+g) / (WACC-g)
- Exit Multiple 교차 검증: EV/EBITDA 멀티플 기반

### Step 6: 밸류에이션

- Enterprise Value = PV(FCF) + PV(TV)
- Equity Value = EV - Net Debt
- 주당 적정가치 = Equity Value / 발행주식수
- 현재 주가 대비 업사이드/다운사이드

### Step 7: 민감도 분석

- WACC vs 영구성장률 매트릭스
- 매출성장률 vs 영업이익률 매트릭스
- Bear / Base / Bull 시나리오

### Step 8: Excel 모델 생성

`skills/xlsx/` 스킬의 openpyxl 패턴으로 Excel 워크북 생성:
- Sheet 1: Assumptions & Inputs
- Sheet 2: Historical Financials
- Sheet 3: Projections
- Sheet 4: DCF Valuation + Sensitivity
- Sheet 5: Football Field (밸류에이션 범위 시각화)

## 주의사항

- K-IFRS 연결재무제표를 기본으로 사용 (별도 재무제표와 혼동 금지)
- 원화 단위 주의: 대형주는 조 단위, 중소형주는 억 단위
- DART 재무제표의 `thstrm_amount`(당기), `frmtrm_amount`(전기), `bfefrmtrm_amount`(전전기) 구분
- 한국 법인세는 누진세율: 2억 이하 10%, 200억 이하 20%, 3000억 이하 22%, 초과 25% (+ 지방세 10%)
