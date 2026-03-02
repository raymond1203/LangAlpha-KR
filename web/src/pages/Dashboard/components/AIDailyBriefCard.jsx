import React from 'react';
import { Sparkles, ArrowRight, BrainCircuit } from 'lucide-react';
import { motion } from 'framer-motion';
import { AI_DAILY_BRIEF } from '../data/mockData';

function TopicBadge({ text, trend }) {
  const styles = {
    up: {
      backgroundColor: 'var(--color-profit-soft)',
      color: 'var(--color-profit)',
      borderColor: 'var(--color-profit-soft)',
    },
    down: {
      backgroundColor: 'var(--color-loss-soft)',
      color: 'var(--color-loss)',
      borderColor: 'var(--color-loss-soft)',
    },
    neutral: {
      backgroundColor: 'var(--color-bg-tag)',
      color: 'var(--color-text-secondary)',
      borderColor: 'var(--color-bg-tag)',
    },
  };

  return (
    <span
      className="px-3 py-1.5 rounded-lg border text-xs font-medium"
      style={styles[trend] || styles.neutral}
    >
      #{text}
    </span>
  );
}

function AIDailyBriefCard() {
  const brief = AI_DAILY_BRIEF;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2 }}
      className="relative group rounded-3xl overflow-hidden border"
      style={{
        borderColor: 'var(--color-accent-overlay)',
        background: `linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-card) 60%, var(--color-accent-soft) 100%)`,
      }}
    >
      {/* Decorative brain icon */}
      <div className="absolute top-0 right-0 p-6 opacity-20 group-hover:opacity-40 transition-opacity pointer-events-none">
        <BrainCircuit size={120} style={{ color: 'var(--color-accent-primary)' }} />
      </div>

      <div className="relative z-10 p-8 flex flex-col md:flex-row gap-8 items-start">
        <div className="flex-1">
          {/* Badge + updated */}
          <div className="flex items-center gap-2 mb-4">
            <div
              className="px-3 py-1 rounded-full border flex items-center gap-2 text-xs font-semibold uppercase tracking-wider"
              style={{
                backgroundColor: 'var(--color-accent-soft)',
                borderColor: 'var(--color-accent-overlay)',
                color: 'var(--color-accent-light)',
              }}
            >
              <Sparkles size={12} />
              AI Generated Insight
            </div>
            <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              Updated {brief.updatedAgo}
            </span>
          </div>

          {/* Headline */}
          <h2
            className="text-3xl font-bold mb-4 leading-tight"
            style={{ color: 'var(--color-text-primary)' }}
          >
            {brief.headline}
          </h2>

          {/* Body */}
          <p
            className="mb-6 leading-relaxed max-w-2xl"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {brief.body}
          </p>

          {/* Topic badges */}
          <div className="flex flex-wrap gap-3">
            {brief.topics.map((topic) => (
              <TopicBadge key={topic.text} text={topic.text} trend={topic.trend} />
            ))}
          </div>
        </div>

        {/* CTA */}
        <div className="w-full md:w-auto flex flex-col items-end justify-between self-stretch">
          <button
            className="group/btn flex items-center gap-2 px-6 py-3 rounded-xl font-semibold transition-colors shadow-lg"
            style={{
              backgroundColor: 'var(--color-btn-primary-bg, var(--color-accent-primary))',
              color: 'var(--color-btn-primary-text, #fff)',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.9')}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
          >
            Read Full Brief
            <ArrowRight size={16} className="group-hover/btn:translate-x-1 transition-transform" />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

export default AIDailyBriefCard;
