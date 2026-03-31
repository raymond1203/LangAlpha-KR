import { describe, it, expect } from 'vitest';
import { filterModelsByAccess, buildConfiguredTypeMap } from '../useFilteredModels';
import type { ModelMetadataEntry } from '../useFilteredModels';
import type { ConfiguredProvider } from '../useConfiguredProviders';
import type { ProviderModelsData } from '@/components/model/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeProviderMap(
  entries: Record<string, string[]>,
): Record<string, ProviderModelsData> {
  const out: Record<string, ProviderModelsData> = {};
  for (const [key, models] of Object.entries(entries)) {
    out[key] = { models, display_name: key };
  }
  return out;
}

function makeConfigured(
  providers: ConfiguredProvider[],
): { configuredSet: Set<string>; configuredTypeMap: Map<string, string> } {
  return {
    configuredSet: new Set(providers.map((p) => p.provider)),
    configuredTypeMap: buildConfiguredTypeMap(providers),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('filterModelsByAccess', () => {
  it('includes models with a direct provider match', () => {
    const providerMap = makeProviderMap({
      openai: ['gpt-4o', 'gpt-4o-mini'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'gpt-4o': { provider: 'openai', access_type: 'api_key' },
      'gpt-4o-mini': { provider: 'openai', access_type: 'api_key' },
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'openai', displayName: 'OpenAI', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    expect(result.openai?.models).toEqual(['gpt-4o', 'gpt-4o-mini']);
  });

  it('includes groupKey fallback when access_type matches and no own key required', () => {
    // DeepInfra models grouped under openrouter — same api_key, shares credentials
    const providerMap = makeProviderMap({
      openrouter: ['deepinfra-model-1'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'deepinfra-model-1': { provider: 'deepinfra', access_type: 'api_key' },
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'openrouter', displayName: 'OpenRouter', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    expect(result.openrouter?.models).toEqual(['deepinfra-model-1']);
  });

  it('excludes groupKey fallback when access_type differs (OAuth leak)', () => {
    // codex-oauth models grouped under openai — user only has api_key for openai
    const providerMap = makeProviderMap({
      openai: ['gpt-4o', 'gpt-5.4-oauth'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'gpt-4o': { provider: 'openai', access_type: 'api_key' },
      'gpt-5.4-oauth': { provider: 'codex-oauth', access_type: 'oauth' },
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'openai', displayName: 'OpenAI', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    // gpt-4o should be included (direct match), gpt-5.4-oauth excluded (different access_type)
    expect(result.openai?.models).toEqual(['gpt-4o']);
  });

  it('passes all models through when configuredSet is empty (no filtering)', () => {
    const providerMap = makeProviderMap({
      openai: ['gpt-4o'],
      anthropic: ['claude-sonnet'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'gpt-4o': { provider: 'openai', access_type: 'api_key' },
      'claude-sonnet': { provider: 'anthropic', access_type: 'api_key' },
    };
    const configuredSet = new Set<string>();
    const configuredTypeMap = new Map<string, string>();

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    // All models should pass through unchanged
    expect(result.openai?.models).toEqual(['gpt-4o']);
    expect(result.anthropic?.models).toEqual(['claude-sonnet']);
  });

  it('excludes models with no metadata entry', () => {
    const providerMap = makeProviderMap({
      openai: ['gpt-4o', 'unknown-model'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'gpt-4o': { provider: 'openai', access_type: 'api_key' },
      // 'unknown-model' has no metadata
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'openai', displayName: 'OpenAI', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    expect(result.openai?.models).toEqual(['gpt-4o']);
  });

  it('excludes groupKey fallback when access_type differs (coding_plan leak)', () => {
    // dashscope-coding models grouped under dashscope — user only has api_key
    const providerMap = makeProviderMap({
      dashscope: ['qwen3-turbo', 'qwen3.5-plus-coding'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'qwen3-turbo': { provider: 'dashscope', access_type: 'api_key' },
      'qwen3.5-plus-coding': { provider: 'dashscope-coding', access_type: 'coding_plan' },
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'dashscope', displayName: 'DashScope', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    expect(result.dashscope?.models).toEqual(['qwen3-turbo']);
  });

  it('excludes groupKey fallback when variant requires own key (regional variant)', () => {
    // z-ai-cn models grouped under z-ai — both api_key but different env_key
    const providerMap = makeProviderMap({
      'z-ai': ['glm-5', 'glm-5-turbo-cn'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'glm-5': { provider: 'z-ai', access_type: 'api_key' },
      'glm-5-turbo-cn': { provider: 'z-ai-cn', access_type: 'api_key', requires_own_key: 'true' },
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'z-ai', displayName: 'Zhipu AI', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    // glm-5 included (direct match), glm-5-turbo-cn excluded (requires own key)
    expect(result['z-ai']?.models).toEqual(['glm-5']);
  });

  it('includes model when metadata is missing access_type (defaults to api_key)', () => {
    const providerMap = makeProviderMap({
      openai: ['gpt-4o'],
    });
    const metadata: Record<string, ModelMetadataEntry> = {
      'gpt-4o': { provider: 'openai' }, // no access_type field
    };
    const { configuredSet, configuredTypeMap } = makeConfigured([
      { provider: 'openai', displayName: 'OpenAI', type: 'api_key' },
    ]);

    const result = filterModelsByAccess(providerMap, metadata, configuredSet, configuredTypeMap);

    // Should include — access_type defaults to 'api_key', matches configured type
    expect(result.openai?.models).toEqual(['gpt-4o']);
  });
});
