import { useState, useCallback, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useWorkspaces } from '../../../hooks/useWorkspaces';
import { queryKeys } from '../../../lib/queryKeys';
import { getWorkspaces, getWorkspaceThreads } from '../utils/api';

interface WorkspaceRecord {
  workspace_id: string;
  is_pinned?: boolean;
  [key: string]: unknown;
}

interface ThreadRecord {
  thread_id: string;
  [key: string]: unknown;
}

interface ThreadsData {
  threads: ThreadRecord[];
  loading: boolean;
}

interface WorkspacesResponse {
  workspaces: WorkspaceRecord[];
  total: number;
}

interface ThreadsResponse {
  threads: ThreadRecord[];
  [key: string]: unknown;
}

const NAV_WS_PARAMS = { limit: 20, sortBy: 'custom' };

export function useNavigationData(currentWorkspaceId: string) {
  const queryClient = useQueryClient();

  // Workspace list via React Query
  const { data: wsData, isLoading } = useWorkspaces(NAV_WS_PARAMS);
  const allFetched: WorkspaceRecord[] = (wsData as WorkspacesResponse | undefined)?.workspaces || [];
  const totalCount = (wsData as WorkspacesResponse | undefined)?.total || 0;

  const [workspaceThreads, setWorkspaceThreads] = useState<Record<string, ThreadsData>>({});
  const [visibleCount, setVisibleCount] = useState(9);

  // Build ordered workspace list: 2 most recent → remaining pinned → rest
  const workspaces = useMemo(() => {
    if (!allFetched.length) return [];

    const recentTwo = allFetched.slice(0, 2);
    const recentIds = new Set(recentTwo.map((ws) => ws.workspace_id));

    const remainingPinned = allFetched.filter((ws) => ws.is_pinned && !recentIds.has(ws.workspace_id));
    const pinnedIds = new Set(remainingPinned.map((ws) => ws.workspace_id));

    const rest = allFetched.filter((ws) => !recentIds.has(ws.workspace_id) && !pinnedIds.has(ws.workspace_id));

    const ordered = [...recentTwo, ...remainingPinned, ...rest];
    const sliced = ordered.slice(0, visibleCount);
    if (currentWorkspaceId && !sliced.some((ws) => ws.workspace_id === currentWorkspaceId)) {
      const currentWs = allFetched.find((ws) => ws.workspace_id === currentWorkspaceId);
      if (currentWs) sliced.unshift(currentWs);
    }
    return sliced;
  }, [allFetched, visibleCount, currentWorkspaceId]);

  const hasMore = useMemo(() => {
    if (visibleCount < allFetched.length) return true;
    if (allFetched.length < totalCount) return true;
    return false;
  }, [visibleCount, allFetched.length, totalCount]);

  const { data: currentWsThreadData, isLoading: currentWsThreadsLoading } = useQuery({
    queryKey: queryKeys.threads.byWorkspace(currentWorkspaceId),
    queryFn: () => getWorkspaceThreads(currentWorkspaceId, 10, 0),
    enabled: !!currentWorkspaceId,
    staleTime: 30_000,
  });

  const mergedThreads = useMemo(() => ({
    ...workspaceThreads,
    ...(currentWorkspaceId && currentWsThreadData !== undefined ? {
      [currentWorkspaceId]: {
        threads: (currentWsThreadData as ThreadsResponse)?.threads || [],
        loading: currentWsThreadsLoading,
      },
    } : currentWorkspaceId ? {
      [currentWorkspaceId]: {
        threads: workspaceThreads[currentWorkspaceId]?.threads || [],
        loading: true,
      },
    } : {}),
  }), [workspaceThreads, currentWorkspaceId, currentWsThreadData, currentWsThreadsLoading]);

  const expandWorkspace = useCallback((wsId: string) => {
    const cached = queryClient.getQueryData(queryKeys.threads.byWorkspace(wsId)) as ThreadsResponse | undefined;
    if (cached) {
      setWorkspaceThreads(prev => ({
        ...prev,
        [wsId]: { threads: cached.threads || [], loading: false },
      }));
      return;
    }

    setWorkspaceThreads(prev => ({
      ...prev,
      [wsId]: { threads: prev[wsId]?.threads || [], loading: true },
    }));

    queryClient.fetchQuery({
      queryKey: queryKeys.threads.byWorkspace(wsId),
      queryFn: () => getWorkspaceThreads(wsId, 10, 0),
      staleTime: 30_000,
    }).then((data: unknown) => {
      setWorkspaceThreads(prev => ({
        ...prev,
        [wsId]: { threads: (data as ThreadsResponse).threads || [], loading: false },
      }));
    }).catch(() => {
      setWorkspaceThreads(prev => ({
        ...prev,
        [wsId]: { threads: [], loading: false },
      }));
    });
  }, [queryClient]);

  const loadAll = useCallback(async () => {
    setVisibleCount(Infinity);

    if (allFetched.length < totalCount) {
      try {
        const data = await getWorkspaces(100, allFetched.length, 'custom') as WorkspacesResponse;
        queryClient.setQueryData(queryKeys.workspaces.list({ ...NAV_WS_PARAMS, offset: 0 }), (old: unknown) => {
          const oldData = old as WorkspacesResponse | undefined;
          if (!oldData) return data;
          const existingIds = new Set(oldData.workspaces.map(w => w.workspace_id));
          const unique = (data.workspaces || []).filter(w => !existingIds.has(w.workspace_id));
          return { ...oldData, workspaces: [...oldData.workspaces, ...unique], total: data.total || oldData.total };
        });
      } catch (e) {
        console.warn('[useNavigationData] Failed to load all workspaces:', e);
      }
    }
  }, [allFetched.length, totalCount, queryClient]);

  return { workspaces, workspaceThreads: mergedThreads, loading: isLoading, hasMore, loadAll, expandWorkspace };
}
