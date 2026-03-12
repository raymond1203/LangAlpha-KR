import { useEffect, type RefObject } from 'react';

/**
 * Calls `handler` when a mousedown event fires outside `ref`.
 * No-ops when `enabled` is false.
 */
export function useOnClickOutside(
  ref: RefObject<HTMLElement | null>,
  handler: () => void,
  enabled = true,
): void {
  useEffect(() => {
    if (!enabled) return;
    const handle = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        handler();
      }
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [ref, handler, enabled]);
}
