/**
 * Dashboard API utilities
 * All backend endpoints used by the Dashboard page
 */
import { api } from '@/api/client';
import { utcMsToETDate, utcMsToETTime } from '@/lib/utils';
import * as portfolioApi from './portfolio';
import * as watchlistApi from './watchlist';
import * as watchlistItemsApi from './watchlistItems';

// --- Market data (see docs/ptc-agent-api/market data) ---

/** Index symbols: normalized (GSPC, IXIC, DJI, RUT). Index.yml / Index Batch.yml use these. */
const INDEX_SYMBOLS = ['GSPC', 'IXIC', 'DJI', 'RUT', 'VIX'];
const INDEX_NAMES = { GSPC: 'S&P 500', IXIC: 'NASDAQ', DJI: 'Dow Jones', RUT: 'Russell 2000', VIX: 'VIX' };

function normalizeIndexSymbol(s) {
  return String(s).replace(/^\^/, '').toUpperCase();
}

function fallbackIndex(norm) {
  return {
    symbol: norm,
    name: INDEX_NAMES[norm] ?? norm,
    price: 0,
    change: 0,
    changePercent: 0,
    isPositive: true,
    sparklineData: [],
  };
}

/**
 * GET /api/v1/market-data/intraday/indexes/:symbol (Index.yml)
 * Path uses normalized symbol (e.g. GSPC). Query: interval, from, to optional.
 * Returns the most recent data point for the index.
 */
export async function getIndex(symbol, opts = {}) {
  const norm = normalizeIndexSymbol(String(symbol).trim());
  try {
    const { data } = await api.get(`/api/v1/market-data/intraday/indexes/${encodeURIComponent(norm)}`);

    const pts = data?.data ?? [];

    if (!Array.isArray(pts) || !pts.length) {
      throw new Error(`No intraday data for ${norm}`);
    }

    // Sort ascending by time (Unix ms)
    const sorted = [...pts].sort((a, b) => a.time - b.time);

    // Isolate the most recent trading day, regular hours only (9:30–16:00)
    const latestDate = utcMsToETDate(sorted[sorted.length - 1].time);
    const todayPoints = sorted.filter((p) => {
      if (utcMsToETDate(p.time) !== latestDate) return false;
      const t = utcMsToETTime(p.time);
      return t >= '09:30' && t <= '16:00';
    });

    const oldest = todayPoints[0];
    const mostRecent = todayPoints[todayPoints.length - 1];

    const open = Number(oldest?.open ?? 0);
    const close = Number(mostRecent?.close ?? 0);
    const change = close - open;
    const changePercent = open ? (change / open) * 100 : 0;

    const result = {
      symbol: norm,
      name: INDEX_NAMES[norm] ?? norm,
      price: Math.round(close * 100) / 100,
      change: Math.round(change * 100) / 100,
      changePercent: Math.round(changePercent * 100) / 100,
      isPositive: change >= 0,
      sparklineData: todayPoints
        .filter((p) => Number(p.close) > 0)
        .map((p) => ({ time: utcMsToETTime(p.time), val: Number(p.close) })),
    };

    return result;
  } catch (e) {
    console.error(`[API] getIndex - ${norm}: Error:`, e?.message);
    const msg = e.response?.data?.detail ?? e.message;
    throw new Error(typeof msg === 'string' ? msg : String(msg));
  }
}

/**
 * Fetches indices data: snapshot batch for price/change, intraday for sparklines.
 * Returns { indices, failedCount }.
 */
export async function getIndices(symbols = INDEX_SYMBOLS, opts = {}) {
  const list = symbols.map((s) => normalizeIndexSymbol(String(s).trim()));

  // Fetch snapshot (price/change) and intraday (sparklines) in parallel
  const [snapshots, sparklineResults] = await Promise.all([
    getSnapshotIndexes(list),
    Promise.all(list.map(async (norm) => {
      try {
        const result = await getIndex(norm);
        return { symbol: norm, sparklineData: result.sparklineData };
      } catch {
        return { symbol: norm, sparklineData: [] };
      }
    })),
  ]);

  const sparklineMap = Object.fromEntries(sparklineResults.map((r) => [r.symbol, r.sparklineData]));
  const snapshotList = snapshots?.snapshots || snapshots?.results || snapshots?.data || [];
  const snapshotMap = Array.isArray(snapshotList)
    ? Object.fromEntries(snapshotList.map((s) => [normalizeIndexSymbol(s.symbol), s]))
    : {};

  let failedCount = 0;
  const indices = list.map((norm) => {
    const snap = snapshotMap[norm];
    if (snap && snap.price != null) {
      const change = snap.change ?? 0;
      const changePct = snap.change_percent ?? (snap.previous_close ? ((change / snap.previous_close) * 100) : 0);
      return {
        symbol: norm,
        name: INDEX_NAMES[norm] ?? snap.name ?? norm,
        price: Math.round(snap.price * 100) / 100,
        change: Math.round(change * 100) / 100,
        changePercent: Math.round(changePct * 100) / 100,
        isPositive: change >= 0,
        previousClose: snap.previous_close ?? null,
        sparklineData: sparklineMap[norm] || [],
      };
    }
    failedCount++;
    return { ...fallbackIndex(norm), sparklineData: sparklineMap[norm] || [] };
  });

  return { indices, failedCount };
}

export { INDEX_NAMES, INDEX_SYMBOLS, fallbackIndex, normalizeIndexSymbol };

// --- Hello ---

export async function fetchHello() {
  const { data } = await api.get('/hello', { responseType: 'text' });
  return data;
}

// --- Users ---

export async function createUser(userData) {
  const { data } = await api.post('/api/v1/users', userData);
  return data;
}

export async function getCurrentUser() {
  const { data } = await api.get('/api/v1/users/me');
  return data;
}

export async function getPreferences() {
  try {
    const { data } = await api.get('/api/v1/users/me/preferences');
    return data;
  } catch (err) {
    if (err.response?.status === 404) return null;
    throw err;
  }
}

export async function updateCurrentUser(userData) {
  const { data } = await api.put('/api/v1/users/me', userData);
  return data;
}

export async function updatePreferences(preferences) {
  const { data } = await api.put('/api/v1/users/me/preferences', preferences);
    return data;
}

export async function clearPreferences() {
  const { data } = await api.delete('/api/v1/users/me/preferences');
  return data;
}

export async function uploadAvatar(file) {
  const formData = new FormData();
  formData.append('file', file);

  const { data } = await api.post('/api/v1/users/me/avatar', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data; // { avatar_url: "https://..." }
}

// --- Watchlist & Watchlist Items (CRUD) ---

/**
 * List all watchlists for a user
 * GET /api/v1/users/me/watchlists
 * Returns: { watchlists: [...], total: number }
 */
export const listWatchlists = watchlistApi.listWatchlists;

export const createWatchlist = watchlistApi.createWatchlist;
export const updateWatchlist = watchlistApi.updateWatchlist;
export const deleteWatchlist = watchlistApi.deleteWatchlist;

/**
 * List items in a specific watchlist
 * GET /api/v1/users/me/watchlists/:watchlist_id/items
 * @param {string} watchlistId - The watchlist ID (UUID or 'default')
 * @returns {Promise<Object>} { items: [...], total: number }
 */
export const listWatchlistItems = watchlistItemsApi.listWatchlistItems;

export const updateWatchlistItem = watchlistItemsApi.updateWatchlistItem;

/**
 * @deprecated Use listWatchlists() and listWatchlistItems() instead
 * This function is kept for backward compatibility but should not be used
 */
export async function getWatchlistItems() {
  return watchlistItemsApi.listWatchlistItems('default');
}

/**
 * Adds a stock to a watchlist with full details
 * @param {Object} itemData - Stock item data: { symbol, instrument_type, exchange, name, notes, alert_settings }
 * @param {string} watchlistId - The watchlist ID (UUID or 'default')
 * @returns {Promise<Object>} Created watchlist item
 */
export async function addWatchlistItem(itemData, watchlistId = 'default') {
  return watchlistItemsApi.addWatchlistItem(watchlistId, itemData);
}

/**
 * Deletes a watchlist item by ID
 * @param {string} itemId - The item ID to delete
 * @param {string} watchlistId - The watchlist ID (UUID or 'default')
 */
export async function deleteWatchlistItem(itemId, watchlistId = 'default') {
  return watchlistItemsApi.deleteWatchlistItem(watchlistId, itemId);
}

// --- Snapshot & market status ---

/**
 * GET /api/v1/market-data/snapshots/indexes?symbols=GSPC,IXIC,...
 * Returns batch snapshot for index symbols.
 */
export async function getSnapshotIndexes(symbols = INDEX_SYMBOLS) {
  const list = symbols.map((s) => normalizeIndexSymbol(String(s).trim()));
  try {
    const { data } = await api.get('/api/v1/market-data/snapshots/indexes', {
      params: { symbols: list.join(',') },
    });
    return data || {};
  } catch (e) {
    console.error('[API] getSnapshotIndexes failed:', e?.message);
    return {};
  }
}

/**
 * GET /api/v1/market-data/snapshots/stocks?symbols=AAPL,TSLA,...
 * Returns batch snapshot for stock symbols.
 */
export async function getSnapshotStocks(symbols) {
  const list = [...(symbols || [])].map((s) => String(s).trim().toUpperCase()).filter(Boolean);
  if (!list.length) return {};
  try {
    const { data } = await api.get('/api/v1/market-data/snapshots/stocks', {
      params: { symbols: list.join(',') },
    });
    return data || {};
  } catch (e) {
    console.error('[API] getSnapshotStocks failed:', e?.message);
    return {};
  }
}

// --- Stock prices (batch, for watchlist) ---

const DEFAULT_WATCHLIST_SYMBOLS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'TSLA'];
const DEFAULT_WATCHLIST_NAMES = { AAPL: 'Apple', MSFT: 'Microsoft', NVDA: 'NVIDIA', AMZN: 'Amazon', TSLA: 'Tesla' };

export { DEFAULT_WATCHLIST_SYMBOLS, DEFAULT_WATCHLIST_NAMES };

/**
 * Get company names for a list of stock symbols (FMP profile companyName).
 * @param {string[]} symbols - e.g. ['AAPL', 'MSFT']
 * @returns {Promise<Record<string, string>>} symbol -> company name
 */
export async function getStockCompanyNames(symbols) {
  const list = [...(symbols || [])].map((s) => String(s).trim().toUpperCase()).filter(Boolean);
  if (!list.length) return {};
  try {
    const { data } = await api.post('/api/v1/market-data/stocks/names', { symbols: list });
    return data?.names ?? {};
  } catch {
    return {};
  }
}

export async function getStockPrices(symbols) {
  const list = [...(symbols || [])].map((s) => String(s).trim().toUpperCase()).filter(Boolean);
  if (!list.length) return [];
  try {
    const snapshots = await getSnapshotStocks(list);
    const snapList = snapshots?.snapshots || snapshots?.results || snapshots?.data || [];
    const snapMap = Array.isArray(snapList)
      ? Object.fromEntries(snapList.map((s) => [String(s.symbol).toUpperCase(), s]))
      : {};

    return list.map((sym) => {
      const snap = snapMap[sym];
      if (snap && snap.price != null) {
        const change = snap.change ?? 0;
        const changePct = snap.change_percent ?? 0;
        return {
          symbol: sym,
          price: Math.round(snap.price * 100) / 100,
          change: Math.round(change * 100) / 100,
          changePercent: Math.round(changePct * 100) / 100,
          isPositive: change >= 0,
          previousClose: snap.previous_close ?? null,
          earlyTradingChangePercent: snap.early_trading_change_percent ?? null,
          lateTradingChangePercent: snap.late_trading_change_percent ?? null,
        };
      }
      return { symbol: sym, price: 0, change: 0, changePercent: 0, isPositive: true };
    });
  } catch {
    return list.map((sym) => ({ symbol: sym, price: 0, change: 0, changePercent: 0, isPositive: true }));
  }
}

// --- Portfolio (use CRUD module) ---

export const listPortfolio = portfolioApi.listPortfolio;
export const updatePortfolioHolding = portfolioApi.updatePortfolioHolding;
export const deletePortfolioHolding = portfolioApi.deletePortfolioHolding;

export const getPortfolio = portfolioApi.listPortfolio;

/** Add portfolio holding. Payload: symbol, instrument_type, quantity, average_cost?, ... */
export const addPortfolioHolding = portfolioApi.addPortfolioHolding;

// --- Models ---

export async function getAvailableModels() {
  const { data } = await api.get('/api/v1/models');
  return data;
}

// --- BYOK API Keys ---

export async function getUserApiKeys() {
  const { data } = await api.get('/api/v1/users/me/api-keys');
  return data;
}

export async function updateUserApiKeys(payload) {
  const { data } = await api.put('/api/v1/users/me/api-keys', payload);
  return data;
}

export async function deleteUserApiKey(provider) {
  const { data } = await api.delete(`/api/v1/users/me/api-keys/${provider}`);
  return data;
}

// --- OAuth (Connected Accounts) ---

/**
 * Start Codex device code flow — returns { user_code, verification_url, interval }.
 * POST /api/v1/oauth/codex/device/initiate
 */
export async function initiateCodexDevice() {
  const { data } = await api.post('/api/v1/oauth/codex/device/initiate');
  return data;
}

/**
 * Poll for device authorization approval.
 * POST /api/v1/oauth/codex/device/poll
 * @returns {Promise<Object>} { pending: true } or { success: true, email, plan_type, account_id }
 */
export async function pollCodexDevice() {
  const { data } = await api.post('/api/v1/oauth/codex/device/poll');
  return data;
}

/**
 * Check Codex OAuth connection status.
 * GET /api/v1/oauth/codex/status
 * Returns { connected, account_id, email, plan_type }
 */
export async function getCodexOAuthStatus() {
  try {
    const { data } = await api.get('/api/v1/oauth/codex/status');
    return data;
  } catch {
    return { connected: false, account_id: null, email: null, plan_type: null };
  }
}

/**
 * Disconnect Codex OAuth — delete stored tokens.
 * DELETE /api/v1/oauth/codex
 */
export async function disconnectCodexOAuth() {
  const { data } = await api.delete('/api/v1/oauth/codex');
  return data;
}

// --- Claude OAuth (PKCE Authorization Code Flow) ---

/**
 * Initiate Claude OAuth — returns { authorize_url }.
 * POST /api/v1/oauth/claude/initiate
 */
export async function initiateClaudeOAuth() {
  const { data } = await api.post('/api/v1/oauth/claude/initiate');
  return data;
}

/**
 * Submit Claude OAuth callback — exchange code#state for tokens.
 * POST /api/v1/oauth/claude/callback
 * @param {string} callbackInput - Full URL, code#state, or code=X&state=Y
 * @returns {Promise<Object>} { success: true }
 */
export async function submitClaudeCallback(callbackInput) {
  const { data } = await api.post('/api/v1/oauth/claude/callback', { callback_input: callbackInput });
  return data;
}

/**
 * Check Claude OAuth connection status.
 * GET /api/v1/oauth/claude/status
 * Returns { connected, account_id, email, plan_type }
 */
export async function getClaudeOAuthStatus() {
  try {
    const { data } = await api.get('/api/v1/oauth/claude/status');
    return data;
  } catch {
    return { connected: false, account_id: null, email: null, plan_type: null };
  }
}

/**
 * Disconnect Claude OAuth — delete stored tokens.
 * DELETE /api/v1/oauth/claude
 */
export async function disconnectClaudeOAuth() {
  const { data } = await api.delete('/api/v1/oauth/claude');
  return data;
}

// --- News feed ---

/**
 * Fetch news articles from the native news endpoint.
 * GET /api/v1/news?tickers=...&limit=...&cursor=...
 * @param {{ tickers?: string[], limit?: number, cursor?: string }} opts
 * @returns {Promise<{ results: Array, count: number, next_cursor: string|null }>}
 */
export async function getNews({ tickers, limit = 20, cursor } = {}) {
  try {
    const params = {};
    if (tickers && tickers.length) params.tickers = tickers.join(',');
    if (limit) params.limit = limit;
    if (cursor) params.cursor = cursor;
    const { data } = await api.get('/api/v1/news', { params });
    return data || { results: [], count: 0, next_cursor: null };
  } catch (e) {
    console.error('[API] getNews failed:', e?.message);
    return { results: [], count: 0, next_cursor: null };
  }
}

/**
 * Fetch a single news article by ID (full detail).
 * GET /api/v1/news/:articleId
 */
export async function getNewsArticle(articleId) {
  const { data } = await api.get(`/api/v1/news/${encodeURIComponent(articleId)}`);
  return data;
}

// --- AI Insights ---

export async function getTodayInsights() {
  try {
    const { data } = await api.get('/api/v1/insights/today');
    return data?.insights || [];
  } catch (e) {
    console.error('[API] getTodayInsights failed:', e?.message);
    return [];
  }
}

export async function getInsightDetail(marketInsightId) {
  const { data } = await api.get(`/api/v1/insights/${encodeURIComponent(marketInsightId)}`);
  return data;
}

// --- InfoFlow (content feed — kept for PopularCard) ---

/**
 * Fetch InfoFlow results filtered by category.
 * GET /api/v1/infoflow/results?category={cat}&limit={limit}&offset={offset}
 */
export async function getInfoFlowResults(category, limit = 10, offset = 0) {
  try {
    const params = { limit, offset };
    if (category) params.category = category;
    const { data } = await api.get('/api/v1/infoflow/results', { params });
    return data || { results: [], total: 0, limit, offset, has_more: false };
  } catch (e) {
    console.error('[API] getInfoFlowResults failed:', e?.message);
    return { results: [], total: 0, limit, offset, has_more: false };
  }
}

/**
 * Fetch InfoFlow result detail by indexNumber.
 * GET /api/v1/infoflow/results/{indexNumber}
 */
export async function getInfoFlowDetail(indexNumber) {
  try {
    const { data } = await api.get(`/api/v1/infoflow/results/${encodeURIComponent(indexNumber)}`);
    return data;
  } catch (e) {
    console.error('[API] getInfoFlowDetail failed:', e?.message);
    return null;
  }
}

// --- Earnings Calendar ---

/**
 * GET /api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD
 * Returns { data: [{ symbol, date, epsEstimated, revenueEstimated, ... }], count }
 */
export async function getEarningsCalendar({ from, to } = {}) {
  try {
    const params = {};
    if (from) params.from = from;
    if (to) params.to = to;
    const { data } = await api.get('/api/v1/calendar/earnings', { params });
    return data || { data: [], count: 0 };
  } catch (e) {
    console.error('[API] getEarningsCalendar failed:', e?.message);
    return { data: [], count: 0 };
  }
}
