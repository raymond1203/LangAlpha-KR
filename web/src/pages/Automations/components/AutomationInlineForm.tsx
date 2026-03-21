import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { searchStocks } from '@/lib/marketUtils';
import CronScheduleBuilder from './CronScheduleBuilder';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import {
  type FormState,
  COMMON_TIMEZONES,
  PRICE_CONDITION_TYPES,
  PRICE_REFERENCE_OPTIONS,
  RETRIGGER_MODES,
  isPctCondition,
  formStateToPayload,
} from '../utils/templates';

// ── Shared styles ──────────────────────────────────────────

const inputStyle = {
  backgroundColor: 'var(--color-bg-card)',
  borderColor: 'var(--color-border-default)',
};

const labelClass = 'form-label';
const radioGroupClass = 'flex flex-wrap gap-x-3 gap-y-2';

// ── RadioOption ────────────────────────────────────────────

interface RadioOptionProps {
  name: string;
  value: string;
  checked: boolean;
  onChange: (value: string) => void;
  label: string;
}

function RadioOption({ name, value, checked, onChange, label }: RadioOptionProps) {
  return (
    <label className="flex items-center gap-2 cursor-pointer text-sm" style={{ color: 'var(--color-text-secondary)' }}>
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
        className="accent-[var(--color-accent-primary)]"
      />
      {label}
    </label>
  );
}

// ── TickerAutocomplete ─────────────────────────────────────

const INDEX_SYMBOLS: Array<{ symbol: string; name: string }> = [
  { symbol: 'SPX', name: 'S&P 500' },
  { symbol: 'DJI', name: 'Dow Jones Industrial Average' },
  { symbol: 'COMP', name: 'Nasdaq Composite' },
  { symbol: 'NDX', name: 'Nasdaq 100' },
  { symbol: 'RUT', name: 'Russell 2000' },
  { symbol: 'VIX', name: 'CBOE Volatility Index' },
];

const INDEX_SYMBOL_SET = new Set(INDEX_SYMBOLS.map((i) => i.symbol));

export function isIndexSymbol(symbol: string): boolean {
  return INDEX_SYMBOL_SET.has(symbol.toUpperCase());
}

interface SearchResult { symbol: string; name?: string; exchangeShortName?: string; isIndex?: boolean }

interface TickerAutocompleteProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

function TickerAutocomplete({ value, onChange, placeholder = 'AAPL, SPX...' }: TickerAutocompleteProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState(value);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Sync external value changes (e.g., template switch)
  useEffect(() => {
    setQuery(value);
  }, [value]);

  // Debounced search — merge index matches + stock API results
  useEffect(() => {
    if (!query || query.length < 1) {
      setResults([]);
      return;
    }
    const q = query.toUpperCase();

    // Instant: filter static index list
    const indexMatches: SearchResult[] = INDEX_SYMBOLS
      .filter((idx) => idx.symbol.includes(q) || idx.name.toUpperCase().includes(q))
      .map((idx) => ({ symbol: idx.symbol, name: idx.name, exchangeShortName: 'INDEX', isIndex: true }));

    // Show index matches immediately
    if (indexMatches.length > 0) {
      setResults(indexMatches);
      setShowDropdown(true);
    }

    // Debounced: fetch stock results and merge
    const timer = setTimeout(async () => {
      const data = await searchStocks(query, 20);
      const stockResults: SearchResult[] = (data.results as SearchResult[]).map((r) => ({ ...r, isIndex: false }));
      // Deduplicate: index symbols already shown won't appear in stock results (different symbols)
      setResults([...indexMatches, ...stockResults]);
      if (indexMatches.length > 0 || stockResults.length > 0) setShowDropdown(true);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Click outside to close
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <Input
        value={query}
        onChange={(e) => {
          const val = e.target.value.toUpperCase();
          setQuery(val);
          onChange(val);
          if (val.length >= 1) setShowDropdown(true);
        }}
        onFocus={() => { if (results.length > 0) setShowDropdown(true); }}
        placeholder={placeholder}
        required
        className="placeholder:text-gray-500 border font-mono uppercase"
        style={inputStyle}
      />
      {showDropdown && results.length > 0 && (
        <div
          className="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto rounded-md border shadow-lg"
          style={{
            backgroundColor: 'var(--color-bg-elevated)',
            borderColor: 'var(--color-border-default)',
          }}
        >
          {results.map((item) => (
            <button
              key={item.symbol}
              type="button"
              className="w-full text-left px-3 py-2 text-sm hover:bg-foreground/5 flex items-center gap-2"
              onClick={() => {
                setQuery(item.symbol);
                onChange(item.symbol);
                setShowDropdown(false);
              }}
            >
              <span className="font-mono font-medium" style={{ color: 'var(--color-text-primary)' }}>
                {item.symbol}
              </span>
              {item.name && (
                <span className="truncate" style={{ color: 'var(--color-text-tertiary)' }}>
                  {item.name}
                  {item.exchangeShortName ? ` (${item.exchangeShortName})` : ''}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Workspace type ─────────────────────────────────────────

interface WorkspaceOption {
  workspace_id: string;
  name: string;
}

// ── AutomationInlineForm ───────────────────────────────────

interface AutomationInlineFormProps {
  initialValues: FormState;
  isEdit: boolean;
  onSubmit: (payload: Record<string, unknown>) => void;
  onCancel: () => void;
  loading: boolean;
}

export default function AutomationInlineForm({
  initialValues,
  isEdit,
  onSubmit,
  onCancel,
  loading,
}: AutomationInlineFormProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(initialValues);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const { data: wsData } = useWorkspaces({ limit: 100 });
  const workspaces = (wsData as { workspaces?: WorkspaceOption[] })?.workspaces ?? [];

  const set = (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement> | string) =>
    setForm((f) => ({ ...f, [key]: typeof e === 'string' ? e : e.target.value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(formStateToPayload(form));
  };

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.25, ease: 'easeInOut' }}
      style={{ overflow: 'hidden' }}
    >
      <form
        onSubmit={handleSubmit}
        className="automation-inline-form rounded-xl p-5 flex flex-col gap-5"
        style={{
          backgroundColor: 'var(--color-bg-elevated)',
          border: '1px solid var(--color-border-default)',
          boxShadow: 'var(--shadow-card)',
        }}
      >
        {/* ── Basic Section ─────────────────────────────── */}

        {/* Name */}
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>{t('common.name')}</label>
          <Input
            value={form.name}
            onChange={set('name')}
            placeholder={t('automation.namePlaceholder')}
            required
            className="placeholder:text-gray-500 border"
            style={inputStyle}
          />
        </div>

        {/* Trigger-specific basic fields */}
        {form.trigger_type === 'price' && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {/* Symbol with autocomplete (stocks + indices) */}
            <div className="flex flex-col gap-1.5">
              <label className={labelClass}>{t('automation.priceSymbol')}</label>
              <TickerAutocomplete
                value={form.price_symbol}
                onChange={(val) => setForm((f) => ({ ...f, price_symbol: val }))}
              />
            </div>

            {/* Condition Type */}
            <div className="flex flex-col gap-1.5">
              <label className={labelClass}>{t('automation.priceCondition')}</label>
              <Select
                value={form.price_condition_type}
                onChange={set('price_condition_type')}
              >
                {PRICE_CONDITION_TYPES.map((opt) => (
                  <option key={opt.value} value={opt.value}>{t(opt.labelKey)}</option>
                ))}
              </Select>
            </div>

            {/* Price Value */}
            <div className="flex flex-col gap-1.5">
              <label className={labelClass}>
                {t('automation.priceValue')} ({isPctCondition(form.price_condition_type) ? '%' : '$'})
              </label>
              <Input
                type="number"
                step="any"
                min={0}
                value={form.price_value}
                onChange={set('price_value')}
                placeholder={isPctCondition(form.price_condition_type) ? '5' : '150.00'}
                required
                className="placeholder:text-gray-500 border w-full"
                style={inputStyle}
              />
            </div>

            {/* Reference (only for pct conditions) */}
            {isPctCondition(form.price_condition_type) && (
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('automation.priceReference')}</label>
                <Select
                  value={form.price_reference}
                  onChange={set('price_reference')}
                >
                  {PRICE_REFERENCE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{t(opt.labelKey)}</option>
                  ))}
                </Select>
              </div>
            )}
          </div>
        )}

        {form.trigger_type === 'cron' && (
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>{t('automation.schedule')}</label>
            <CronScheduleBuilder
              value={form.cron_expression}
              onChange={set('cron_expression')}
            />
          </div>
        )}

        {form.trigger_type === 'once' && (
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>{t('automation.runAt')}</label>
            <Input
              type="datetime-local"
              value={form.next_run_at}
              onChange={set('next_run_at')}
              required
              className="border"
              style={inputStyle}
            />
          </div>
        )}

        {/* Instruction */}
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>{t('automation.instruction')}</label>
          <Textarea
            value={form.instruction}
            onChange={set('instruction')}
            placeholder={t('automation.instructionPlaceholder')}
            required
            rows={4}
            className="placeholder:text-gray-500 border"
            style={inputStyle}
          />
        </div>

        {/* ── Advanced Section ──────────────────────────── */}

        <div>
          <button
            type="button"
            onClick={() => setAdvancedOpen(!advancedOpen)}
            className="w-full flex items-center justify-between text-sm font-medium cursor-pointer"
            style={{ color: 'var(--color-text-primary)' }}
          >
            <span>{t('automation.advancedOptions')}</span>
            <ChevronDown
              className="h-4 w-4 transition-transform"
              style={{
                color: 'var(--color-text-tertiary)',
                transform: advancedOpen ? 'rotate(180deg)' : 'rotate(0deg)',
              }}
            />
          </button>

          <AnimatePresence>
            {advancedOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2 }}
              style={{ overflow: 'hidden' }}
              className="flex flex-col gap-4 mt-3"
            >
              {/* Trigger Type */}
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('automation.triggerType')}</label>
                <div className={radioGroupClass}>
                  <RadioOption name="trigger_type" value="cron" checked={form.trigger_type === 'cron'} onChange={set('trigger_type')} label={t('automation.cronRecurring')} />
                  <RadioOption name="trigger_type" value="once" checked={form.trigger_type === 'once'} onChange={set('trigger_type')} label={t('automation.once')} />
                  <RadioOption name="trigger_type" value="price" checked={form.trigger_type === 'price'} onChange={set('trigger_type')} label={t('automation.priceAlert')} />
                </div>
              </div>

              {/* Agent Mode */}
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('automation.agentMode')}</label>
                <div className={radioGroupClass}>
                  <RadioOption name="agent_mode" value="flash" checked={form.agent_mode === 'flash'} onChange={set('agent_mode')} label={t('automation.flash')} />
                  <RadioOption name="agent_mode" value="ptc" checked={form.agent_mode === 'ptc'} onChange={set('agent_mode')} label={t('automation.ptcSandbox')} />
                </div>
              </div>

              {/* Workspace */}
              {form.agent_mode === 'ptc' && (
                <div className="flex flex-col gap-1.5">
                  <label className={labelClass}>{t('thread.workspace')}</label>
                  <Select
                    value={form.workspace_id}
                    onChange={set('workspace_id')}
                    required
                  >
                    <option value="">{t('automation.selectWorkspace')}</option>
                    {workspaces.map((ws) => (
                      <option key={ws.workspace_id} value={ws.workspace_id}>
                        {ws.name}
                      </option>
                    ))}
                  </Select>
                </div>
              )}

              {/* Timezone (hidden for price triggers) */}
              {form.trigger_type !== 'price' && (
                <div className="flex flex-col gap-1.5">
                  <label className={labelClass}>{t('settings.timezone')}</label>
                  <Select
                    value={form.timezone}
                    onChange={set('timezone')}
                  >
                    {COMMON_TIMEZONES.map((tz) => (
                      <option key={tz} value={tz}>{tz}</option>
                    ))}
                  </Select>
                </div>
              )}

              {/* Thread Strategy */}
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('automation.threadStrategy')}</label>
                <div className={radioGroupClass}>
                  <RadioOption name="thread_strategy" value="new" checked={form.thread_strategy === 'new'} onChange={set('thread_strategy')} label={t('automation.newThreadEachRun')} />
                  <RadioOption name="thread_strategy" value="continue" checked={form.thread_strategy === 'continue'} onChange={set('thread_strategy')} label={t('automation.continueExisting')} />
                </div>
              </div>

              {/* Delivery */}
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('automation.delivery')}</label>
                <div className={radioGroupClass}>
                  <RadioOption name="delivery_method" value="" checked={form.delivery_method === ''} onChange={set('delivery_method')} label={t('automation.deliverNone')} />
                  <RadioOption name="delivery_method" value="slack" checked={form.delivery_method === 'slack'} onChange={set('delivery_method')} label={t('automation.deliverToSlack')} />
                  <RadioOption name="delivery_method" value="discord" checked={form.delivery_method === 'discord'} onChange={set('delivery_method')} label={t('automation.deliverToDiscord')} />
                </div>
              </div>

              {/* Retrigger Mode (price only) */}
              {form.trigger_type === 'price' && (
                <>
                  <div className="flex flex-col gap-1.5">
                    <label className={labelClass}>{t('automation.priceRetrigger')}</label>
                    <div className={radioGroupClass}>
                      {RETRIGGER_MODES.map((opt) => (
                        <RadioOption
                          key={opt.value}
                          name="price_retrigger_mode"
                          value={opt.value}
                          checked={form.price_retrigger_mode === opt.value}
                          onChange={set('price_retrigger_mode')}
                          label={t(opt.labelKey)}
                        />
                      ))}
                    </div>
                  </div>

                  {form.price_retrigger_mode === 'recurring' && (
                    <div className="flex flex-col gap-1.5">
                      <label className={labelClass}>{t('automation.priceCooldown')}</label>
                      <div className="flex items-center gap-2">
                        <Input
                          type="number"
                          min={240}
                          placeholder="Leave empty for daily"
                          value={form.price_cooldown_minutes}
                          onChange={set('price_cooldown_minutes')}
                          className="border w-32"
                          style={inputStyle}
                        />
                        <span className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                          {t('automation.minutes')}
                        </span>
                      </div>
                      <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                        {t('automation.cooldownHint')}
                      </p>
                    </div>
                  )}
                </>
              )}

              {/* Max Failures */}
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('automation.maxFailures')}</label>
                <Input
                  type="number"
                  min={1}
                  max={100}
                  value={form.max_failures}
                  onChange={set('max_failures')}
                  className="border w-24"
                  style={inputStyle}
                />
              </div>

              {/* Description */}
              <div className="flex flex-col gap-1.5">
                <label className={labelClass}>{t('common.description')}</label>
                <Input
                  value={form.description}
                  onChange={set('description')}
                  placeholder={t('automation.descPlaceholder')}
                  className="placeholder:text-gray-500 border"
                  style={inputStyle}
                />
              </div>
            </motion.div>
          )}
          </AnimatePresence>
        </div>

        {/* ── Actions ───────────────────────────────────── */}

        <div
          className="form-actions flex flex-col-reverse sm:flex-row sm:justify-end gap-2 pt-4 mt-1"
          style={{ borderTop: '1px solid var(--color-border-default)' }}
        >
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            disabled={loading}
            style={{ backgroundColor: 'var(--color-accent-primary)', color: 'var(--color-text-on-accent)' }}
          >
            {loading ? t('common.saving') : isEdit ? t('automation.saveChanges') : t('common.create')}
          </Button>
        </div>
      </form>
    </motion.div>
  );
}
