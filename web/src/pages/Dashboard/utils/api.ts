/**
 * Dashboard API utilities
 * All backend endpoints used by the Dashboard page
 */
import { api } from '@/api/client';
import { utcMsToTzDate, utcMsToTzTime } from '@/lib/utils';
import * as portfolioApi from './portfolio';
import * as watchlistApi from './watchlist';
import * as watchlistItemsApi from './watchlistItems';

// --- Interfaces ---

interface IntradayPoint {
  time: number;
  open: number;
  close: number;
  high?: number;
  low?: number;
  volume?: number;
}

interface SparklinePoint {
  time: string;
  val: number;
}

interface IndexData {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  isPositive: boolean;
  sparklineData: SparklinePoint[];
  previousClose?: number | null;
}

interface StockPrice {
  symbol: string;
  price: number;
  change: number;
  changePercent: number;
  isPositive: boolean;
  previousClose?: number | null;
  earlyTradingChangePercent?: number | null;
  lateTradingChangePercent?: number | null;
}

interface SnapshotEntry {
  symbol: string;
  name?: string;
  price?: number;
  change?: number;
  change_percent?: number;
  previous_close?: number;
  early_trading_change_percent?: number;
  late_trading_change_percent?: number;
}

interface SnapshotResponse {
  snapshots?: SnapshotEntry[];
  results?: SnapshotEntry[];
  data?: SnapshotEntry[];
}

interface IndicesResult {
  indices: IndexData[];
  failedCount: number;
}

interface NewsParams {
  tickers?: string[];
  limit?: number;
  cursor?: string;
}

interface NewsResponse {
  results: Record<string, unknown>[];
  count: number;
  next_cursor: string | null;
}

interface EarningsParams {
  from?: string;
  to?: string;
}

interface EarningsResponse {
  data: Record<string, unknown>[];
  count: number;
}

interface InfoFlowResponse {
  results: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

// --- Market data (see docs/ptc-agent-api/market data) ---

/** Index symbols: normalized (GSPC, IXIC, DJI, RUT). Index.yml / Index Batch.yml use these. */
const INDEX_SYMBOLS: string[] = ['GSPC', 'IXIC', 'DJI', 'RUT', 'VIX'];
const INDEX_NAMES: Record<string, string> = { GSPC: 'S&P 500', IXIC: 'NASDAQ', DJI: 'Dow Jones', RUT: 'Russell 2000', VIX: 'VIX' };

// FORK: 한국 시장 인덱스 set — yfinance ^KS11/^KQ11/^KS200 로 자동 prefix 됨.
// 백엔드 yfinance source 가 indices asset_type 일 때 ^ prefix 를 자동 추가하므로 여기서는 bare 심볼만 둠.
// V-KOSPI(^VKOSPI) 는 yfinance 내부 PriceHistory 버그로 현재 fetch 불가 — 별도 데이터 소스 도입 후 추가 예정.
const KR_INDEX_SYMBOLS: string[] = ['KS11', 'KQ11', 'KS200'];
const KR_INDEX_NAMES: Record<string, string> = {
  KS11: '코스피',
  KQ11: '코스닥',
  KS200: '코스피 200',
};

const _KR_INDEX_SET = new Set(KR_INDEX_SYMBOLS);

/**
 * 정규화된 심볼 기반으로 거래소 시간대를 도출. locale 에 의존하지 않음 —
 * 심볼이 KR set 에 속하면 KST, 아니면 ET (NYSE/NASDAQ).
 */
function getIndexTimezone(norm: string): string {
  return _KR_INDEX_SET.has(norm) ? 'Asia/Seoul' : 'America/New_York';
}

/** 정규장 시간 (HH:MM 24h, 거래소 로컬). KRX 09:00-15:30, NYSE/NASDAQ 09:30-16:00. */
function getIndexMarketHours(norm: string): { open: string; close: string } {
  return _KR_INDEX_SET.has(norm)
    ? { open: '09:00', close: '15:30' }
    : { open: '09:30', close: '16:00' };
}

/**
 * locale 에 맞는 인덱스 ticker set + 표시명 반환.
 * ko 로 시작하는 locale → KR set, 그 외 → US set.
 */
export function getIndexSetForLocale(locale: string | undefined | null): { symbols: string[]; names: Record<string, string> } {
  if (locale && locale.toLowerCase().startsWith('ko')) {
    return { symbols: KR_INDEX_SYMBOLS, names: KR_INDEX_NAMES };
  }
  return { symbols: INDEX_SYMBOLS, names: INDEX_NAMES };
}

function normalizeIndexSymbol(s: string): string {
  return String(s).replace(/^\^/, '').toUpperCase();
}

/**
 * 정규화된 심볼의 표시명 도출. US set → KR set → 호출자 fallback (예: snapshot.name) → 심볼 자체 순서로 resolve.
 */
function getIndexDisplayName(norm: string, fallback?: string | null): string {
  return INDEX_NAMES[norm] ?? KR_INDEX_NAMES[norm] ?? fallback ?? norm;
}

function fallbackIndex(norm: string): IndexData {
  return {
    symbol: norm,
    name: getIndexDisplayName(norm),
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
export async function getIndex(symbol: string, _opts: Record<string, unknown> = {}): Promise<IndexData> {
  const norm = normalizeIndexSymbol(String(symbol).trim());
  try {
    const { data } = await api.get(`/api/v1/market-data/intraday/indexes/${encodeURIComponent(norm)}`);

    const pts: IntradayPoint[] = data?.data ?? [];

    if (!Array.isArray(pts) || !pts.length) {
      throw new Error(`No intraday data for ${norm}`);
    }

    // Sort ascending by time (Unix ms)
    const sorted = [...pts].sort((a: IntradayPoint, b: IntradayPoint) => a.time - b.time);

    // FORK: 심볼별 거래소 시간대로 정규장 시간 필터링 (KR: KST 09:00-15:30, US: ET 09:30-16:00).
    const tz = getIndexTimezone(norm);
    const hours = getIndexMarketHours(norm);
    const latestDate = utcMsToTzDate(sorted[sorted.length - 1].time, tz);
    const todayPoints = sorted.filter((p: IntradayPoint) => {
      if (utcMsToTzDate(p.time, tz) !== latestDate) return false;
      const t = utcMsToTzTime(p.time, tz);
      return t >= hours.open && t <= hours.close;
    });

    const oldest = todayPoints[0];
    const mostRecent = todayPoints[todayPoints.length - 1];

    const open = Number(oldest?.open ?? 0);
    const close = Number(mostRecent?.close ?? 0);
    const change = close - open;
    const changePercent = open ? (change / open) * 100 : 0;

    const result: IndexData = {
      symbol: norm,
      name: getIndexDisplayName(norm),
      price: Math.round(close * 100) / 100,
      change: Math.round(change * 100) / 100,
      changePercent: Math.round(changePercent * 100) / 100,
      isPositive: change >= 0,
      sparklineData: todayPoints
        .filter((p: IntradayPoint) => Number(p.close) > 0)
        .map((p: IntradayPoint) => ({ time: utcMsToTzTime(p.time, tz), val: Number(p.close) })),
    };

    return result;
  } catch (e: unknown) {
    const err = e as { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
    console.error(`[API] getIndex - ${norm}: Error:`, err?.message);
    const msg = err.response?.data?.detail ?? err.message;
    throw new Error(typeof msg === 'string' ? msg : String(msg));
  }
}

/**
 * Fetches indices data: snapshot batch for price/change, intraday for sparklines.
 * Returns { indices, failedCount }.
 */
export async function getIndices(symbols: string[] = INDEX_SYMBOLS, _opts: Record<string, unknown> = {}): Promise<IndicesResult> {
  const list = symbols.map((s: string) => normalizeIndexSymbol(String(s).trim()));

  // Fetch snapshot (price/change) and intraday (sparklines) in parallel
  const [snapshots, sparklineResults] = await Promise.all([
    getSnapshotIndexes(list),
    Promise.all(list.map(async (norm: string) => {
      try {
        const result = await getIndex(norm);
        return { symbol: norm, sparklineData: result.sparklineData };
      } catch {
        return { symbol: norm, sparklineData: [] as SparklinePoint[] };
      }
    })),
  ]);

  const sparklineMap: Record<string, SparklinePoint[]> = Object.fromEntries(sparklineResults.map((r) => [r.symbol, r.sparklineData]));
  const snapshotList: SnapshotEntry[] = snapshots?.snapshots || snapshots?.results || snapshots?.data || [];
  const snapshotMap: Record<string, SnapshotEntry> = Array.isArray(snapshotList)
    ? Object.fromEntries(snapshotList.map((s: SnapshotEntry) => [normalizeIndexSymbol(s.symbol), s]))
    : {};

  let failedCount = 0;
  const indices: IndexData[] = list.map((norm: string) => {
    const snap = snapshotMap[norm];
    if (snap && snap.price != null) {
      const change = snap.change ?? 0;
      const changePct = snap.change_percent ?? (snap.previous_close ? ((change / snap.previous_close) * 100) : 0);
      return {
        symbol: norm,
        name: getIndexDisplayName(norm, snap.name),
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

export {
  INDEX_NAMES,
  INDEX_SYMBOLS,
  KR_INDEX_NAMES,
  KR_INDEX_SYMBOLS,
  fallbackIndex,
  normalizeIndexSymbol,
};

// --- Hello ---

export async function fetchHello(): Promise<string> {
  const { data } = await api.get('/hello', { responseType: 'text' });
  return data;
}

// --- Users ---

export async function createUser(userData: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/users', userData);
  return data;
}

export async function getCurrentUser(params?: { refresh_tier?: boolean }): Promise<Record<string, unknown>> {
  const { data } = await api.get('/api/v1/users/me', { params });
  return data;
}

export async function getPreferences(): Promise<Record<string, unknown> | null> {
  try {
    const { data } = await api.get('/api/v1/users/me/preferences');
    return data;
  } catch (e: unknown) {
    const err = e as { response?: { status?: number; data?: unknown }; message?: string };
    if (err.response?.status === 404) return null;
    throw e;
  }
}

export async function updateCurrentUser(userData: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { data } = await api.put('/api/v1/users/me', userData);
  return data;
}

export async function updatePreferences(preferences: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { data } = await api.put('/api/v1/users/me/preferences', preferences);
    return data;
}

export async function clearPreferences(): Promise<Record<string, unknown>> {
  const { data } = await api.delete('/api/v1/users/me/preferences');
  return data;
}

export async function uploadAvatar(file: File): Promise<{ avatar_url: string }> {
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
export async function getWatchlistItems(): Promise<unknown> {
  return watchlistItemsApi.listWatchlistItems('default');
}

/**
 * Adds a stock to a watchlist with full details
 * @param {Object} itemData - Stock item data: { symbol, instrument_type, exchange, name, notes, alert_settings }
 * @param {string} watchlistId - The watchlist ID (UUID or 'default')
 * @returns {Promise<Object>} Created watchlist item
 */
export async function addWatchlistItem(itemData: Record<string, unknown>, watchlistId: string = 'default'): Promise<unknown> {
  return watchlistItemsApi.addWatchlistItem(watchlistId, itemData as unknown as watchlistItemsApi.AddWatchlistItemPayload);
}

/**
 * Deletes a watchlist item by ID
 * @param {string} itemId - The item ID to delete
 * @param {string} watchlistId - The watchlist ID (UUID or 'default')
 */
export async function deleteWatchlistItem(itemId: string, watchlistId: string = 'default'): Promise<void> {
  return watchlistItemsApi.deleteWatchlistItem(watchlistId, itemId);
}

// --- Snapshot & market status ---

/**
 * GET /api/v1/market-data/snapshots/indexes?symbols=GSPC,IXIC,...
 * Returns batch snapshot for index symbols.
 */
export async function getSnapshotIndexes(symbols: string[] = INDEX_SYMBOLS): Promise<SnapshotResponse> {
  const list = symbols.map((s: string) => normalizeIndexSymbol(String(s).trim()));
  try {
    const { data } = await api.get('/api/v1/market-data/snapshots/indexes', {
      params: { symbols: list.join(',') },
    });
    return data || {};
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getSnapshotIndexes failed:', err?.message);
    return {};
  }
}

/**
 * GET /api/v1/market-data/snapshots/stocks?symbols=AAPL,TSLA,...
 * Returns batch snapshot for stock symbols.
 */
export async function getSnapshotStocks(symbols: string[]): Promise<SnapshotResponse> {
  const list = [...(symbols || [])].map((s: string) => String(s).trim().toUpperCase()).filter(Boolean);
  if (!list.length) return {};
  try {
    const { data } = await api.get('/api/v1/market-data/snapshots/stocks', {
      params: { symbols: list.join(',') },
    });
    return data || {};
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getSnapshotStocks failed:', err?.message);
    return {};
  }
}

// --- Stock prices (batch, for watchlist) ---

const DEFAULT_WATCHLIST_SYMBOLS: string[] = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'TSLA'];
const DEFAULT_WATCHLIST_NAMES: Record<string, string> = { AAPL: 'Apple', MSFT: 'Microsoft', NVDA: 'NVIDIA', AMZN: 'Amazon', TSLA: 'Tesla' };

export { DEFAULT_WATCHLIST_SYMBOLS, DEFAULT_WATCHLIST_NAMES };

/**
 * Get company names for a list of stock symbols (FMP profile companyName).
 * @param {string[]} symbols - e.g. ['AAPL', 'MSFT']
 * @returns {Promise<Record<string, string>>} symbol -> company name
 */
export async function getStockCompanyNames(symbols: string[]): Promise<Record<string, string>> {
  const list = [...(symbols || [])].map((s: string) => String(s).trim().toUpperCase()).filter(Boolean);
  if (!list.length) return {};
  try {
    const { data } = await api.post('/api/v1/market-data/stocks/names', { symbols: list });
    return data?.names ?? {};
  } catch {
    return {};
  }
}

export async function getStockPrices(symbols: string[]): Promise<StockPrice[]> {
  const list = [...(symbols || [])].map((s: string) => String(s).trim().toUpperCase()).filter(Boolean);
  if (!list.length) return [];
  try {
    const snapshots = await getSnapshotStocks(list);
    const snapList: SnapshotEntry[] = snapshots?.snapshots || snapshots?.results || snapshots?.data || [];
    const snapMap: Record<string, SnapshotEntry> = Array.isArray(snapList)
      ? Object.fromEntries(snapList.map((s: SnapshotEntry) => [String(s.symbol).toUpperCase(), s]))
      : {};

    return list.map((sym: string) => {
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
    return list.map((sym: string) => ({ symbol: sym, price: 0, change: 0, changePercent: 0, isPositive: true }));
  }
}

// --- Portfolio (use CRUD module) ---

export const listPortfolio = portfolioApi.listPortfolio;
export const updatePortfolioHolding = portfolioApi.updatePortfolioHolding;
export const deletePortfolioHolding = portfolioApi.deletePortfolioHolding;

export const getPortfolio = portfolioApi.listPortfolio;

/** Add portfolio holding. Payload: symbol, instrument_type, quantity, average_cost?, ... */
export const addPortfolioHolding = portfolioApi.addPortfolioHolding;

// --- Models & BYOK API Keys (moved to shared api/model.ts) ---

export { getAvailableModels, getUserApiKeys, updateUserApiKeys, deleteUserApiKey } from '@/api/model';

// --- OAuth (Connected Accounts) ---

/**
 * Start Codex device code flow — returns { user_code, verification_url, interval }.
 * POST /api/v1/oauth/codex/device/initiate
 */
export async function initiateCodexDevice(): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/oauth/codex/device/initiate');
  return data;
}

/**
 * Poll for device authorization approval.
 * POST /api/v1/oauth/codex/device/poll
 * @returns {Promise<Object>} { pending: true } or { success: true, email, plan_type, account_id }
 */
export async function pollCodexDevice(): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/oauth/codex/device/poll');
  return data;
}

/**
 * Check Codex OAuth connection status.
 * GET /api/v1/oauth/codex/status
 * Returns { connected, account_id, email, plan_type }
 */
export async function getCodexOAuthStatus(): Promise<{ connected: boolean; account_id: string | null; email: string | null; plan_type: string | null }> {
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
export async function disconnectCodexOAuth(): Promise<Record<string, unknown>> {
  const { data } = await api.delete('/api/v1/oauth/codex');
  return data;
}

// --- Claude OAuth (PKCE Authorization Code Flow) ---

/**
 * Initiate Claude OAuth — returns { authorize_url }.
 * POST /api/v1/oauth/claude/initiate
 */
export async function initiateClaudeOAuth(): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/oauth/claude/initiate');
  return data;
}

/**
 * Submit Claude OAuth callback — exchange code#state for tokens.
 * POST /api/v1/oauth/claude/callback
 * @param {string} callbackInput - Full URL, code#state, or code=X&state=Y
 * @returns {Promise<Object>} { success: true }
 */
export async function submitClaudeCallback(callbackInput: string): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/oauth/claude/callback', { callback_input: callbackInput });
  return data;
}

/**
 * Check Claude OAuth connection status.
 * GET /api/v1/oauth/claude/status
 * Returns { connected, account_id, email, plan_type }
 */
export async function getClaudeOAuthStatus(): Promise<{ connected: boolean; account_id: string | null; email: string | null; plan_type: string | null }> {
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
export async function disconnectClaudeOAuth(): Promise<Record<string, unknown>> {
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
export async function getNews({ tickers, limit = 20, cursor }: NewsParams = {}): Promise<NewsResponse> {
  try {
    const params: Record<string, string | number> = {};
    if (tickers && tickers.length) params.tickers = tickers.join(',');
    if (limit) params.limit = limit;
    if (cursor) params.cursor = cursor;
    const { data } = await api.get('/api/v1/news', { params });
    return data || { results: [], count: 0, next_cursor: null };
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getNews failed:', err?.message);
    return { results: [], count: 0, next_cursor: null };
  }
}

/**
 * Fetch a single news article by ID (full detail).
 * GET /api/v1/news/:articleId
 */
export async function getNewsArticle(articleId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/api/v1/news/${encodeURIComponent(articleId)}`);
  return data;
}

// --- AI Insights ---

export async function getTodayInsights(): Promise<Record<string, unknown>[]> {
  try {
    const { data } = await api.get('/api/v1/insights/today');
    return data?.insights || [];
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getTodayInsights failed:', err?.message);
    return [];
  }
}

export async function getInsightDetail(marketInsightId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/api/v1/insights/${encodeURIComponent(marketInsightId)}`);
  return data;
}

export async function generatePersonalizedInsight(): Promise<Record<string, unknown>> {
  const { data } = await api.post('/api/v1/insights/generate');
  return data;
}

// --- InfoFlow (content feed — kept for PopularCard) ---

/**
 * Fetch InfoFlow results filtered by category.
 * GET /api/v1/infoflow/results?category={cat}&limit={limit}&offset={offset}
 */
export async function getInfoFlowResults(category: string, limit: number = 10, offset: number = 0): Promise<InfoFlowResponse> {
  try {
    const params: Record<string, string | number> = { limit, offset };
    if (category) params.category = category;
    const { data } = await api.get('/api/v1/infoflow/results', { params });
    return data || { results: [], total: 0, limit, offset, has_more: false };
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getInfoFlowResults failed:', err?.message);
    return { results: [], total: 0, limit, offset, has_more: false };
  }
}

/**
 * Fetch InfoFlow result detail by indexNumber.
 * GET /api/v1/infoflow/results/{indexNumber}
 */
export async function getInfoFlowDetail(indexNumber: string): Promise<Record<string, unknown> | null> {
  try {
    const { data } = await api.get(`/api/v1/infoflow/results/${encodeURIComponent(indexNumber)}`);
    return data;
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getInfoFlowDetail failed:', err?.message);
    return null;
  }
}

// --- Earnings Calendar ---

/**
 * GET /api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD
 * Returns { data: [{ symbol, date, epsEstimated, revenueEstimated, ... }], count }
 */
export async function getEarningsCalendar({ from, to }: EarningsParams = {}): Promise<EarningsResponse> {
  try {
    const params: Record<string, string> = {};
    if (from) params.from = from;
    if (to) params.to = to;
    const { data } = await api.get('/api/v1/calendar/earnings', { params });
    return data || { data: [], count: 0 };
  } catch (e: unknown) {
    const err = e as { message?: string };
    console.error('[API] getEarningsCalendar failed:', err?.message);
    return { data: [], count: 0 };
  }
}
