import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import enUS from './locales/en-US.json';
import zhCN from './locales/zh-CN.json';

const SUPPORTED_LOCALES = ['en-US', 'zh-CN'] as const;

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
    'zh-CN': { translation: zhCN },
  },
  lng: detectLocale(),
  fallbackLng: 'en-US',
  interpolation: { escapeValue: false },
});

export default i18n;
