import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import enUS from './locales/en-US.json';
import koKR from './locales/ko-KR.json';
import zhCN from './locales/zh-CN.json';

// FORK: ko-KR 추가 — 한국 사용자 첫 인상 한국화
const SUPPORTED_LOCALES = ['en-US', 'ko-KR', 'zh-CN'] as const;

function detectLocale(): string {
  // 1. Explicit user choice persisted in localStorage
  const stored = localStorage.getItem('locale');
  if (stored && SUPPORTED_LOCALES.includes(stored as typeof SUPPORTED_LOCALES[number])) {
    return stored;
  }

  // 2. Browser language — try exact match first, then prefix match
  const browserLang = navigator.language; // e.g. "zh-CN", "zh-TW", "en-GB"
  if (SUPPORTED_LOCALES.includes(browserLang as typeof SUPPORTED_LOCALES[number])) {
    return browserLang;
  }
  const prefix = browserLang.split('-')[0]; // "zh", "en"
  const prefixMatch = SUPPORTED_LOCALES.find((l) => l.startsWith(prefix + '-'));
  if (prefixMatch) return prefixMatch;

  // 3. Fallback
  return 'en-US';
}

i18n.use(initReactI18next).init({
  resources: {
    'en-US': { translation: enUS },
    'ko-KR': { translation: koKR }, // FORK: 한국어
    'zh-CN': { translation: zhCN },
  },
  lng: detectLocale(),
  fallbackLng: 'en-US',
  interpolation: { escapeValue: false },
});

export default i18n;
