import React from 'react';
import { useNavigate } from 'react-router-dom';
import { LineChart, Line, ResponsiveContainer, YAxis, Tooltip } from 'recharts';
import { motion } from 'framer-motion';

function IndexCard({ index, delay }) {
  const navigate = useNavigate();
  const pos = index.isPositive;
  const ch = Number(index.change);
  const pct = Number(index.changePercent);
  const changeStr = ch.toFixed(2);
  const pctStr = '(' + (pos ? '+' : '') + pct.toFixed(2) + '%)';
  const chartData = (index.sparklineData || []).map((val, i) => ({ val, i }));

  const today = new Date();
  const dateStr = `${today.getMonth() + 1}/${today.getDate()}`;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: delay * 0.1 }}
      className="overflow-hidden rounded-2xl border transition-colors group flex flex-col cursor-pointer"
      style={{
        borderColor: 'var(--color-border-muted)',
        backgroundColor: 'var(--color-bg-card)',
      }}
      onClick={() => navigate(`/market?symbol=^${index.symbol}`)}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-default)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-muted)')}
    >
      {/* Header: name+date | price, symbol | change */}
      <div className="p-4 pb-0">
        <div className="flex justify-between items-start">
          <div>
            <div className="flex items-baseline gap-2">
              <h3
                className="text-base font-bold tracking-tight"
                style={{ color: 'var(--color-text-primary)' }}
              >
                {index.name}
              </h3>
              <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                {dateStr}
              </span>
            </div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
              ^{index.symbol}
            </div>
          </div>
          <div className="text-right">
            <div
              className="text-lg font-bold tracking-tight dashboard-mono"
              style={{ color: 'var(--color-text-primary)' }}
            >
              {Number(index.price).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </div>
            <div
              className="text-xs dashboard-mono"
              style={{ color: pos ? 'var(--color-profit)' : 'var(--color-loss)' }}
            >
              {changeStr} {pctStr}
            </div>
          </div>
        </div>
      </div>

      {/* Sparkline chart */}
      <div className="mt-2 px-1 pb-2 [&_*]:outline-none" style={{ height: 100 }}>
        {chartData.length > 1 ? (
          <ResponsiveContainer width="100%" height={100}>
            <LineChart data={chartData}>
              <Line
                type="monotone"
                dataKey="val"
                stroke={pos ? 'var(--color-profit)' : 'var(--color-loss)'}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
              <Tooltip content={() => null} cursor={false} />
              <YAxis domain={['dataMin', 'dataMax']} hide />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              No chart data
            </span>
          </div>
        )}
      </div>
    </motion.div>
  );
}

function IndexMovementCard({ indices = [], loading = false }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
      {loading
        ? Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="flex flex-col rounded-2xl animate-pulse border"
              style={{
                backgroundColor: 'var(--color-bg-card)',
                borderColor: 'var(--color-border-muted)',
              }}
            >
              <div className="p-4 pb-0">
                <div className="flex justify-between">
                  <div>
                    <div
                      className="h-4 rounded mb-1"
                      style={{ backgroundColor: 'var(--color-border-default)', width: 80 }}
                    />
                    <div
                      className="h-3 rounded"
                      style={{ backgroundColor: 'var(--color-border-default)', width: 40 }}
                    />
                  </div>
                  <div className="text-right">
                    <div
                      className="h-5 rounded mb-1"
                      style={{ backgroundColor: 'var(--color-border-default)', width: 80 }}
                    />
                    <div
                      className="h-3 rounded"
                      style={{ backgroundColor: 'var(--color-border-default)', width: 60 }}
                    />
                  </div>
                </div>
              </div>
              <div className="mt-2 px-1 pb-2 [&_*]:outline-none" style={{ height: 100 }}>
                <div
                  className="w-full h-full rounded"
                  style={{ backgroundColor: 'var(--color-border-default)', opacity: 0.3 }}
                />
              </div>
            </div>
          ))
        : indices.map((index, i) => (
            <IndexCard key={index.symbol} index={index} delay={i} />
          ))}
    </div>
  );
}

export default IndexMovementCard;
