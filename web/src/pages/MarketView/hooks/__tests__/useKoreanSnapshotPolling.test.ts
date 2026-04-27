/**
 * Unit tests for KR market hours guard inside useKoreanSnapshotPolling.
 *
 * 본 테스트는 hook 자체 (React Query 통합) 가 아닌 isKRMarketOpen 시간 검사 logic
 * 만 검증. hook 동작 (refetch / disabled) 은 useDashboardData 와 동일 React Query
 * 패턴이라 별도 통합 테스트 불필요.
 */
import { describe, it, expect } from 'vitest';
import { __test_isKRMarketOpen } from '../useKoreanSnapshotPolling';

function utcDateForKST(year: number, month: number, day: number, hour: number, minute = 0): Date {
  // KST = UTC+9 → UTC time = KST - 9h
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute));
}

describe('isKRMarketOpen (KST 09:00–15:30 weekdays)', () => {
  it('open at 09:00 weekday', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 27, 9, 0))).toBe(true); // Mon
  });

  it('open at 15:30 weekday (boundary inclusive)', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 27, 15, 30))).toBe(true);
  });

  it('closed at 08:59 weekday (just before open)', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 27, 8, 59))).toBe(false);
  });

  it('closed at 15:31 weekday (just after close)', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 27, 15, 31))).toBe(false);
  });

  it('closed on Saturday during market hours', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 25, 11, 0))).toBe(false); // Sat
  });

  it('closed on Sunday during market hours', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 26, 11, 0))).toBe(false); // Sun
  });

  it('closed at midnight weekday', () => {
    expect(__test_isKRMarketOpen(utcDateForKST(2026, 4, 27, 0, 0))).toBe(false);
  });
});
