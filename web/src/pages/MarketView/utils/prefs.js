// --- localStorage persistence helpers (shared across MarketView components) ---
const STORAGE_PREFIX = 'market-chart:';

export function loadPref(key, fallback) {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + key);
    return raw !== null ? JSON.parse(raw) : fallback;
  } catch { return fallback; }
}

export function savePref(key, value) {
  try { localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value)); } catch { /* noop */ }
}
