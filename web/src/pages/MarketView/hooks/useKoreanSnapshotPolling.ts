/**
 * useKoreanSnapshotPolling — KR ticker 의 60s snapshot polling.
 *
 * FORK (#33): 본 fork 의 backend 에 KR 시장 WebSocket source 가 없어 (한투/키움 OAuth
 * 부담, ginlix-data 외부 종속) realtime 대신 polling 으로 fallback. React Query 로
 * 위임해 dedup / abort / visibility (refetchIntervalInBackground=false) 자동 처리.
 *
 * symbol 이 KR 아니면 (.KS / .KQ 외) query disabled — useDashboardData 패턴과 동일.
 *
 * KR 정규장 (KST 09:00–15:30) 외 시간에는 polling interval 을 늘려 traffic 절감
 * (closed 면 5분, open 이면 60초). 마지막 snapshot 은 staleTime 동안 캐시되어
 * 시장 closed 시 사용자에게 직전 종가 표시.
 */
import { useQuery } from '@tanstack/react-query';
import { fetchSnapshot } from '../utils/api';
import type { PriceUpdate } from './useMarketDataWS';
import { isKoreanSymbol } from '@/lib/marketTimezone';

const OPEN_INTERVAL_MS = 60_000;
const CLOSED_INTERVAL_MS = 5 * 60_000;

/** KST 평일 09:00–15:30 (정규장) 검사 — 단순한 시간 기반, 휴장일 미반영. */
function isKRMarketOpen(now: Date = new Date()): boolean {
  // KST = UTC+9. browser timezone 무관하게 UTC 시간으로 KST 도출.
  const kstMs = now.getTime() + 9 * 60 * 60 * 1000;
  const kst = new Date(kstMs);
  const day = kst.getUTCDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const minutes = kst.getUTCHours() * 60 + kst.getUTCMinutes();
  return minutes >= 9 * 60 && minutes <= 15 * 60 + 30;
}

export function useKoreanSnapshotPolling(symbol: string | null | undefined): PriceUpdate | null {
  // FORK (#33): hook 자체가 KR 검사 — 호출자는 그냥 selectedStock 넘기면 됨.
  // KR 외 symbol 은 query disabled 로 idle (no fetch, no PriceUpdate).
  const enabled = !!symbol && isKoreanSymbol(symbol);

  const { data } = useQuery({
    queryKey: ['marketview', 'krSnapshot', symbol],
    queryFn: ({ signal }) => fetchSnapshot(symbol as string, { signal }),
    enabled,
    refetchInterval: () => (isKRMarketOpen() ? OPEN_INTERVAL_MS : CLOSED_INTERVAL_MS),
    // 탭이 background 면 폴링 중단 — visibilitychange 시 자동 resume.
    refetchIntervalInBackground: false,
    staleTime: 30_000,
    retry: 1,
  });

  if (!enabled || !data) return null;

  const close = data.price ?? 0;
  const prev = data.previous_close ?? 0;
  const change = data.change ?? (prev ? close - prev : 0);
  const pct = data.change_percent ?? (prev ? (change / prev) * 100 : 0);
  const open = data.open ?? 0;
  const high = data.high ?? 0;
  const low = data.low ?? 0;
  const volume = data.volume ?? 0;

  return {
    symbol: data.symbol || (symbol as string),
    price: close,
    open,
    high,
    low,
    close,
    volume,
    change,
    changePercent: pct,
    timestamp: Date.now(),
    barData: {
      time: Math.floor(Date.now() / 1000),
      open,
      high,
      low,
      close,
      volume,
    },
  };
}

// 테스트용 export — KR 정규장 시간 검사 자체 검증.
export const __test_isKRMarketOpen = isKRMarketOpen;
