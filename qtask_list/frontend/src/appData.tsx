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
  lastUpdated: number | null;
  interval: RefreshInterval;
  setInterval: (v: RefreshInterval) => void;
  reload: () => void;
}

const Ctx = createContext<AppData | null>(null);

export function AppDataProvider({ children }: { children: ReactNode }) {
  const [interval, setIntervalState] = useState<RefreshInterval>(5);
  const [reloadKey, setReloadKey] = useState(0);

  const { data, error, loading, lastUpdated } = usePolling(async () => {
    const [queues, workers, health] = await Promise.all([api.queues(), api.workers(), api.health()]);
    if (health.status !== "ok") throw new Error(health.error ?? "Redis 不可用");
    return { queues, workers, health };
  }, interval, [reloadKey]);

  const alerts = useMemo(() => {
    if (!data) return [] as AlertItem[];
    return alertEngine.compute(data.queues, data.workers ?? [], data.health);
  }, [data]);

  const value: AppData = {
    queues: data?.queues ?? null,
    workers: data?.workers ?? null,
    health: data?.health ?? null,
    alerts,
    error: error ?? (data?.health.status === "error" ? data.health.error ?? "Redis 不可用" : null),
    loading,
    lastUpdated,
    interval,
    setInterval: (v) => setIntervalState(v),
    reload: () => setReloadKey((key) => key + 1),
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppData(): AppData {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("AppData missing");
  return ctx;
}
