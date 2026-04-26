import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Mock } from 'vitest';
import { renderHookWithProviders } from '../../../../test/utils';
import { useDashboardData } from '../useDashboardData';
import { act, waitFor } from '@testing-library/react';
import i18n from '../../../../i18n';

vi.mock('../../utils/api', () => ({
  getNews: vi.fn(),
  getIndices: vi.fn(),
  INDEX_SYMBOLS: ['GSPC', 'IXIC', 'DJI', 'RUT', 'VIX'],
  KR_INDEX_SYMBOLS: ['KS11', 'KQ11', 'KS200'],
  fallbackIndex: vi.fn((s: string) => ({
    symbol: s, name: s, price: 0, change: 0, changePercent: 0, isPositive: true, sparklineData: [],
  })),
  normalizeIndexSymbol: vi.fn((s: string) => String(s).replace(/^\^/, '').toUpperCase()),
  // FORK (#32): locale 의존 → market 의존으로 시그니처 변경
  getIndexSetForMarket: vi.fn((market: 'kr' | 'us') => {
    if (market === 'kr') {
      return { symbols: ['KS11', 'KQ11', 'KS200'], names: { KS11: '코스피', KQ11: '코스닥', KS200: '코스피 200' } };
    }
    return { symbols: ['GSPC', 'IXIC', 'DJI', 'RUT', 'VIX'], names: { GSPC: 'S&P 500', IXIC: 'NASDAQ', DJI: 'Dow Jones', RUT: 'Russell 2000', VIX: 'VIX' } };
  }),
  getNewsRegionForMarket: vi.fn((market: 'kr' | 'us') => (market === 'kr' ? 'kr' : undefined)),
}));

vi.mock('@/lib/marketUtils', () => ({
  fetchMarketStatus: vi.fn(),
}));

import { getNews, getIndices } from '../../utils/api';
import { fetchMarketStatus } from '@/lib/marketUtils';

const mockFetchMarketStatus = fetchMarketStatus as Mock;
const mockGetIndices = getIndices as Mock;
const mockGetNews = getNews as Mock;

describe('useDashboardData', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    // 다른 테스트로부터의 locale/market leakage 방지 — 각 테스트가 en-US + auto market (= us) 에서 시작
    await i18n.changeLanguage('en-US');
    localStorage.removeItem('marketRegion');
    mockFetchMarketStatus.mockResolvedValue({ market: 'open', afterHours: false, earlyHours: false });
    mockGetIndices.mockResolvedValue({
      indices: [
        { symbol: 'GSPC', name: 'S&P 500', price: 5000, change: 50, changePercent: 1.0, isPositive: true, sparklineData: [] },
      ],
      failedCount: 0,
    });
    mockGetNews.mockResolvedValue({ results: [], count: 0 });
  });

  it('returns marketStatus from the fetched data', async () => {
    const { result } = renderHookWithProviders(() => useDashboardData());

    await waitFor(() => expect(result.current.marketStatus).not.toBeNull());
    expect(result.current.marketStatus!.market).toBe('open');
  });

  it('passes region=us to fetchMarketStatus by default (en-US locale, auto market)', async () => {
    renderHookWithProviders(() => useDashboardData());
    await waitFor(() => {
      expect(mockFetchMarketStatus).toHaveBeenCalledWith(
        expect.objectContaining({ region: 'us' }),
      );
    });
  });

  it('refetches marketStatus with region=kr when locale changes to ko-KR', async () => {
    // FORK (#37): marketStatus useQuery 의 queryKey 에 region 포함 → market=kr 전환 시 자동 refetch
    renderHookWithProviders(() => useDashboardData());
    await waitFor(() => {
      expect(mockFetchMarketStatus).toHaveBeenCalledWith(
        expect.objectContaining({ region: 'us' }),
      );
    });

    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });

    await waitFor(() => {
      const krCall = mockFetchMarketStatus.mock.calls.find(
        (args) => (args[0] as { region?: string })?.region === 'kr',
      );
      expect(krCall).toBeDefined();
    });
  });

  it('returns indices data', async () => {
    const { result } = renderHookWithProviders(() => useDashboardData());

    await waitFor(() => expect(result.current.indices).toBeDefined());
    // Indices should eventually resolve (either from query or placeholderData)
    expect(Array.isArray(result.current.indices)).toBe(true);
  });

  it('returns newsItems as an empty array when no news', async () => {
    mockGetNews.mockResolvedValue({ results: [] });

    const { result } = renderHookWithProviders(() => useDashboardData());

    await waitFor(() => expect(result.current.newsLoading).toBe(false));
    expect(result.current.newsItems).toEqual([]);
  });

  it('transforms news results into formatted items', async () => {
    mockGetNews.mockResolvedValue({
      results: [
        {
          id: 'n-1',
          title: 'Markets rally',
          published_at: new Date().toISOString(),
          has_sentiment: true,
          source: { name: 'Reuters', favicon_url: 'https://favicon.com/r.ico' },
          image_url: 'https://img.com/1.jpg',
          tickers: ['AAPL'],
        },
      ],
      count: 1,
    });

    const { result } = renderHookWithProviders(() => useDashboardData());

    await waitFor(() => expect(result.current.newsItems.length).toBe(1));
    const item = result.current.newsItems[0];
    expect(item.id).toBe('n-1');
    expect(item.title).toBe('Markets rally');
    expect(item.source).toBe('Reuters');
    expect(item.isHot).toBe(true);
    expect(item.tickers).toEqual(['AAPL']);
  });

  it('provides a marketStatusRef for backward compatibility', async () => {
    const { result } = renderHookWithProviders(() => useDashboardData());

    await waitFor(() => expect(result.current.marketStatus).not.toBeNull());
    expect(result.current.marketStatusRef).toBeDefined();
    expect(result.current.marketStatusRef.current).toEqual(result.current.marketStatus);
  });

  it('refetches indices with KR ticker set when locale changes to ko-KR', async () => {
    // en-US 초기 상태 — getIndices 가 US 심볼로 호출됐는지 확인
    renderHookWithProviders(() => useDashboardData());
    await waitFor(() => {
      expect(mockGetIndices).toHaveBeenCalledWith(['GSPC', 'IXIC', 'DJI', 'RUT', 'VIX']);
    });

    const usCallCount = mockGetIndices.mock.calls.length;

    // locale 을 ko-KR 로 전환 — useTranslation 구독자가 리렌더 → useMemo dep 변경 → queryKey 갱신 → 자동 refetch
    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });

    await waitFor(() => {
      const krCall = mockGetIndices.mock.calls.find((args) =>
        Array.isArray(args[0]) && args[0][0] === 'KS11',
      );
      expect(krCall).toBeDefined();
      expect(krCall![0]).toEqual(['KS11', 'KQ11', 'KS200']);
    });
    // locale 변경 이후에 추가 호출이 발생했는지 확인 — 캐시가 분리되어 새 fetch 가 트리거됨
    expect(mockGetIndices.mock.calls.length).toBeGreaterThan(usCallCount);
  });

  it('passes region=kr to getNews when locale is ko-KR', async () => {
    // en-US: region 파라미터 없이 (undefined) 호출됨 — 글로벌 fallback
    renderHookWithProviders(() => useDashboardData());
    await waitFor(() => {
      expect(mockGetNews).toHaveBeenCalledWith({ limit: 50, region: undefined });
    });

    // locale 전환 → region='kr' 로 재호출, queryKey 분리로 새 fetch 트리거
    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });

    await waitFor(() => {
      const krNewsCall = mockGetNews.mock.calls.find(
        (args) => (args[0] as { region?: string })?.region === 'kr',
      );
      expect(krNewsCall).toBeDefined();
    });
  });

  it('explicit market="us" 가 ko-KR locale 보다 우선 — locale 전환해도 KR 인덱스로 안 바뀜', async () => {
    // initialMarket='us' 로 명시 → MarketProvider 가 localStorage/locale 둘 다 무시하고 us 강제
    renderHookWithProviders(() => useDashboardData(), { initialMarket: 'us' });
    await waitFor(() => {
      expect(mockGetIndices).toHaveBeenCalledWith(['GSPC', 'IXIC', 'DJI', 'RUT', 'VIX']);
    });

    // locale 을 ko-KR 로 전환해도 explicit 'us' setting 이라 region 안 바뀜 → KR 호출 없어야
    await act(async () => {
      await i18n.changeLanguage('ko-KR');
    });

    // 잠시 기다려 잠재적 추가 fetch 가 없는지 확인
    await new Promise((r) => setTimeout(r, 50));

    const krCall = mockGetIndices.mock.calls.find((args) =>
      Array.isArray(args[0]) && args[0][0] === 'KS11',
    );
    expect(krCall).toBeUndefined();
  });
});
