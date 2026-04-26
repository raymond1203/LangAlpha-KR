import { describe, it, expect } from 'vitest';
import { getTimezoneForSymbol, toTradingViewSymbol } from '../marketTimezone';

describe('getTimezoneForSymbol', () => {
  it('returns Asia/Seoul for .KS (KOSPI)', () => {
    expect(getTimezoneForSymbol('005930.KS')).toBe('Asia/Seoul');
  });

  it('returns Asia/Seoul for .KQ (KOSDAQ)', () => {
    expect(getTimezoneForSymbol('263750.KQ')).toBe('Asia/Seoul');
  });

  it('case-insensitive on suffix', () => {
    expect(getTimezoneForSymbol('005930.ks')).toBe('Asia/Seoul');
    expect(getTimezoneForSymbol('263750.kq')).toBe('Asia/Seoul');
  });

  it('returns America/New_York for bare US ticker', () => {
    expect(getTimezoneForSymbol('GOOGL')).toBe('America/New_York');
    expect(getTimezoneForSymbol('AAPL')).toBe('America/New_York');
  });

  it('returns America/New_York for empty/null/undefined', () => {
    expect(getTimezoneForSymbol('')).toBe('America/New_York');
    expect(getTimezoneForSymbol(null)).toBe('America/New_York');
    expect(getTimezoneForSymbol(undefined)).toBe('America/New_York');
  });

  it('returns America/New_York for unknown suffix (fallback)', () => {
    // 향후 .HK / .T 추가 시 본 케이스 갱신
    expect(getTimezoneForSymbol('0700.HK')).toBe('America/New_York');
  });
});

describe('toTradingViewSymbol', () => {
  it('maps .KS to KRX prefix', () => {
    expect(toTradingViewSymbol('005930.KS')).toBe('KRX:005930');
  });

  it('maps .KQ to KRX prefix (KOSDAQ also uses KRX exchange)', () => {
    expect(toTradingViewSymbol('263750.KQ')).toBe('KRX:263750');
  });

  it('case-insensitive on suffix', () => {
    expect(toTradingViewSymbol('005930.ks')).toBe('KRX:005930');
  });

  it('passes US symbols through unchanged', () => {
    expect(toTradingViewSymbol('GOOGL')).toBe('GOOGL');
    expect(toTradingViewSymbol('AAPL')).toBe('AAPL');
  });

  it('returns empty string for null/undefined', () => {
    expect(toTradingViewSymbol(null)).toBe('');
    expect(toTradingViewSymbol(undefined)).toBe('');
  });

  it('passes unknown suffixes through (TradingView 가 자체 routing 시도)', () => {
    expect(toTradingViewSymbol('0700.HK')).toBe('0700.HK');
  });
});
