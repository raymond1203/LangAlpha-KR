import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { getWorkspaces, getWorkspaceThreads } from '../utils/api';

/**
 * useNavigationData — fetches workspace + thread data for the navigation panel.
 *
 * Display order: 2 most recent unpinned → pinned → remaining unpinned (up to 5).
 * "Load more" fetches the next page and appends.
 *
 * @param {string} currentWorkspaceId
 * @returns {{ workspaces, workspaceThreads, loading, hasMore, loadMore, expandWorkspace }}
 */
export function useNavigationData(currentWorkspaceId) {
  const [allFetched, setAllFetched] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [workspaceThreads, setWorkspaceThreads] = useState({});
  const [loading, setLoading] = useState(true);
  const [visibleCount, setVisibleCount] = useState(9); // 2 recent + pinned + 5 rest initially

  const threadCacheRef = useRef({});
  const fetchingRef = useRef(new Set());
  const offsetRef = useRef(0);

  // Fetch initial batch of workspaces
  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getWorkspaces(20, 0, 'custom');
        if (cancelled) return;

        const all = data.workspaces || [];
        setAllFetched(all);
        setTotalCount(data.total || all.length);
        offsetRef.current = all.length;
      } catch (e) {
        console.warn('[useNavigationData] Failed to load workspaces:', e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, []);

  // Build ordered workspace list: 2 most recent → remaining pinned → rest
  const workspaces = useMemo(() => {
    if (!allFetched.length) return [];

    // First 2 most recent (pinned or not — API returns by recency)
    const recentTwo = allFetched.slice(0, 2);
    const recentIds = new Set(recentTwo.map((ws) => ws.workspace_id));

    // Remaining pinned that aren't already in recent two
    const remainingPinned = allFetched.filter((ws) => ws.is_pinned && !recentIds.has(ws.workspace_id));
    const pinnedIds = new Set(remainingPinned.map((ws) => ws.workspace_id));

    // Rest: everything not in recent two or remaining pinned
    const rest = allFetched.filter((ws) => !recentIds.has(ws.workspace_id) && !pinnedIds.has(ws.workspace_id));

    const ordered = [...recentTwo, ...remainingPinned, ...rest];
    const sliced = ordered.slice(0, visibleCount);
    // Always include the current workspace even if it fell outside the visible slice
    if (currentWorkspaceId && !sliced.some((ws) => ws.workspace_id === currentWorkspaceId)) {
      const currentWs = allFetched.find((ws) => ws.workspace_id === currentWorkspaceId);
      if (currentWs) sliced.unshift(currentWs);
    }
    return sliced;
  }, [allFetched, visibleCount, currentWorkspaceId]);

  const hasMore = useMemo(() => {
    // More to show from already fetched
    if (visibleCount < allFetched.length) return true;
    // More to fetch from server
    if (offsetRef.current < totalCount) return true;
    return false;
  }, [visibleCount, allFetched.length, totalCount]);

  const loadAll = useCallback(async () => {
    // Show everything already fetched
    setVisibleCount(Infinity);

    // Fetch remaining from server if needed
    if (offsetRef.current < totalCount) {
      try {
        const data = await getWorkspaces(100, offsetRef.current, 'custom');
        const newWs = data.workspaces || [];
        offsetRef.current += newWs.length;
        setAllFetched((prev) => {
          const existingIds = new Set(prev.map((w) => w.workspace_id));
          const unique = newWs.filter((w) => !existingIds.has(w.workspace_id));
          return [...prev, ...unique];
        });
        setTotalCount(data.total || totalCount);
      } catch (e) {
        console.warn('[useNavigationData] Failed to load all workspaces:', e);
      }
    }
  }, [totalCount]);

  // Auto-fetch threads for the current workspace
  useEffect(() => {
    if (!currentWorkspaceId) return;
    if (threadCacheRef.current[currentWorkspaceId]) {
      setWorkspaceThreads((prev) => ({
        ...prev,
        [currentWorkspaceId]: { threads: threadCacheRef.current[currentWorkspaceId], loading: false },
      }));
      return;
    }
    if (fetchingRef.current.has(currentWorkspaceId)) return;

    let cancelled = false;
    fetchingRef.current.add(currentWorkspaceId);

    setWorkspaceThreads((prev) => ({
      ...prev,
      [currentWorkspaceId]: { threads: [], loading: true },
    }));

    getWorkspaceThreads(currentWorkspaceId, 10, 0)
      .then((data) => {
        if (cancelled) return;
        const threads = data.threads || [];
        threadCacheRef.current[currentWorkspaceId] = threads;
        setWorkspaceThreads((prev) => ({
          ...prev,
          [currentWorkspaceId]: { threads, loading: false },
        }));
      })
      .catch((e) => {
        console.warn('[useNavigationData] Failed to load threads for', currentWorkspaceId, e);
        if (!cancelled) {
          setWorkspaceThreads((prev) => ({
            ...prev,
            [currentWorkspaceId]: { threads: [], loading: false },
          }));
        }
      })
      .finally(() => {
        fetchingRef.current.delete(currentWorkspaceId);
      });

    return () => { cancelled = true; };
  }, [currentWorkspaceId]);

  // Lazy-load threads for a workspace on expand
  const expandWorkspace = useCallback((wsId) => {
    if (threadCacheRef.current[wsId]) {
      setWorkspaceThreads((prev) => ({
        ...prev,
        [wsId]: { threads: threadCacheRef.current[wsId], loading: false },
      }));
      return;
    }
    if (fetchingRef.current.has(wsId)) return;

    fetchingRef.current.add(wsId);
    setWorkspaceThreads((prev) => ({
      ...prev,
      [wsId]: { threads: prev[wsId]?.threads || [], loading: true },
    }));

    getWorkspaceThreads(wsId, 10, 0)
      .then((data) => {
        const threads = data.threads || [];
        threadCacheRef.current[wsId] = threads;
        setWorkspaceThreads((prev) => ({
          ...prev,
          [wsId]: { threads, loading: false },
        }));
      })
      .catch((e) => {
        console.warn('[useNavigationData] Failed to load threads for', wsId, e);
        setWorkspaceThreads((prev) => ({
          ...prev,
          [wsId]: { threads: [], loading: false },
        }));
      })
      .finally(() => {
        fetchingRef.current.delete(wsId);
      });
  }, []);

  return { workspaces, workspaceThreads, loading, hasMore, loadAll, expandWorkspace };
}
