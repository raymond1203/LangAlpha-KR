import { useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ModelTierConfig } from '@/components/model/ModelTierConfig';
import type { ProviderModelsData } from '@/components/model/types';
import { useModels } from '@/hooks/useModels';
import { useApiKeys } from '@/hooks/useApiKeys';
import { usePreferences } from '@/hooks/usePreferences';
import { useUpdatePreferences } from '@/hooks/useUpdatePreferences';

// ---------------------------------------------------------------------------
// ModelSelectionStep
// ---------------------------------------------------------------------------

export default function ModelSelectionStep() {
  const navigate = useNavigate();
  const { models, isLoading: modelsLoading } = useModels();
  const { apiKeys } = useApiKeys();
  const { preferences } = usePreferences();
  const updatePreferences = useUpdatePreferences();

  // ---------------------------------------------------------------------------
  // Derive configured provider list from API keys
  // ---------------------------------------------------------------------------

  const configuredProviderIds = useMemo<string[]>(() => {
    if (!apiKeys) return [];
    const keys = apiKeys as Record<string, unknown>;
    if (Array.isArray(keys.providers)) {
      return (keys.providers as Array<{ provider: string; has_key?: boolean }>)
        .filter((p) => p.has_key)
        .map((p) => p.provider);
    }
    return [];
  }, [apiKeys]);

  // ---------------------------------------------------------------------------
  // Normalize models to the shape ModelTierConfig expects
  // ---------------------------------------------------------------------------

  /** Normalize models response.
   *  Response shape: { models: { [provider]: { display_name, models } }, model_metadata, system_defaults }.
   *  We need the nested `models` object.
   */
  const normalizedModels = useMemo<Record<string, ProviderModelsData>>(() => {
    if (!models) return {};
    const raw = models as Record<string, unknown>;
    const providerMap = (raw.models ?? raw) as Record<string, Record<string, unknown>>;
    const out: Record<string, ProviderModelsData> = {};
    for (const [provider, data] of Object.entries(providerMap)) {
      if (!data || typeof data !== 'object') continue;
      out[provider] = {
        models: (data.models as string[]) ?? [],
        display_name: (data.display_name as string) ?? provider,
      };
    }
    return out;
  }, [models]);

  // ---------------------------------------------------------------------------
  // Selection state — seed from existing preferences if available
  // ---------------------------------------------------------------------------

  const [primaryModel, setPrimaryModel] = useState<string>(
    () => (preferences as Record<string, unknown>)?.default_model as string ?? '',
  );
  const [flashModel, setFlashModel] = useState<string>(
    () => (preferences as Record<string, unknown>)?.flash_model as string ?? '',
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canContinue = Boolean(primaryModel && flashModel);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleBack = useCallback(() => {
    navigate('/setup/connect');
  }, [navigate]);

  const handleNext = useCallback(async () => {
    if (!primaryModel || !flashModel) return;

    setSaving(true);
    setError(null);

    try {
      await updatePreferences.mutateAsync({
        default_model: primaryModel,
        flash_model: flashModel,
      });

      navigate('/setup/ready');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(err?.response?.data?.detail ?? err?.message ?? 'Failed to save model preferences.');
    } finally {
      setSaving(false);
    }
  }, [primaryModel, flashModel, updatePreferences, navigate]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (modelsLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-5 w-5 animate-spin" style={{ color: 'var(--color-text-tertiary)' }} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Section heading */}
      <div className="flex flex-col gap-1">
        <h2
          className="font-semibold"
          style={{ fontSize: '1.125rem', color: 'var(--color-text-primary)' }}
        >
          Choose your models
        </h2>
        <p
          className="text-sm"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          Select which models to use for deep research and quick answers. You can change these anytime.
        </p>
      </div>

      {/* Model tier config */}
      <ModelTierConfig
        models={normalizedModels}
        filterProviders={configuredProviderIds.length > 0 ? configuredProviderIds : undefined}
        primaryModel={primaryModel}
        onPrimaryModelChange={setPrimaryModel}
        flashModel={flashModel}
        onFlashModelChange={setFlashModel}
        showExplainer
        showAdvanced={false}
      />

      {/* Error */}
      {error && (
        <p className="text-sm" style={{ color: 'var(--color-loss)' }}>
          {error}
        </p>
      )}

      {/* Navigation buttons */}
      <div className="flex items-center justify-between pt-2">
        <Button variant="outline" onClick={handleBack}>
          Back
        </Button>
        <Button
          variant="default"
          disabled={saving || !canContinue}
          onClick={handleNext}
          className="min-w-[120px]"
        >
          {saving ? (
            <>
              <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
              Saving...
            </>
          ) : (
            'Continue'
          )}
        </Button>
      </div>
    </div>
  );
}
