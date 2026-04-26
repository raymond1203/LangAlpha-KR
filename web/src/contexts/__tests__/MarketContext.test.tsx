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

  it('localStorage 가 locale 도출보다 우선 (kr stored + en-US locale → kr)', () => {
    // beforeEach 가 i18n 을 en-US 로 리셋한 상태. stored='kr' 가 진짜로 우선인지 검증
    // (이전 'us' stored + en-US locale 케이스는 derive 결과와 동일해 storage 우선 여부 모호).
    localStorage.setItem('marketRegion', 'kr');
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('kr');
    expect(result.current.region).toBe('kr');
  });

  it('ignores garbage localStorage value (falls back to auto)', () => {
    localStorage.setItem('marketRegion', 'garbage');
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('auto');
  });

  it('locale change while in auto mode flips region', async () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.region).toBe('us');

    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });
    // useTranslation 이 자동 re-render trigger — manual rerender() 불필요
    expect(result.current.region).toBe('kr');
  });

  it('locale change while in explicit mode does NOT flip region', async () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    act(() => result.current.setSetting('us'));
    expect(result.current.region).toBe('us');

    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });
    // 명시적으로 us 를 골랐으므로 ko-KR 로 바뀌어도 us 유지
    expect(result.current.region).toBe('us');
  });

  it('initialSetting prop 명시 시 localStorage 무시 (테스트 격리용)', () => {
    localStorage.setItem('marketRegion', 'kr');

    function customWrapper({ children }: { children: React.ReactNode }) {
      return <MarketProvider initialSetting="us">{children}</MarketProvider>;
    }

    const { result } = renderHook(() => useMarket(), { wrapper: customWrapper });
    expect(result.current.setting).toBe('us');
    expect(result.current.region).toBe('us');
  });

  it('cross-tab storage 이벤트로 setting 동기화', () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('auto');

    // 다른 탭에서 'kr' 로 변경한 시뮬레이션 — storage 이벤트는 다른 document 에서만 fire
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'marketRegion',
        newValue: 'kr',
        oldValue: 'auto',
      }));
    });

    expect(result.current.setting).toBe('kr');
    expect(result.current.region).toBe('kr');
  });

  it('storage 이벤트의 invalid 값은 무시', () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    expect(result.current.setting).toBe('auto');

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'marketRegion',
        newValue: 'garbage',
      }));
    });

    expect(result.current.setting).toBe('auto');
  });

  it('storage 이벤트의 다른 키는 무시', () => {
    const { result } = renderHook(() => useMarket(), { wrapper });
    act(() => result.current.setSetting('us'));
    expect(result.current.setting).toBe('us');

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'someOtherKey',
        newValue: 'kr',
      }));
    });

    expect(result.current.setting).toBe('us');
  });
});

describe('deriveRegionFromLocale — pseudo-prefix 정확성', () => {
  it('ko-* prefix 만 매칭, kok 같은 가짜 prefix 는 us', () => {
    // 'kok' (Konkani) 같은 진짜 ISO 코드는 'kor' 와 무관 → us 로 매칭돼야
    expect(deriveRegionFromLocale('kok-IN')).toBe('us');
    expect(deriveRegionFromLocale('kok')).toBe('us');
    expect(deriveRegionFromLocale('koXX')).toBe('us');
  });
});
