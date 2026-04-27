/**
 * useKoreanSnapshotPolling — KR ticker 의 60s snapshot polling.
 *
 * FORK (#33): 본 fork 의 backend 에 KR 시장 WebSocket source 가 없어 (한투/키움 OAuth
 * 부담, ginlix-data 외부 종속) realtime 대신 polling 으로 fallback. 단일 사용자
 * self-hosted production 컨텍스트에서 polling traffic 미미. KR 정규장 09:00–15:30 (KST)
 * 짧고, 60s lag 은 dashboard 모니터링에 충분.
 *
 * symbol 이 KR 아니면 (.KS / .KQ 외) no-op — US 심볼은 useMarketDataWS 가 담당.
 */
import { useEffect, useState } from 'react';
import { fetchSnapshot } from '../utils/api';
import type { PriceUpdate } from './useMarketDataWS';
import { isKoreanSymbol } from '@/lib/marketTimezone';

const POLLING_INTERVAL_MS = 60_000;

export function useKoreanSnapshotPolling(symbol: string | null): PriceUpdate | null {
  const [price, setPrice] = useState<PriceUpdate | null>(null);

  useEffect(() => {
    if (!symbol || !isKoreanSymbol(symbol)) {
      setPrice(null);
      return;
    }

    let aborted = false;

    const fetchOnce = async () => {
      try {
        const snap = await fetchSnapshot(symbol);
        if (aborted || !snap) return;
        const close = snap.price ?? 0;
        const prev = snap.previous_close ?? 0;
        const change = snap.change ?? (prev ? close - prev : 0);
        const pct = snap.change_percent ?? (prev ? (change / prev) * 100 : 0);
        const open = snap.open ?? 0;
        const high = snap.high ?? 0;
        const low = snap.low ?? 0;
        const volume = snap.volume ?? 0;

        setPrice({
          symbol: snap.symbol || symbol,
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
        });
      } catch (err) {
        if (!aborted) console.warn('[KR polling] snapshot failed:', err);
      }
    };

    fetchOnce();
    const id = setInterval(fetchOnce, POLLING_INTERVAL_MS);
    return () => {
      aborted = true;
      clearInterval(id);
    };
  }, [symbol]);

  return price;
}
