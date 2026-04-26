import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import {
  MarketProvider,
  deriveRegionFromLocale,
  useMarket,
} from '../MarketContext';
import i18n from '../../i18n';

describe('deriveRegionFromLocale', () => {
  it('ko-* → kr', () => {
    expect(deriveRegionFromLocale('ko-KR')).toBe('kr');
    expect(deriveRegionFromLocale('ko')).toBe('kr');
  });

  it('non-ko → us', () => {
    expect(deriveRegionFromLocale('en-US')).toBe('us');
    expect(deriveRegionFromLocale('zh-CN')).toBe('us');
  });

  it('null/undefined/empty → us', () => {
    expect(deriveRegionFromLocale(undefined)).toBe('us');
    expect(deriveRegionFromLocale(null)).toBe('us');
    expect(deriveRegionFromLocale('')).toBe('us');
  });

  it('case-insensitive on prefix', () => {
    expect(deriveRegionFromLocale('KO-KR')).toBe('kr');
    expect(deriveRegionFromLocale('KO')).toBe('kr');
  });
});

describe('MarketProvider + useMarket', () => {
  beforeEach(async () => {
    localStorage.clear();
    await i18n.changeLanguage('en-US');
  });

  function wrapper({ children }: { children: React.ReactNode }) {
    return <MarketProvider>{children}</MarketProvider>;
  }

  it('throws if useMarket called outside provider', () => {
    expect(() => renderHook(() => useMarket())).toThrow(/MarketProvider/);
  });

  it('defaults to setting=auto and resolves region by locale (en-US → us)', () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('auto');
    expect(result.current.region).toBe('us');
  });

  it('auto + ko-KR locale → region=kr', async () => {
    await i18n.changeLanguage('ko-KR');
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('auto');
    expect(result.current.region).toBe('kr');
  });

  it('explicit setting=kr overrides locale (en-US locale + setting=kr → kr)', () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    act(() => result.current.setSetting('kr'));
    expect(result.current.region).toBe('kr');
  });

  it('explicit setting=us overrides locale (ko-KR locale + setting=us → us)', async () => {
    await i18n.changeLanguage('ko-KR');
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.region).toBe('kr'); // 초기 auto = kr
    act(() => result.current.setSetting('us'));
    expect(result.current.region).toBe('us');
  });

  it('persists setting to localStorage', () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    act(() => result.current.setSetting('kr'));
    expect(localStorage.getItem('marketRegion')).toBe('kr');
  });

  it('reads existing setting from localStorage on mount', () => {
    localStorage.setItem('marketRegion', 'us');
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('us');
    expect(result.current.region).toBe('us');
  });

  it('ignores garbage localStorage value (falls back to auto)', () => {
    localStorage.setItem('marketRegion', 'garbage');
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('auto');
  });

  it('locale change while in auto mode flips region', async () => {
    const { result, rerender } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.region).toBe('us');

    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });
    rerender();
    expect(result.current.region).toBe('kr');
  });

  it('locale change while in explicit mode does NOT flip region', async () => {
    const { result, rerender } = renderHook(() => useMarket(), { wrapper });
    act(() => result.current.setSetting('us'));
    expect(result.current.region).toBe('us');

    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });
    rerender();
    // 명시적으로 us 를 골랐으므로 ko-KR 로 바뀌어도 us 유지
    expect(result.current.region).toBe('us');
  });
});
