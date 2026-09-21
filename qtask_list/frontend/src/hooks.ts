import { useEffect, useRef, useState } from "react";

export type RefreshInterval = 0 | 5 | 15 | 30;

export function usePolling<T>(fn: () => Promise<T>, interval: RefreshInterval, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const erroredRef = useRef(false);
  const tickRef = useRef(0);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;

    const run = async () => {
      try {
        const result = await fnRef.current();
        if (stopped) return;
        setData(result);
        setError(null);
        if (erroredRef.current) {
          erroredRef.current = false;
          tickRef.current = 0;
        }
      } catch (e) {
        if (stopped) return;
        setError(e instanceof Error ? e.message : String(e));
        erroredRef.current = true;
      } finally {
        if (!stopped) setLoading(false);
      }
    };

    const schedule = () => {
      if (stopped || interval === 0) return;
      const backoff = erroredRef.current ? 30 : interval;
      timer = window.setTimeout(async () => {
        if (document.hidden) {
          schedule();
          return;
        }
        await run();
        schedule();
      }, backoff * 1000);
    };

    run().then(schedule);
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval, tickRef.current, ...deps]);

  return { data, error, loading };
}
