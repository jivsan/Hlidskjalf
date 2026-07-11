import { useCallback, useEffect, useRef, useState } from "react";

export interface PollState<T> {
  data: T | null;
  error: string | null;
  loading: boolean; // true only before the first successful/failed fetch
  refresh: () => void;
}

/**
 * Poll an async fetcher on an interval. Fetches immediately, pauses while the
 * document is hidden (visibilitychange), refetches on show. Previous data is
 * held during refetches (no skeleton flash).
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled = true,
): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const run = async () => {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      }
    };

    const start = () => {
      if (timer == null) {
        void run();
        timer = setInterval(() => {
          if (!document.hidden) void run();
        }, intervalMs);
      }
    };
    const stop = () => {
      if (timer != null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, enabled, tick]);

  return { data, error, loading, refresh };
}
