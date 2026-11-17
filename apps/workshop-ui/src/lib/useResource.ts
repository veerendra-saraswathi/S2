import { useCallback, useEffect, useRef, useState } from "react";

export type Resource<T> = {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
};

/** Load something, reload on demand or on an interval. `key` change reloads; null skips. */
export function useResource<T>(key: string | null, load: () => Promise<T>, refreshMs?: number): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(key !== null);
  const [tick, setTick] = useState(0);
  const loader = useRef(load);
  loader.current = load;

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    setLoading(true);
    loader
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(undefined);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key, tick]);

  useEffect(() => {
    if (!refreshMs || key === null) return;
    const timer = setInterval(() => setTick((t) => t + 1), refreshMs);
    return () => clearInterval(timer);
  }, [refreshMs, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
