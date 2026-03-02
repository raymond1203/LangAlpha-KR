import React, { useState, useMemo } from 'react';
import { TrendingUp, Clock, Briefcase, Eye, Search, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const TABS = [
  { key: 'market', label: 'Market Pulse', icon: TrendingUp },
  { key: 'portfolio', label: 'Your Portfolio', icon: Briefcase },
  { key: 'watchlist', label: 'Your Watchlist', icon: Eye },
];

const DATE_RANGES = [
  { key: 'all', label: 'All' },
  { key: '1h', label: '1H' },
  { key: '6h', label: '6H' },
  { key: '24h', label: '24H' },
  { key: '7d', label: '7D' },
];

function parseRelativeTime(timeStr) {
  if (!timeStr) return null;
  const now = Date.now();
  const m = timeStr.match(/^(\d+)\s*(min|hr|hrs|hour|hours|day|days)/i);
  if (!m) return now;
  const val = parseInt(m[1], 10);
  const unit = m[2].toLowerCase();
  if (unit === 'min') return now - val * 60 * 1000;
  if (unit.startsWith('hr') || unit.startsWith('hour')) return now - val * 3600 * 1000;
  if (unit.startsWith('day')) return now - val * 86400 * 1000;
  return now;
}

function getDateRangeCutoff(key) {
  if (key === 'all') return 0;
  const now = Date.now();
  switch (key) {
    case '1h': return now - 3600 * 1000;
    case '6h': return now - 6 * 3600 * 1000;
    case '24h': return now - 24 * 3600 * 1000;
    case '7d': return now - 7 * 86400 * 1000;
    default: return 0;
  }
}

function NewsRow({ item, idx, onNewsClick }) {
  const sentiment = item.isHot ? 'positive' : 'neutral';
  const sentimentColor =
    sentiment === 'positive'
      ? 'var(--color-profit)'
      : 'var(--color-text-secondary)';
  const tickers = item.tickers?.length > 0 ? item.tickers : null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: idx * 0.05 }}
      onClick={() => onNewsClick?.(item.id)}
      className="group flex items-center gap-4 p-3 rounded-xl border border-transparent transition-all cursor-pointer"
      style={{ backgroundColor: 'transparent' }}
      onMouseEnter={(e) => {
        e.currentTarget.style.backgroundColor = 'var(--color-bg-hover)';
        e.currentTarget.style.borderColor = 'var(--color-border-muted)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.backgroundColor = 'transparent';
        e.currentTarget.style.borderColor = 'transparent';
      }}
    >
      {/* Thumbnail */}
      {item.image && (
        <div className="relative h-16 w-24 flex-shrink-0 overflow-hidden rounded-lg">
          <img
            src={item.image}
            alt=""
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
            onError={(e) => { e.target.style.display = 'none'; }}
          />
        </div>
      )}

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span
            className="w-1.5 h-1.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: sentimentColor }}
          />
          {item.favicon && (
            <img
              src={item.favicon}
              alt=""
              className="w-4 h-4 rounded flex-shrink-0"
              onError={(e) => { e.target.style.display = 'none'; }}
            />
          )}
          {item.source && (
            <span
              className="text-[10px] font-medium uppercase tracking-wide"
              style={{ color: 'var(--color-accent-light)' }}
            >
              {item.source}
            </span>
          )}
          <span
            className="text-xs flex items-center gap-1"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            <Clock size={10} /> {item.time}
          </span>
        </div>
        <h3
          className="text-sm font-medium truncate transition-colors"
          style={{ color: 'var(--color-text-primary)' }}
          title={item.title}
        >
          {item.title}
        </h3>
        {tickers && (
          <div className="flex items-center gap-1.5 mt-1">
            {tickers.slice(0, 4).map((t) => (
              <span
                key={t}
                className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                style={{
                  backgroundColor: 'var(--color-accent-soft)',
                  color: 'var(--color-accent-light)',
                }}
              >
                {t}
              </span>
            ))}
            {tickers.length > 4 && (
              <span className="text-[10px]" style={{ color: 'var(--color-text-secondary)' }}>
                +{tickers.length - 4}
              </span>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
}

function SkeletonRows({ count = 6 }) {
  return Array.from({ length: count }).map((_, idx) => (
    <div key={idx} className="flex items-center gap-4 p-3 animate-pulse">
      <div
        className="h-16 w-24 flex-shrink-0 rounded-lg"
        style={{ backgroundColor: 'var(--color-border-default)' }}
      />
      <div className="flex-1 min-w-0">
        <div className="h-3 rounded mb-2" style={{ backgroundColor: 'var(--color-border-default)', width: '40%' }} />
        <div className="h-4 rounded" style={{ backgroundColor: 'var(--color-border-default)', width: `${60 + (idx % 3) * 15}%` }} />
      </div>
    </div>
  ));
}

function EmptyState({ tab, hasFilters }) {
  if (hasFilters) {
    return (
      <div className="flex items-center justify-center py-16">
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          No news matching your filters
        </p>
      </div>
    );
  }

  const messages = {
    market: 'No market news available',
    portfolio: 'Add stocks to your portfolio to see relevant news here',
    watchlist: 'Add stocks to your watchlist to see relevant news here',
  };

  return (
    <div className="flex items-center justify-center py-16">
      <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        {messages[tab] || 'No news available'}
      </p>
    </div>
  );
}

function NewsFeedCard({
  marketItems = [],
  marketLoading = false,
  portfolioItems = [],
  portfolioLoading = false,
  watchlistItems = [],
  watchlistLoading = false,
  onNewsClick,
}) {
  const [activeTab, setActiveTab] = useState('market');
  const [tickerFilter, setTickerFilter] = useState('');
  const [dateRange, setDateRange] = useState('all');

  const dataMap = {
    market: { items: marketItems, loading: marketLoading },
    portfolio: { items: portfolioItems, loading: portfolioLoading },
    watchlist: { items: watchlistItems, loading: watchlistLoading },
  };

  const { items, loading } = dataMap[activeTab];

  const hasFilters = tickerFilter.trim() !== '' || dateRange !== 'all';

  const filteredItems = useMemo(() => {
    let result = items;

    // Ticker filter
    const query = tickerFilter.trim().toUpperCase();
    if (query) {
      result = result.filter((item) =>
        item.tickers?.some((t) => t.toUpperCase().includes(query))
      );
    }

    // Date range filter
    if (dateRange !== 'all') {
      const cutoff = getDateRangeCutoff(dateRange);
      result = result.filter((item) => {
        const ts = parseRelativeTime(item.time);
        return ts !== null && ts >= cutoff;
      });
    }

    return result;
  }, [items, tickerFilter, dateRange]);

  // Collect unique tickers across current items for quick-pick
  const availableTickers = useMemo(() => {
    const set = new Set();
    for (const item of items) {
      if (item.tickers) item.tickers.forEach((t) => set.add(t));
    }
    return [...set].sort();
  }, [items]);

  return (
    <div className="dashboard-glass-card p-6">
      {/* Header: tabs + filters on same row */}
      <div
        className="flex items-center justify-between gap-3 pb-4 mb-4 border-b flex-wrap"
        style={{ borderColor: 'var(--color-border-muted)' }}
      >
        {/* Tabs */}
        <div className="flex items-center gap-1 p-1 rounded-lg" style={{ backgroundColor: 'var(--color-bg-tag)' }}>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => {
                  setActiveTab(tab.key);
                  setTickerFilter('');
                  setDateRange('all');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all"
                style={{
                  backgroundColor: isActive ? 'var(--color-bg-elevated)' : 'transparent',
                  color: isActive ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                }}
              >
                <Icon size={13} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2">
          {/* Ticker search */}
          <div
            className="flex items-center gap-1.5 h-7 px-2 rounded-lg border"
            style={{
              backgroundColor: 'var(--color-bg-input, var(--color-bg-tag))',
              borderColor: 'var(--color-border-muted)',
              width: tickerFilter ? 160 : 130,
              transition: 'width 0.2s',
            }}
          >
            <Search size={12} style={{ color: 'var(--color-text-secondary)', flexShrink: 0 }} />
            <input
              type="text"
              placeholder="Ticker..."
              value={tickerFilter}
              onChange={(e) => setTickerFilter(e.target.value)}
              className="flex-1 text-[11px] bg-transparent border-none outline-none min-w-0"
              style={{ color: 'var(--color-text-primary)' }}
            />
            {tickerFilter && (
              <button
                onClick={() => setTickerFilter('')}
                className="flex-shrink-0"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                <X size={11} />
              </button>
            )}
          </div>

          {/* Date range pills */}
          <div className="flex items-center gap-0.5 p-0.5 rounded-lg" style={{ backgroundColor: 'var(--color-bg-tag)' }}>
            {DATE_RANGES.map((dr) => {
              const isActive = dateRange === dr.key;
              return (
                <button
                  key={dr.key}
                  onClick={() => setDateRange(dr.key)}
                  className="px-2 py-1 rounded-md text-[11px] font-medium transition-all"
                  style={{
                    backgroundColor: isActive ? 'var(--color-bg-elevated)' : 'transparent',
                    color: isActive ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                  }}
                >
                  {dr.label}
                </button>
              );
            })}
          </div>

          {/* Clear */}
          {hasFilters && (
            <button
              onClick={() => { setTickerFilter(''); setDateRange('all'); }}
              style={{ color: 'var(--color-text-secondary)' }}
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Tab content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.2 }}
          className="flex flex-col gap-1"
        >
          {loading ? (
            <SkeletonRows />
          ) : filteredItems.length === 0 ? (
            <EmptyState tab={activeTab} hasFilters={hasFilters} />
          ) : (
            filteredItems.map((item, idx) => (
              <NewsRow key={item.id || idx} item={item} idx={idx} onNewsClick={onNewsClick} />
            ))
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

export default NewsFeedCard;
