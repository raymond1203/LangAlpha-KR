import { describe, it, expect } from 'vitest';
import {
  cn,
  utcMsToChartSec,
  utcMsToETDate,
  utcMsToETTime,
  utcMsToTzDate,
  utcMsToTzTime,
} from '../utils';

describe('cn', () => {
  it('merges simple class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar');
  });

  it('handles conditional classes via clsx syntax', () => {
    // eslint-disable-next-line no-constant-binary-expression
    expect(cn('base', false && 'hidden', 'visible')).toBe('base visible');
  });

  it('merges tailwind conflicting classes (last wins)', () => {
    // twMerge should resolve conflicts
    const result = cn('px-2 py-1', 'px-4');
    expect(result).toContain('px-4');
    expect(result).not.toContain('px-2');
  });

  it('returns empty string for no inputs', () => {
    expect(cn()).toBe('');
  });

  it('handles undefined and null inputs gracefully', () => {
    expect(cn('a', undefined, null, 'b')).toBe('a b');
  });
});

describe('utcMsToChartSec', () => {
  it('returns 0 for null input', () => {
    expect(utcMsToChartSec(null)).toBe(0);
  });

  it('returns 0 for NaN input', () => {
    expect(utcMsToChartSec(NaN)).toBe(0);
  });

  it('returns 0 for undefined input', () => {
    expect(utcMsToChartSec(undefined)).toBe(0);
  });

  it('returns a numeric seconds value for a valid UTC ms timestamp', () => {
    // 2024-01-15 12:00:00 UTC = 1705320000000 ms
    const utcMs = 1705320000000;
    const result = utcMsToChartSec(utcMs);
    expect(typeof result).toBe('number');
    expect(result).toBeGreaterThan(0);
    // Result should be in seconds (roughly same order of magnitude as Unix seconds)
    expect(result).toBeGreaterThan(1_700_000_000);
    expect(result).toBeLessThan(1_800_000_000);
  });
});

describe('utcMsToETDate', () => {
  it('converts a UTC timestamp to ET date string in YYYY-MM-DD format', () => {
    // 2024-06-15 20:00:00 UTC is 2024-06-15 16:00:00 ET (still same day)
    const utcMs = Date.UTC(2024, 5, 15, 20, 0, 0); // June 15, 2024 20:00 UTC
    const result = utcMsToETDate(utcMs);
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('utcMsToETTime', () => {
  it('returns a time string in HH:MM 24h format', () => {
    const utcMs = Date.UTC(2024, 5, 15, 18, 30, 0); // June 15, 2024 18:30 UTC
    const result = utcMsToETTime(utcMs);
    expect(result).toMatch(/^\d{2}:\d{2}$/);
  });
});

describe('utcMsToTzDate', () => {
  it('returns YYYY-MM-DD in the given timezone (Asia/Seoul)', () => {
    // 2024-06-15 18:00 UTC → KST 03:00 다음날 (KST = UTC+9)
    const utcMs = Date.UTC(2024, 5, 15, 18, 0, 0);
    expect(utcMsToTzDate(utcMs, 'Asia/Seoul')).toBe('2024-06-16');
  });

  it('matches utcMsToETDate when given America/New_York', () => {
    const utcMs = Date.UTC(2024, 5, 15, 20, 0, 0);
    expect(utcMsToTzDate(utcMs, 'America/New_York')).toBe(utcMsToETDate(utcMs));
  });
});

describe('utcMsToTzTime', () => {
  it('returns HH:MM 24h in the given timezone (Asia/Seoul)', () => {
    // 2024-06-15 03:00 UTC → KST 12:00 (UTC+9, no DST)
    const utcMs = Date.UTC(2024, 5, 15, 3, 0, 0);
    expect(utcMsToTzTime(utcMs, 'Asia/Seoul')).toBe('12:00');
  });

  it('correctly identifies KRX regular hours (KST 09:00-15:30) for a June timestamp', () => {
    // 2024-06-17 (월) 00:00 UTC = 09:00 KST → 정규장 시작
    const openMs = Date.UTC(2024, 5, 17, 0, 0, 0);
    expect(utcMsToTzTime(openMs, 'Asia/Seoul')).toBe('09:00');
    // 06:30 UTC = 15:30 KST → 정규장 종료
    const closeMs = Date.UTC(2024, 5, 17, 6, 30, 0);
    expect(utcMsToTzTime(closeMs, 'Asia/Seoul')).toBe('15:30');
  });

  it('matches utcMsToETTime when given America/New_York', () => {
    const utcMs = Date.UTC(2024, 5, 15, 18, 30, 0);
    expect(utcMsToTzTime(utcMs, 'America/New_York')).toBe(utcMsToETTime(utcMs));
  });
});
