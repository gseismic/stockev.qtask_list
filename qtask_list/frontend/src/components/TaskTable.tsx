import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { TaskRow } from "../types";
import { STATE_KEYS, STATE_LABELS, type StateKey } from "../types";
import { ConfirmDialog, ErrorBanner, Skeleton, StateBadge, fmtTime, shortId } from "./ui";

export interface TaskFilters {
  state: StateKey;
  search: string;
  createdAfter?: string;
  createdBefore?: string;
  completedAfter?: string;
  completedBefore?: string;
}

export function TaskFilterBar({
  state,
  onStateChange,
  search,
  onSearchChange,
  timeRange,
  onTimeRangeChange,
  autoRefresh,
  onAutoRefreshChange,
  extra,
}: {
  state: StateKey;
  onStateChange: (s: StateKey) => void;
  search: string;
  onSearchChange: (s: string) => void;
  timeRange: { createdStart: string; createdEnd: string; completedStart: string; completedEnd: string };
  onTimeRangeChange: (t: TaskFilterBarTimeRange) => void;
  autoRefresh: boolean;
  onAutoRefreshChange: (v: boolean) => void;
  extra?: React.ReactNode;
}) {
  const [local, setLocal] = useState(search);
  useEffect(() => {
    const timer = window.setTimeout(() => onSearchChange(local), 300);
    return () => window.clearTimeout(timer);
  }, [local]);
  return (
    <div className="filter-bar">
      <input
        className="input"
        style={{ width: 260 }}
        placeholder="搜索 task_id / action / payload"
        value={local}
        onChange={(e) => setLocal(e.target.value)}
      />
      <select className="input" value={state} onChange={(e) => onStateChange(e.target.value as StateKey)}>
        {STATE_KEYS.map((s) => (
          <option key={s} value={s}>
            {STATE_LABELS[s]}
          </option>
        ))}
      </select>
      {extra}
      <div className="spacer" />
      <label className="faint">
        发布 <input type="datetime-local" className="input" style={{ width: 180 }} value={timeRange.createdStart} onChange={(e) => onTimeRangeChange({ ...timeRange, createdStart: e.target.value }) } /> ~{" "}
        <input type="datetime-local" className="input" style={{ width: 180 }} value={timeRange.createdEnd} onChange={(e) => onTimeRangeChange({ ...timeRange, createdEnd: e.target.value })} />
      </label>
      <label className="faint" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <input type="checkbox" checked={autoRefresh} onChange={(e) => onAutoRefreshChange(e.target.checked)} />
        自动刷新
      </label>
    </div>
  );
}

export interface TaskFilterBarTimeRange {
  createdStart: string;
  createdEnd: string;
  completedStart: string;
  completedEnd: string;
}

export function toUnix(dtLocal: string): number | undefined {
  if (!dtLocal) return undefined;
  const t = new Date(dtLocal).getTime();
  return Number.isFinite(t) ? t / 1000 : undefined;
}

export function TaskTable({
  queue,
  filters,
  limit,
  onLimitChange,
  onOpenTask,
  reloadKey,
  autoRefresh,
  emptyHint,
}: {
  queue: string | null;
  filters: TaskFilters;
  limit: number;
  onLimitChange: (n: number) => void;
  onOpenTask: (task: TaskRow) => void;
  reloadKey: number;
  autoRefresh: boolean;
  emptyHint?: React.ReactNode;
}) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [scanLimited, setScanLimited] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const pagedState = [
    "all", "history", "completed", "failed", "skipped", "cancelled",
    "retry_wait", "deadline_missed", "expired",
  ].includes(filters.state);

  const queryOptions = (cursor?: string) => ({
    queue: queue ?? "",
    state: filters.state,
    search: filters.search || undefined,
    limit,
    cursor,
    createdAfter: toUnix(filters.createdAfter ?? ""),
    createdBefore: toUnix(filters.createdBefore ?? ""),
    completedAfter: toUnix(filters.completedAfter ?? ""),
    completedBefore: toUnix(filters.completedBefore ?? ""),
  });

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      if (!queue) return;
      try {
        const data = await api.queueTasks(queryOptions());
        if (stopped) return;
        setRows(data.tasks);
        setNextCursor(data.next_cursor ?? null);
        setScanLimited(Boolean(data.scan_limited));
        setError(null);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!stopped) setLoading(false);
      }
    };
    setLoading(true);
    load();
    let timer: number | undefined;
    if (autoRefresh && !pagedState) {
      timer = window.setInterval(() => {
        if (!document.hidden) load();
      }, 5000);
    }
    return () => {
      stopped = true;
      if (timer) window.clearInterval(timer);
    };
  }, [queue, filters.state, filters.search, filters.createdAfter, filters.createdBefore, filters.completedAfter, filters.completedBefore, limit, reloadKey, autoRefresh, retryKey]);

  const loadMore = async () => {
    if (!queue || !nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const data = await api.queueTasks(queryOptions(nextCursor));
      setRows((current) => [...(current ?? []), ...data.tasks]);
      setNextCursor(data.next_cursor ?? null);
      setScanLimited(Boolean(data.scan_limited));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const actions = useMemo(() => {
    const set = new Set<string>();
    for (const row of rows ?? []) {
      if (row.action) set.add(String(row.action));
    }
    return [...set].sort();
  }, [rows]);

  if (!queue) return <EmptyInline text="请选择队列" />;
  if (loading && rows === null) return <Skeleton lines={10} height={16} />;
  if (error) return <ErrorBanner error={error} onRetry={() => setRetryKey((key) => key + 1)} />;

  return (
    <>
      {actions.length > 0 && (
        <div className="faint" style={{ marginBottom: 6 }}>
          本页 action：{actions.join("、")}
        </div>
      )}
      <button className="btn" style={{ marginBottom: 6 }} onClick={() => setRetryKey((key) => key + 1)}>刷新</button>
      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>task_id</th>
              <th>action</th>
              <th>attempt</th>
              <th>状态</th>
              <th>发布时间</th>
              <th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((row, i) => {
              const state = String(row._state ?? row.state ?? "");
              return (
                <tr key={`${row.task_id ?? i}-${i}`} className="rowlink" onClick={() => onOpenTask(row)}>
                  <td className="mono" title={row.task_id}>
                    {shortId(row.task_id as string)}
                  </td>
                  <td className="mono">{row.action || "—"}</td>
                  <td className="num">{row.attempt ?? "—"}</td>
                  <td>
                    <StateBadge state={state} label={STATE_LABELS[state as StateKey] ?? state} />
                  </td>
                  <td className="num faint">{fmtTime(row.created_at)}</td>
                  <td className="num faint">{fmtTime(row.updated_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows !== null && rows.length === 0 && (
          <div style={{ padding: 24 }}>
            {scanLimited ? "本页没有匹配任务，可继续搜索。" : (emptyHint ?? "该筛选下没有任务。")}
          </div>
        )}
      </div>
      {scanLimited && <div className="muted" style={{ marginTop: 8 }}>本页已达到扫描上限，可继续搜索。</div>}
      {(nextCursor || (!pagedState && rows !== null && rows.length >= limit && limit < 500)) && (
        <div style={{ marginTop: 10 }}>
          <button className="btn" onClick={nextCursor ? loadMore : () => onLimitChange(limit + 50)} disabled={loading || loadingMore}>
            {loading || loadingMore ? "加载中…" : `加载更多（已显示 ${rows?.length ?? 0} 条）`}
          </button>
        </div>
      )}
    </>
  );
}

function EmptyInline({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}

export function ConfirmDialogShim(props: React.ComponentProps<typeof ConfirmDialog>) {
  return <ConfirmDialog {...props} />;
}
