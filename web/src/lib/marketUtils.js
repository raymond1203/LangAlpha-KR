/**
 * Shared market utilities used across Dashboard and MarketView.
 */
import { api } from '@/api/client';

/**
 * Compute extended-hours display info from market status and a data row.
 * Accepts both camelCase (snapshot-enriched rows) and snake_case (raw snapshot) field names.
 *
 * @param {Object|null} marketStatus - { market, afterHours, earlyHours }
 * @param {Object} data - Row with earlyTradingChangePercent/lateTradingChangePercent or early_trading_change_percent/late_trading_change_percent
 * @param {{ shortLabels?: boolean }} [opts]
 * @returns {{ extPct: number|null, extLabel: string|null }}
 */
export function getExtendedHoursInfo(marketStatus, data, { shortLabels = false } = {}) {
  const isRegularOpen = marketStatus?.market === 'open' && !marketStatus?.afterHours && !marketStatus?.earlyHours;
  const isPreMarket = marketStatus?.earlyHours === true;

  const earlyPct = data?.earlyTradingChangePercent ?? data?.early_trading_change_percent ?? null;
  const latePct = data?.lateTradingChangePercent ?? data?.late_trading_change_percent ?? null;

  const extPct = isPreMarket && earlyPct != null
    ? earlyPct
    : !isRegularOpen && latePct != null
      ? latePct
      : null;

  const extLabel = isPreMarket && earlyPct != null
    ? (shortLabels ? 'PM' : 'Pre-Market')
    : !isRegularOpen && latePct != null
      ? (shortLabels ? 'AH' : 'After-Hours')
      : null;

  return { extPct, extLabel };
}

/**
 * Search for stocks by keyword (symbol or company name).
 * GET /api/v1/market-data/search/stocks
 * @param {string} query - Search keyword
 * @param {number} limit - Maximum results (default: 50, max: 100)
 * @returns {Promise<{query: string, results: Array, count: number}>}
 */
export async function searchStocks(query, limit = 50) {
  if (!query || !query.trim()) {
    return { query: '', results: [], count: 0 };
  }
  try {
    const params = new URLSearchParams();
    params.append('query', query.trim());
    params.append('limit', String(Math.min(Math.max(1, limit), 100)));
    const { data } = await api.get('/api/v1/market-data/search/stocks', { params });
    return data || { query: query.trim(), results: [], count: 0 };
  } catch (e) {
    console.error('Search stocks failed:', e?.response?.status, e?.response?.data, e?.message);
    return { query: query.trim(), results: [], count: 0 };
  }
}

/**
 * GET /api/v1/market-data/market-status
 * Returns { market, afterHours, earlyHours, serverTime, exchanges }
 */
export async function fetchMarketStatus({ signal } = {}) {
  try {
    const { data } = await api.get('/api/v1/market-data/market-status', { signal });
    return data || {};
  } catch (e) {
    if (e?.name === 'CanceledError' || e?.name === 'AbortError') throw e;
    console.error('[API] fetchMarketStatus failed:', e?.message);
    return {};
  }
}
