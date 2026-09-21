import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAppData } from "../appData";
import type { TaskRow } from "../types";
import { STATE_KEYS, STATE_LABELS, type StateKey } from "../types";
import { ErrorBanner, Skeleton } from "../components/ui";
import { TaskFilterBar, TaskTable, type TaskFilters } from "../components/TaskTable";
import { TaskDrawer } from "../components/TaskDrawer";

export function TasksPage() {
  const { queues } = useAppData();
  const [params, setParams] = useSearchParams();
  const stateParam = (params.get("state") as StateKey) || "all";
  const queueParam = params.get("queue") || "";
  const searchParam = params.get("q") || "";

  const [filters, setFilters] = useState<TaskFilters>({
    state: STATE_KEYS.includes(stateParam) ? stateParam : "all",
    search: searchParam,
    createdAfter: "",
    createdBefore: "",
    completedAfter: "",
    completedBefore: "",
  });
  const [queueSel, setQueueSel] = useState<string>(queueParam);
  const [limit, setLimit] = useState(50);
  const [reloadKey, setReloadKey] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [openTask, setOpenTask] = useState<TaskRow | null>(null);
  const [exactError, setExactError] = useState<string | null>(null);

  const queueNames = useMemo(() => (queues ?? []).map((q) => String(q.name)), [queues]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (filters.state !== "all") next.set("state", filters.state);
    if (queueSel) next.set("queue", queueSel);
    if (filters.search) next.set("q", filters.search);
    setParams(next, { replace: true });
  }, [filters.state, queueSel, filters.search]);

  useEffect(() => {
    if (!filters.search) {
      setExactError(null);
      return;
    }
    const trimmed = filters.search.trim();
    if (!/^[a-zA-Z0-9_-]{8,}$/.test(trimmed)) {
      setExactError(null);
      return;
    }
    let stopped = false;
    api
      .task(trimmed)
      .then((t) => {
        if (!stopped && t && (t.task_id === trimmed || t.taskId === trimmed)) setOpenTask({ task_id: trimmed });
      })
      .catch(() => undefined);
    return () => {
      stopped = true;
    };
  }, [filters.search]);

  const filtersWithQueue: TaskFilters = { ...filters };

  return (
    <div className="page">
      <h1 className="page-title">任务</h1>
      <p className="page-desc">跨队列排查：搜索 task_id / action / payload。task_id 精确命中时直接打开详情。</p>
      {exactError && <ErrorBanner error={exactError} onRetry={() => setExactError(null)} />}

      <div className="filter-bar">
        <select
          className="input"
          value={queueSel}
          onChange={(e) => setQueueSel(e.target.value)}
          title="选择队列维度（默认全部队列）"
        >
          <option value="">全部队列</option>
          {queueNames.map((q) => (
            <option key={q} value={q}>
              {q}
            </option>
          ))}
        </select>
      </div>

      {queueSel ? (
        <TaskFilterBar
          state={filters.state}
          onStateChange={(s) => setFilters((f) => ({ ...f, state: s }))}
          search={filters.search}
          onSearchChange={(s) => setFilters((f) => ({ ...f, search: s }))}
          timeRange={{
            createdStart: filters.createdAfter ?? "",
            createdEnd: filters.createdBefore ?? "",
            completedStart: "",
            completedEnd: "",
          }}
          onTimeRangeChange={(t) => setFilters((f) => ({ ...f, createdAfter: t.createdStart, createdBefore: t.createdEnd }))}
          autoRefresh={autoRefresh}
          onAutoRefreshChange={setAutoRefresh}
        />
      ) : (
        <div className="filter-bar">
          <input
            className="input"
            style={{ width: 260 }}
            placeholder="搜索 task_id / action / payload"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
          <select
            className="input"
            value={filters.state}
            onChange={(e) => setFilters((f) => ({ ...f, state: e.target.value as StateKey }))}
          >
            {STATE_KEYS.map((s) => (
              <option key={s} value={s}>
                {STATE_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
      )}

      {queueSel ? (
        <TaskTable
          queue={queueSel}
          filters={filtersWithQueue}
          limit={limit}
          onLimitChange={setLimit}
          onOpenTask={(t: TaskRow) => t.task_id && setOpenTask(t)}
          reloadKey={reloadKey}
          autoRefresh={autoRefresh}
        />
      ) : (
        <GlobalTaskTable
          filters={filters}
          limit={limit}
          onLimitChange={setLimit}
          onOpenTask={(t) => t.task_id && setOpenTask(t)}
          reloadKey={reloadKey}
        />
      )}

      {openTask?.task_id && (
        <TaskDrawer
          taskId={String(openTask.task_id)}
          stateHint={String(openTask._state ?? openTask.state ?? "")}
          onClose={() => setOpenTask(null)}
          onChanged={() => setReloadKey((k) => k + 1)}
        />
      )}
    </div>
  );
}

function GlobalTaskTable({
  filters,
  limit,
  onLimitChange,
  onOpenTask,
  reloadKey,
}: {
  filters: TaskFilters;
  limit: number;
  onLimitChange: (n: number) => void;
  onOpenTask: (t: TaskRow) => void;
  reloadKey: number;
}) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const query = new URLSearchParams({
          status: filters.state === "all" ? "" : filters.state,
          search: filters.search,
          limit: String(limit),
        });
        if (!query.get("status")) query.delete("status");
        const data = await fetch(`/api/tasks?${query.toString()}`).then((r) => r.json());
        if (stopped) return;
        if (data.detail) throw new Error(String(data.detail));
        setRows(data.tasks);
        setError(null);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!stopped) setLoading(false);
      }
    };
    setLoading(true);
    load();
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, 8000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [filters.state, filters.search, limit, reloadKey]);

  if (loading && rows === null) return <Skeleton lines={10} height={16} />;
  if (error) return <ErrorBanner error={error} onRetry={() => onLimitChange(limit)} />;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>task_id</th>
            <th>action</th>
            <th>队列</th>
            <th>状态</th>
            <th>发布时间</th>
          </tr>
        </thead>
        <tbody>
          {(rows ?? []).map((row, i) => (
            <tr key={`${row.task_id ?? i}-${i}`} className="rowlink" onClick={() => onOpenTask(row)}>
              <td className="mono">{row.task_id}</td>
              <td className="mono">{row.action || "—"}</td>
              <td className="mono">{String(row._queue ?? row.queue ?? "—")}</td>
              <td>{String(row._state ?? row.state ?? "—")}</td>
              <td className="faint num">{row.created_at ? new Date(Number(row.created_at) * 1000).toLocaleString("zh-CN") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows !== null && rows.length === 0 && <div style={{ padding: 24 }} className="muted">没有匹配的任务。</div>}
      {rows !== null && rows.length >= limit && limit < 500 && (
        <div style={{ padding: "10px 12px" }}>
          <button className="btn" onClick={() => onLimitChange(limit + 50)}>
            加载更多（已显示 {rows.length} 条）
          </button>
        </div>
      )}
    </div>
  );
}
