import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getNews,
  getIndices,
  fallbackIndex,
  normalizeIndexSymbol,
  getIndexSetForMarket,
  getNewsRegionForMarket,
} from '../utils/api';
import { fetchMarketStatus } from '@/lib/marketUtils';
import { useMarket } from '@/contexts/MarketContext';
import type { IndexData } from '@/types/market';

interface MarketStatusData {
  market?: string;
  afterHours?: boolean;
  earlyHours?: boolean;
  [key: string]: unknown;
}

interface NewsItem {
  id: string;
  title: string;
  time: string;
  isHot: boolean;
  source: string;
  favicon: string | null;
  image: string | null;
  tickers: string[];
  articleUrl?: string | null;
}

interface DashboardData {
  indices: IndexData[] | undefined;
  indicesLoading: boolean;
  newsItems: NewsItem[];
  newsLoading: boolean;
  marketStatus: MarketStatusData | null;
  marketStatusRef: { current: MarketStatusData | null };
}

/**
 * Formats a given timestamp to a relative time string (e.g. "just now", "10 min ago").
 */
function formatRelativeTime(timestamp: string | number | null | undefined): string {
  if (!timestamp) return '';
  const now = new Date();
  const then = new Date(timestamp);
  const diffMs = now.getTime() - then.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hr${diffHr > 1 ? 's' : ''} ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;
}

/**
 * useDashboardData Hook
 * Uses TanStack Query to manage fetching, caching, and auto-polling of data.
 * Eliminates race conditions and reduces boilerplate of manual useEffects.
 */
export function useDashboardData(): DashboardData {
  // FORK (#32): 시장(MarketContext) 으로 인덱스/뉴스 region 도출 — locale 의존성 제거.
  // 사용자가 한국어 UX 로 미국 시장, 또는 영어 UX 로 한국 시장 보는 시나리오 지원.
  const { region } = useMarket();
  const indexSet = useMemo(() => getIndexSetForMarket(region), [region]);
  const newsRegion = useMemo(() => getNewsRegionForMarket(region), [region]);

  // 1. Market Status (Polls every 60s, cached globally)
  const { data: marketStatus = null } = useQuery<MarketStatusData | null>({
    queryKey: ['dashboard', 'marketStatus'],
    queryFn: fetchMarketStatus,
    refetchInterval: 60000,
    refetchIntervalInBackground: false,
    staleTime: 30000,
  });

  // 2. Market Indices (Adaptive Polling: 30s open / 60s closed)
  const isMarketOpen = marketStatus?.market === 'open' ||
    (marketStatus && !marketStatus.afterHours && !marketStatus.earlyHours && marketStatus.market !== 'closed');

  const { data: indices, isLoading: indicesLoading } = useQuery<IndexData[]>({
    // queryKey 에 indexSet.symbols 를 포함 → locale 변경 시 자동 refetch + 별도 캐시 분리
    queryKey: ['dashboard', 'indices', indexSet.symbols],
    queryFn: async () => {
      const { indices: next } = await getIndices(indexSet.symbols);
      return next;
    },
    // Using placeholderData provides standard fallback values instantly
    // without populating the cache as "fresh", thereby triggering an immediate background fetch
    placeholderData: (): IndexData[] => indexSet.symbols.map((s) => fallbackIndex(normalizeIndexSymbol(s))),
    refetchInterval: isMarketOpen ? 30000 : 60000,
    refetchIntervalInBackground: false,
    staleTime: 10000,
  });

  // 3. News Feed (Fetched once, cached for 5 minutes)
  // FORK: queryKey 에 newsRegion 포함 → locale 변경 시 KR/US 캐시 분리 + 자동 refetch
  const { data: newsItems = [], isLoading: newsLoading } = useQuery<NewsItem[]>({
    queryKey: ['dashboard', 'news', newsRegion ?? 'global'],
    queryFn: async (): Promise<NewsItem[]> => {
      const data = await getNews({ limit: 50, region: newsRegion });
      if (data.results && data.results.length > 0) {
        return data.results.map((r: Record<string, unknown>) => ({
          id: r.id as string,
          title: r.title as string,
          time: formatRelativeTime(r.published_at as string | null | undefined),
          isHot: r.has_sentiment as boolean,
          source: (r.source as Record<string, unknown> | undefined)?.name as string || '',
          favicon: (r.source as Record<string, unknown> | undefined)?.favicon_url as string || null,
          image: r.image_url as string || null,
          tickers: (r.tickers as string[]) || [],
          articleUrl: (r.article_url as string) || null,
        }));
      }
      return [];
    },
    staleTime: 5 * 60 * 1000, // 5 minutes fresh cache
  });

  return {
    indices,
    indicesLoading,
    newsItems,
    newsLoading,
    marketStatus,
    // Kept for backward compatibility with components that might use MarketStatusRef
    marketStatusRef: { current: marketStatus }
  };
}
