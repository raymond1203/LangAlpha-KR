import { useCallback, useEffect, useState } from 'react';
import { getNews } from '../utils/api';

// Module-level caches keyed by caller-provided cacheKey
const cacheMap = new Map();

function formatRelativeTime(timestamp) {
  if (!timestamp) return '';
  const now = new Date();
  const then = new Date(timestamp);
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hr${diffHr > 1 ? 's' : ''} ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;
}

function mapNewsResults(results) {
  return results.map((r) => ({
    id: r.id,
    title: r.title,
    time: formatRelativeTime(r.published_at),
    isHot: r.has_sentiment,
    image: r.image_url || null,
    source: r.source?.name || '',
    favicon: r.source?.favicon_url || null,
    tickers: r.tickers || [],
  }));
}

/**
 * Hook to fetch news for a list of ticker rows.
 * @param {Array} rows - Array of objects with a `symbol` property
 * @param {string} cacheKey - Unique key for module-level caching (e.g. 'portfolio', 'watchlist')
 * @returns {{ items: Array, loading: boolean }}
 */
export function useTickerNews(rows, cacheKey) {
  const cached = cacheMap.get(cacheKey);
  const [items, setItems] = useState(() => cached?.items || []);
  const [loading, setLoading] = useState(!cached);

  const fetchNews = useCallback(async () => {
    const tickers = (rows || []).map((r) => r.symbol).filter(Boolean);
    const tickerKey = [...tickers].sort().join(',');

    if (!tickers.length) {
      setItems([]);
      setLoading(false);
      cacheMap.set(cacheKey, { items: [], tickerKey: '' });
      return;
    }

    setLoading(true);
    try {
      const data = await getNews({ tickers, limit: 50 });
      const mapped = data.results?.length > 0 ? mapNewsResults(data.results) : [];
      setItems(mapped);
      cacheMap.set(cacheKey, { items: mapped, tickerKey });
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [rows, cacheKey]);

  useEffect(() => {
    const tickers = (rows || []).map((r) => r.symbol).filter(Boolean);
    const tickerKey = [...tickers].sort().join(',');
    const cached = cacheMap.get(cacheKey);
    if (cached?.tickerKey !== tickerKey) {
      cacheMap.delete(cacheKey);
    }
    if (!cacheMap.has(cacheKey)) fetchNews();
  }, [fetchNews, rows, cacheKey]);

  return { items, loading };
}
