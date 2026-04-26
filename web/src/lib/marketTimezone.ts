/**
 * Symbol → market timezone / TradingView symbol mapping.
 *
 * FORK (#33): MarketView 의 timezone / symbol 처리가 US 만 가정해 한국 차트 (.KS/.KQ)
 * 를 보면 NYSE 시간으로 잘못 표시되던 문제. backend 의 symbol_timezone 패턴을
 * frontend 에 mirror.
 */

const US_TZ = 'America/New_York';
const KR_TZ = 'Asia/Seoul';

/**
 * Derive IANA timezone from symbol suffix.
 *
 * - `.KS` (KOSPI) / `.KQ` (KOSDAQ) → `Asia/Seoul`
 * - bare ticker, `.US` → `America/New_York`
 * - 그 외 (HK / JP / EU 등) → `America/New_York` (현재 MarketView 미지원, 향후 확장 시 추가)
 */
export function getTimezoneForSymbol(symbol: string | null | undefined): string {
  if (!symbol) return US_TZ;
  if (/\.(KS|KQ)$/i.test(symbol)) return KR_TZ;
  return US_TZ;
}

/**
 * Convert a Yahoo-style symbol to a TradingView-compatible symbol.
 *
 * TradingView 의 한국 시장은 KOSPI/KOSDAQ 모두 `KRX` exchange 로 통합
 * (예: `005930.KS` → `KRX:005930`). `.KS`/`.KQ` suffix 는 TradingView 가 직접
 * 인식하지 못해 KRX prefix 변환이 필요.
 *
 * US 심볼 (bare ticker) 은 그대로 — TradingView 가 자체 routing.
 */
export function toTradingViewSymbol(symbol: string | null | undefined): string {
  if (!symbol) return '';
  const match = symbol.match(/^(.+)\.(KS|KQ)$/i);
  if (match) return `KRX:${match[1]}`;
  return symbol;
}
