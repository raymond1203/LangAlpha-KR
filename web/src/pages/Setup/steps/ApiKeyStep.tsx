import { useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, Ticket } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ProviderManager } from '@/components/model/ProviderManager';
import type { TestResult } from '@/components/model/ApiKeyInput';
import type { ByokProvider } from '@/components/model/types';
import { useModels } from '@/hooks/useModels';
import { useApiKeys } from '@/hooks/useApiKeys';
import { useUpdateApiKeys } from '@/hooks/useApiKeys';
import { queryKeys } from '@/lib/queryKeys';
import { api } from '@/api/client';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Providers that require a custom base URL (e.g. Azure, custom endpoints). */
const NEEDS_BASE_URL = new Set(['azure', 'custom']);

function providerNeedsBaseUrl(provider: string): boolean {
  return NEEDS_BASE_URL.has(provider);
}

async function testApiKey(
  provider: string,
  apiKey: string,
): Promise<TestResult> {
  try {
    const { data } = await api.post('/api/v1/keys/test', {
      provider,
      api_key: apiKey,
    });
    return data as TestResult;
  } catch {
    return { success: false, error: 'Test request failed' };
  }
}

// ---------------------------------------------------------------------------
// ApiKeyStep
// ---------------------------------------------------------------------------

export default function ApiKeyStep() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { models } = useModels();
  const { apiKeys } = useApiKeys();
  const updateApiKeys = useUpdateApiKeys();

  // Local state for key inputs
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [baseUrlInputs, setBaseUrlInputs] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Invitation code state
  const [showInvitation, setShowInvitation] = useState(false);
  const [invitationCode, setInvitationCode] = useState('');
  const [invitationError, setInvitationError] = useState<string | null>(null);
  const [redeemingInvitation, setRedeemingInvitation] = useState(false);

  // ---------------------------------------------------------------------------
  // Derived data
  // ---------------------------------------------------------------------------

  /** Build provider list from the API keys endpoint (BYOK-eligible only).
   *  The apiKeys response { providers: ByokProvider[] } already contains only
   *  BYOK-eligible providers (no OAuth like Codex/Claude). Use this as the
   *  source of truth for the provider grid.
   */
  const providerList = useMemo(() => {
    if (!apiKeys) return [];
    const keys = apiKeys as Record<string, unknown>;
    if (!Array.isArray(keys.providers)) return [];
    return (keys.providers as Array<{ provider: string; display_name: string; has_key?: boolean }>).map((p) => ({
      provider: p.provider,
      display_name: p.display_name ?? p.provider,
      byok_eligible: true as boolean | undefined,
    }));
  }, [apiKeys]);

  /** Convert API keys response to ByokProvider[].
   *  Response shape: { byok_enabled: bool, providers: ByokProvider[] }.
   *  Only include providers that actually have a key configured.
   */
  const configuredProviders = useMemo<ByokProvider[]>(() => {
    if (!apiKeys) return [];
    const keys = apiKeys as Record<string, unknown>;
    if (Array.isArray(keys.providers)) {
      return (keys.providers as ByokProvider[]).filter((p) => p.has_key);
    }
    return [];
  }, [apiKeys]);

  /** Track locally-added keys (not yet persisted to backend) */
  const [localProviders, setLocalProviders] = useState<ByokProvider[]>([]);

  /** Merge backend-configured + locally-added */
  const mergedConfigured = useMemo<ByokProvider[]>(() => {
    const backendSet = new Set(configuredProviders.map((p) => p.provider));
    return [
      ...configuredProviders,
      ...localProviders.filter((lp) => !backendSet.has(lp.provider)),
    ];
  }, [configuredProviders, localProviders]);

  const hasAtLeastOneKey = mergedConfigured.length > 0 || Object.values(keyInputs).some(Boolean);

  // ---------------------------------------------------------------------------
  // Callbacks
  // ---------------------------------------------------------------------------

  const handleKeyChange = useCallback((provider: string, value: string) => {
    setKeyInputs((prev) => ({ ...prev, [provider]: value }));
    setError(null);
  }, []);

  const handleBaseUrlChange = useCallback((provider: string, value: string) => {
    setBaseUrlInputs((prev) => ({ ...prev, [provider]: value }));
  }, []);

  const handleTestKey = useCallback(
    async (provider: string, apiKey: string): Promise<TestResult> => {
      const result = await testApiKey(provider, apiKey);
      // If key test passes, add to local configured list immediately
      if (result.success) {
        const displayName =
          providerList.find((p) => p.provider === provider)?.display_name ?? provider;
        setLocalProviders((prev) => {
          if (prev.some((p) => p.provider === provider)) return prev;
          return [
            ...prev,
            {
              provider,
              display_name: displayName,
              has_key: true,
              masked_key: null,
              base_url: baseUrlInputs[provider] ?? null,
            },
          ];
        });
      }
      return result;
    },
    [providerList, baseUrlInputs],
  );

  const handleDeleteProvider = useCallback((provider: string) => {
    setLocalProviders((prev) => prev.filter((p) => p.provider !== provider));
    setKeyInputs((prev) => {
      const next = { ...prev };
      delete next[provider];
      return next;
    });
    setBaseUrlInputs((prev) => {
      const next = { ...prev };
      delete next[provider];
      return next;
    });
  }, []);

  const handleNext = useCallback(async () => {
    // Collect all key inputs that have values
    const keysToSave = Object.entries(keyInputs).filter(([, v]) => v.trim());

    if (keysToSave.length === 0 && configuredProviders.length === 0) {
      setError('Please add at least one API key to continue.');
      return;
    }

    setSaving(true);
    setError(null);

    try {
      // Save each key
      for (const [provider, apiKey] of keysToSave) {
        const payload: Record<string, unknown> = { provider, api_key: apiKey };
        const baseUrl = baseUrlInputs[provider];
        if (baseUrl) payload.base_url = baseUrl;
        await updateApiKeys.mutateAsync(payload);
      }

      // Invalidate caches so downstream pages see fresh data
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.user.me() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.user.apiKeys() }),
      ]);

      navigate('/setup/models');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(err?.response?.data?.detail ?? err?.message ?? 'Failed to save API key.');
    } finally {
      setSaving(false);
    }
  }, [keyInputs, baseUrlInputs, configuredProviders, updateApiKeys, queryClient, navigate]);

  const handleRedeemInvitation = useCallback(async () => {
    if (!invitationCode.trim()) {
      setInvitationError('Please enter an invitation code.');
      return;
    }

    setRedeemingInvitation(true);
    setInvitationError(null);

    try {
      await api.post('/api/v1/invitations/redeem', { code: invitationCode.trim() });
      // Invitation redeemed — user now has access
      await queryClient.invalidateQueries({ queryKey: queryKeys.user.me() });
      navigate('/setup/models');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setInvitationError(err?.response?.data?.detail ?? 'Invalid invitation code.');
    } finally {
      setRedeemingInvitation(false);
    }
  }, [invitationCode, queryClient, navigate]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col gap-6">
      {/* Section heading */}
      <div className="flex flex-col gap-1">
        <h2
          className="font-semibold"
          style={{ fontSize: '1.125rem', color: 'var(--color-text-primary)' }}
        >
          Connect your AI provider
        </h2>
        <p
          className="text-sm"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          Add your API key from any supported provider. You can add more later in Settings.
        </p>
      </div>

      {/* Provider grid + key input */}
      <ProviderManager
        providers={providerList}
        configuredProviders={mergedConfigured}
        selectedProvider={selectedProvider}
        onSelectProvider={setSelectedProvider}
        keyInputs={keyInputs}
        onKeyChange={handleKeyChange}
        baseUrlInputs={baseUrlInputs}
        onBaseUrlChange={handleBaseUrlChange}
        providerNeedsBaseUrl={providerNeedsBaseUrl}
        onTestKey={handleTestKey}
        onDeleteProvider={handleDeleteProvider}
      />

      {/* Error message */}
      {error && (
        <p className="text-sm" style={{ color: 'var(--color-loss)' }}>
          {error}
        </p>
      )}

      {/* Invitation code section */}
      {!showInvitation ? (
        <button
          type="button"
          onClick={() => setShowInvitation(true)}
          className="inline-flex items-center gap-1.5 text-sm font-medium transition-colors self-start"
          style={{ color: 'var(--color-accent-primary)' }}
        >
          <Ticket className="h-4 w-4" />
          Have an invitation code?
        </button>
      ) : (
        <div
          className="flex flex-col gap-3 rounded-lg p-4"
          style={{
            background: 'var(--color-bg-surface)',
            border: '1px solid var(--color-border-default)',
          }}
        >
          <label
            className="text-sm font-medium"
            style={{ color: 'var(--color-text-primary)' }}
          >
            Invitation code
          </label>
          <div className="flex gap-2">
            <Input
              value={invitationCode}
              onChange={(e) => {
                setInvitationCode(e.target.value);
                setInvitationError(null);
              }}
              placeholder="Enter your invitation code"
              className="flex-1"
              autoComplete="off"
              spellCheck={false}
            />
            <Button
              variant="default"
              disabled={redeemingInvitation || !invitationCode.trim()}
              onClick={handleRedeemInvitation}
              className="shrink-0"
            >
              {redeemingInvitation ? (
                <>
                  <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                  Redeeming...
                </>
              ) : (
                'Redeem'
              )}
            </Button>
          </div>
          {invitationError && (
            <p className="text-xs" style={{ color: 'var(--color-loss)' }}>
              {invitationError}
            </p>
          )}
          <button
            type="button"
            onClick={() => {
              setShowInvitation(false);
              setInvitationError(null);
            }}
            className="text-xs self-start"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            Use an API key instead
          </button>
        </div>
      )}

      {/* Next button */}
      <div className="flex justify-end pt-2">
        <Button
          variant="default"
          disabled={saving || !hasAtLeastOneKey}
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
