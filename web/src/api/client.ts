/**
 * Shared API client for backend REST calls.
 * Bearer token is set automatically via setTokenGetter (called from AuthContext).
 */
import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

const baseURL = import.meta.env.VITE_API_BASE_URL ?? '';

type TokenGetter = () => Promise<string | null>;

/** Async function that returns the current access token (set by AuthContext). */
let _getAccessToken: TokenGetter | null = null;

export function setTokenGetter(fn: TokenGetter) {
  _getAccessToken = fn;
}

export const api = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  if (_getAccessToken) {
    try {
      const token = await _getAccessToken();
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    } catch {
      /* proceed without auth */
    }
  }
  return config;
});

// Enrich 429 errors with structured rate limit info
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError & { status?: number; rateLimitInfo?: Record<string, unknown>; retryAfter?: number | null }) => {
    if (error.response?.status === 429) {
      const detail = (error.response.data as Record<string, unknown>)?.detail || {};
      error.status = 429;
      error.rateLimitInfo = typeof detail === 'object' ? detail as Record<string, unknown> : {};
      error.retryAfter = parseInt(error.response.headers?.['retry-after'] as string, 10) || null;
    }
    return Promise.reject(error);
  },
);
