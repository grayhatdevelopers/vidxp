import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type AsyncStatus = 'idle' | 'checking' | 'installing' | 'launching' | 'succeeded' | 'failed';

export function useAsyncAction<TArgs extends unknown[], TResult>(
  activeStatus: Exclude<AsyncStatus, 'idle' | 'succeeded' | 'failed'>,
  action: (...args: TArgs) => Promise<TResult>,
) {
  const generation = useRef(0);
  const mounted = useRef(true);
  const [status, setStatus] = useState<AsyncStatus>('idle');
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
    };
  }, []);

  const run = useCallback(async (...args: TArgs) => {
    const current = ++generation.current;
    setStatus(activeStatus);
    setError(null);
    try {
      const result = await action(...args);
      if (mounted.current && generation.current === current) setStatus('succeeded');
      return result;
    } catch (caught) {
      if (mounted.current && generation.current === current) {
        setError(caught);
        setStatus('failed');
      }
      throw caught;
    }
  }, [action, activeStatus]);

  const reset = useCallback(() => {
    generation.current += 1;
    setStatus('idle');
    setError(null);
  }, []);

  return { run, reset, status, error, pending: status === activeStatus };
}

export function useExclusiveOperation<TKind>() {
  const generation = useRef(0);
  const active = useRef<{ id: number; kind: TKind } | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
      active.current = null;
    };
  }, []);

  const begin = useCallback((kind: TKind) => {
    if (active.current) return null;
    const id = ++generation.current;
    active.current = { id, kind };
    return id;
  }, []);

  const current = useCallback(
    (id: number) => mounted.current && active.current?.id === id,
    [],
  );

  const settle = useCallback((id: number) => {
    if (!mounted.current || active.current?.id !== id) return false;
    active.current = null;
    return true;
  }, []);

  return useMemo(() => ({ begin, current, settle }), [begin, current, settle]);
}
