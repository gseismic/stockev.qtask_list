import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import { usePolling, type RefreshInterval } from "./hooks";
import { alertEngine, type AlertItem } from "./alerts";
import type { HealthInfo, QueueInfo, WorkerInfo } from "./types";

interface AppData {
  queues: QueueInfo[] | null;
  workers: WorkerInfo[] | null;
  health: HealthInfo | null;
  alerts: AlertItem[];
  error: string | null;
  loading: boolean;
  interval: RefreshInterval;
  setInterval: (v: RefreshInterval) => void;
  reload: () => void;
}

const Ctx = createContext<AppData | null>(null);

export function AppDataProvider({ children }: { children: ReactNode }) {
  const [interval, setIntervalState] = useState<RefreshInterval>(5);

  const { data, error, loading } = usePolling(async () => {
    const [queues, workers, health] = await Promise.all([api.queues(), api.workers(), api.health()]);
    return { queues, workers, health };
  }, interval);

  const alerts = useMemo(() => {
    if (!data) return [] as AlertItem[];
    return alertEngine.compute(data.queues, data.workers ?? [], data.health);
  }, [data]);

  const value: AppData = {
    queues: data?.queues ?? null,
    workers: data?.workers ?? null,
    health: data?.health ?? null,
    alerts,
    error,
    loading,
    interval,
    setInterval: (v) => setIntervalState(v),
    reload: () => setIntervalState((v) => v),
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppData(): AppData {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("AppData missing");
  return ctx;
}
