---
name: kr-screener
description: "KOSPI/KOSDAQ 종목 스크리닝: 밸류에이션, 모멘텀, 펀더멘탈 기반 필터링 및 랭킹"
license: "Apache-2.0"
---

# 한국 주식 스크리너

KOSPI/KOSDAQ 전종목 데이터를 활용하여 투자 아이디어를 발굴한다. 밸류에이션, 모멘텀, 펀더멘탈 기준으로 종목을 필터링하고 랭킹한다.

## 트리거

사용자가 다음을 요청할 때:
- "저PER 한국 주식 찾아줘"
- "KOSDAQ에서 고성장 종목 스크리닝"
- "한국 배당주 추천"
- "시가총액 1조 이상 저PBR 종목"
- "KOSPI 종목 중 거래량 급증한 거"

## 도구

- **kr_price MCP**: `get_kr_market_snapshot`, `get_kr_fundamental`, `get_kr_market_cap`, `get_kr_stock_ohlcv`, `search_kr_ticker`
- **kr_dart MCP**: `get_dart_financials` (재무 건전성 검증용)
- **WebSearch**: 업종 뉴스, 테마 확인

## 워크플로우

### Step 1: 스크리닝 기준 정의

사용자에게 확인:
- **시장**: KOSPI, KOSDAQ, 또는 전체
- **시가총액 범위**: 대형(10조+), 중형(1-10조), 소형(1조 이하)
- **스타일**: 가치주, 성장주, 배당주, 모멘텀
- **업종**: 특정 업종 또는 전체
- **추가 조건**: 사용자 맞춤 필터

### Step 2: 전종목 데이터 수집

```python
from tools.kr_price import get_kr_market_snapshot, get_kr_fundamental

# 전종목 당일 시세
snapshot = get_kr_market_snapshot(date="20240423", market="KOSPI")

# 전종목 펀더멘탈 (PER, PBR, DIV)
# 주의: 전종목 조회 시 date 파라미터만 사용 (from_date = to_date)
```

### Step 3: 필터 적용

**가치주 스크린**:
- PER < 10 (또는 업종 중앙값 이하)
- PBR < 1.0
- 배당수익률(DIV) > 3%
- 시가총액 > 5000억

**성장주 스크린**:
- 최근 분기 매출 성장률 > 15% YoY (DART 재무제표 활용)
- 영업이익 흑자 전환 또는 성장 가속
- 시가총액 > 1000억

**배당주 스크린**:
- DIV > 4%
- 3년 연속 배당 유지/증가
- PER < 15

**모멘텀 스크린**:
- 20일 거래량 평균 대비 급증 (2배 이상)
- 최근 1개월 수익률 상위
- 신고가 근접

### Step 4: 랭킹 및 정렬

pandas DataFrame으로 결과를 정리하고 복합 점수로 랭킹:

```python
import pandas as pd

# 예시: 가치주 복합 점수
# PER 역수 + PBR 역수 + DIV
df['value_score'] = (1/df['per']) + (1/df['pbr']) + df['div']
df_ranked = df.sort_values('value_score', ascending=False).head(20)
```

### Step 5: 상위 종목 심화 분석

상위 5-10개 종목에 대해:
1. DART 재무제표로 재무 건전성 확인
2. 최근 6개월 주가 차트
3. 대량보유 변동 확인
4. 최근 뉴스/공시 확인

### Step 6: 결과 출력

형식:
1. **스크리닝 요약 테이블**: 상위 20개 종목 (티커, 종목명, 시가총액, PER, PBR, DIV, 점수)
2. **상위 종목 카드**: 각 종목별 1-2문장 요약
3. **차트**: 상위 종목 주가 추이 비교 차트
4. **주의사항**: 스크리닝 한계점 (과거 데이터 기반, 정성적 판단 필요)

## 스크리닝 팁

- 전종목 데이터(`get_kr_market_snapshot`)는 하루 1회만 호출하고 결과를 재사용
- PER 0 또는 음수는 적자 기업 — 필터에서 제외하거나 별도 분류
- KOSDAQ은 KOSPI 대비 유동성 낮은 종목 많음 — 거래대금 하한 필터 권장
- 업종 분류는 pykrx에서 직접 제공하지 않으므로, DART 기업정보의 induty_code 활용
