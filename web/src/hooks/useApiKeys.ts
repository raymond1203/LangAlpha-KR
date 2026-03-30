import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { getUserApiKeys, updateUserApiKeys, deleteUserApiKey } from '../api/model';

/**
 * Shared hook for the current user's BYOK API keys.
 * All consumers share a single cached entry keyed by queryKeys.user.apiKeys().
 */
export function useApiKeys() {
  const { data, ...rest } = useQuery({
    queryKey: queryKeys.user.apiKeys(),
    queryFn: getUserApiKeys,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return { apiKeys: data ?? null, ...rest };
}

/**
 * Mutation hook for updating (setting / enabling) BYOK API keys.
 * Invalidates the apiKeys cache on success.
 */
export function useUpdateApiKeys() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateUserApiKeys,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.user.apiKeys() });
    },
  });
}

/**
 * Mutation hook for deleting a single provider's API key.
 * Invalidates the apiKeys cache on success.
 */
export function useDeleteApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteUserApiKey,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.user.apiKeys() });
    },
  });
}
