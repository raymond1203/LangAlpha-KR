import React, { useEffect, useRef, memo } from 'react';
import { useTheme } from '@/contexts/ThemeContext';
// FORK (#33): KR symbol (.KS / .KQ) 일 때 KRX prefix 변환 + KST timezone
import { getTimezoneForSymbol, toTradingViewSymbol } from '@/lib/marketTimezone';

// Map our interval keys to TradingView widget interval values
const TV_INTERVALS: Record<string, string> = {
  '1min': '1',
  '5min': '5',
  '15min': '15',
  '30min': '30',
  '1hour': '60',
  '4hour': '240',
  '1day': 'D',
};

interface TradingViewWidgetProps {
  symbol: string;
  interval?: string;
}

/**
 * TradingView Advanced Chart widget embed.
 * Provides full drawing tools, indicators, and TradingView's own real-time data.
 */
function TradingViewWidget({ symbol, interval = '1day' }: TradingViewWidgetProps) {
  const { theme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const scriptRef = useRef<HTMLScriptElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Clear previous widget
    containerRef.current.innerHTML = '';

    // Create the widget container div that TradingView expects
    const widgetDiv = document.createElement('div');
    widgetDiv.className = 'tradingview-widget-container__widget';
    widgetDiv.style.height = '100%';
    widgetDiv.style.width = '100%';
    containerRef.current.appendChild(widgetDiv);

    // Create and configure the embed script
    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      // FORK (#33): KR ticker (.KS/.KQ) 는 KRX:XXXXXX 형태로 변환, US 는 그대로.
      // 사용자 OS timezone 대신 시장 timezone 사용 — 미국 사용자가 한국 차트
      // 보면 KST 봉 시간이 정확.
      symbol: toTradingViewSymbol(symbol),
      interval: TV_INTERVALS[interval] || 'D',
      timezone: getTimezoneForSymbol(symbol),
      theme: theme === 'light' ? 'light' : 'dark',
      style: '1', // Candlestick
      locale: 'en',
      backgroundColor: theme === 'light' ? '#FFFCF9' : '#000000',
      gridColor: theme === 'light' ? '#E8E2DB' : '#1A1A1A',
      allow_symbol_change: false,
      hide_side_toolbar: false, // Keep drawing tools visible
      hide_top_toolbar: false,
      withdateranges: true,
      details: false,
      calendar: false,
      studies: ['RSI@tv-basicstudies'],
      support_host: 'https://www.tradingview.com',
    });
    scriptRef.current = script;
    containerRef.current.appendChild(script);

    return () => {
      // Cleanup: remove widget DOM
      if (containerRef.current) {
        containerRef.current.innerHTML = '';
      }
      scriptRef.current = null;
    };
  }, [symbol, interval, theme]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container"
      style={{ height: '100%', width: '100%' }}
    />
  );
}

export default memo(TradingViewWidget);
