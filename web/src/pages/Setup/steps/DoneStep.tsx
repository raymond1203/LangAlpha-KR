import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePreferences } from '@/hooks/usePreferences';
import { useApiKeys } from '@/hooks/useApiKeys';
import type { ByokProvider } from '@/components/model/types';

// ---------------------------------------------------------------------------
// DoneStep — confirmation screen
// ---------------------------------------------------------------------------

export default function DoneStep() {
  const navigate = useNavigate();
  const { preferences } = usePreferences();
  const { apiKeys } = useApiKeys();

  // ---------------------------------------------------------------------------
  // Derive summary data
  // ---------------------------------------------------------------------------

  const configuredProviderNames = useMemo<string[]>(() => {
    if (!apiKeys) return [];
    const keys = apiKeys as Record<string, unknown>;
    if (Array.isArray(keys.providers)) {
      return (keys.providers as ByokProvider[])
        .filter((p) => p.has_key !== false)
        .map((p) => p.display_name || p.provider);
    }
    return Object.entries(keys)
      .filter(([, v]) => v && typeof v === 'object')
      .map(([k, v]) => (v as Record<string, unknown>).display_name as string ?? k);
  }, [apiKeys]);

  const prefs = preferences as Record<string, unknown> | null;
  const otherPref = prefs?.other_preference as Record<string, unknown> | undefined;
  const primaryModel = (otherPref?.preferred_model as string) ?? 'Not set';
  const flashModel = (otherPref?.preferred_flash_model as string) ?? 'Not set';
  const providerLabel = configuredProviderNames.length > 0
    ? configuredProviderNames.join(', ')
    : 'Not configured';

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col items-center gap-8 py-4">
      {/* Success icon */}
      <div className="flex flex-col items-center gap-3">
        <CheckCircle2
          className="h-12 w-12"
          style={{ color: 'var(--color-success)' }}
        />
        <h2
          className="font-semibold text-center"
          style={{ fontSize: '1.25rem', color: 'var(--color-text-primary)' }}
        >
          You&apos;re all set!
        </h2>
        <p
          className="text-sm text-center max-w-md"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          Your AI research assistant is configured and ready to go.
        </p>
      </div>

      {/* Summary card */}
      <div
        className="w-full rounded-lg overflow-hidden"
        style={{
          background: 'var(--color-bg-surface)',
          border: '1px solid var(--color-border-default)',
        }}
      >
        <div className="flex flex-col divide-y" style={{ borderColor: 'var(--color-border-default)' }}>
          <SummaryRow label="Provider" value={providerLabel} />
          <SummaryRow label="Primary model" value={primaryModel} />
          <SummaryRow label="Flash model" value={flashModel} />
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-col items-center gap-3 w-full">
        <Button
          variant="default"
          className="w-full"
          onClick={() => navigate('/dashboard', { replace: true })}
        >
          Go to Dashboard
        </Button>
        <button
          type="button"
          className="text-sm font-medium transition-colors"
          style={{ color: 'var(--color-accent-primary)' }}
          onClick={() =>
            navigate('/chat/t/__default__', {
              replace: true,
              state: { isPersonalizing: true },
            })
          }
        >
          Set up investment profile
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SummaryRow
// ---------------------------------------------------------------------------

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <span
        className="text-sm"
        style={{ color: 'var(--color-text-secondary)' }}
      >
        {label}
      </span>
      <span
        className="text-sm font-medium text-right"
        style={{ color: 'var(--color-text-primary)' }}
      >
        {value}
      </span>
    </div>
  );
}
