import { memo } from 'react';
import { useTheme } from '@/contexts/ThemeContext';
import { TradingViewEmbed } from '@/pages/Dashboard/widgets/framework/TradingViewEmbed';
// FORK (#33): KR symbol (.KS / .KQ) 일 때 KRX prefix 변환 + KST timezone
import { getTimezoneForSymbol, toTradingViewSymbol } from '@/lib/marketTimezone';

// Map our interval keys to TradingView widget interval values
const TV_INTERVALS: Record<string, string> = {
  '1s': '1S',
  '1min': '1',
  '5min': '5',
  '15min': '15',
  '30min': '30',
  '1hour': '60',
  '4hour': '240',
  '1day': 'D',
  '1week': 'W',
  '1month': 'M',
};

interface TradingViewWidgetProps {
  symbol: string;
  interval?: string;
}

/**
 * TradingView Advanced Chart widget — wrapped via the upstream `TradingViewEmbed`
 * framework. FORK (#33): KR ticker (.KS/.KQ) 는 KRX:XXXXXX 로 변환, timezone 은
 * 사용자 OS 가 아니라 시장 timezone 을 사용 — 미국 사용자가 한국 차트 봐도 KST 봉
 * 시간 정확.
 */
function TradingViewWidget({ symbol, interval = '1day' }: TradingViewWidgetProps) {
  const { theme } = useTheme();
  const isLight = theme === 'light';
  const config = {
    symbol: toTradingViewSymbol(symbol),
    interval: TV_INTERVALS[interval] || 'D',
    timezone: getTimezoneForSymbol(symbol),
    style: '1',
    isTransparent: false,
    backgroundColor: isLight ? '#FFFCF9' : '#000000',
    gridColor: isLight ? '#E8E2DB' : '#1A1A1A',
    allow_symbol_change: false,
    hide_side_toolbar: false,
    hide_top_toolbar: false,
    withdateranges: true,
    details: false,
    calendar: false,
    studies: ['RSI@tv-basicstudies'],
  };

  return (
    <TradingViewEmbed
      scriptKey="advanced-chart"
      config={config}
      className="h-full w-full"
    />
  );
}

export default memo(TradingViewWidget);
