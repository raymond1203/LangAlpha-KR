// FORK: 시장(KR/US) 을 first-class state 로 승격해 locale 과 분리.
// PR #25/#28/#29 에서 빠른 v1 으로 \`locale === market\` 가정을 깔았으나
// "한국어 UX + 미국주" / "영어 UX + 한국 시장" 시나리오를 막는 결합이 됐다.
// 본 컨텍스트가 도입된 이후 모든 시장 의존 컴포넌트는 useMarket().region 을 사용 — locale 은 표시 언어만.
import React, { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { safeLocalStorage } from '@/lib/utils';

export type MarketRegion = 'kr' | 'us';
export type MarketSetting = MarketRegion | 'auto';

const STORAGE_KEY = 'marketRegion';

export interface MarketContextValue {
  /** 사용자가 Settings 에서 고른 raw 값. UI 표시/dropdown 에 그대로 사용. */
  setting: MarketSetting;
  setSetting: (value: MarketSetting) => void;
  /**
   * resolved 값 — 'auto' 면 locale 으로 도출 (ko-* → 'kr', 그 외 → 'us').
   * 모든 시장 의존 컴포넌트는 이 값만 읽으면 됨.
   */
  region: MarketRegion;
}

const MarketContext = createContext<MarketContextValue | null>(null);

/** 'auto' 모드에서 locale 을 시장으로 변환. ko-* → kr, 그 외 → us. */
export function deriveRegionFromLocale(locale: string | undefined | null): MarketRegion {
  if (locale && locale.toLowerCase().startsWith('ko')) {
    return 'kr';
  }
  return 'us';
}

function getInitialSetting(): MarketSetting {
  const stored = safeLocalStorage.getItem(STORAGE_KEY);
  if (stored === 'kr' || stored === 'us' || stored === 'auto') return stored;
  return 'auto';
}

export function MarketProvider({ children }: { children: React.ReactNode }) {
  const { i18n } = useTranslation();
  const [setting, setSettingState] = useState<MarketSetting>(getInitialSetting);

  // localStorage 영속 — Settings 변경이 새로고침에도 유지
  useEffect(() => {
    safeLocalStorage.setItem(STORAGE_KEY, setting);
  }, [setting]);

  // i18n 이 mock 으로 stub 되거나 init 전인 환경 (테스트 격리, 초기 부팅) 에서도 안전하게 동작.
  // deriveRegionFromLocale 자체가 null/undefined 를 us 로 fallback.
  const region: MarketRegion =
    setting === 'auto' ? deriveRegionFromLocale(i18n?.language ?? null) : setting;

  const setSetting = (value: MarketSetting) => setSettingState(value);

  const value = useMemo<MarketContextValue>(
    () => ({ setting, setSetting, region }),
    [setting, region],
  );

  return <MarketContext.Provider value={value}>{children}</MarketContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useMarket(): MarketContextValue {
  const ctx = useContext(MarketContext);
  if (!ctx) throw new Error('useMarket must be used within MarketProvider');
  return ctx;
}
