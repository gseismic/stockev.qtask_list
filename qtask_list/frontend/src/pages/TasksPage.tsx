import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAppData } from "../appData";
import type { TaskRow } from "../types";
import { STATE_KEYS, STATE_LABELS, type StateKey } from "../types";
import { ErrorBanner, Skeleton } from "../components/ui";
import { TaskFilterBar, TaskTable, toUnix, type TaskFilters } from "../components/TaskTable";
import { TaskDrawer } from "../components/TaskDrawer";

export function TasksPage() {
  const { queues, reload } = useAppData();
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
  const [searchInput, setSearchInput] = useState(searchParam);
  const [actionInput, setActionInput] = useState("");
  const [action, setAction] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [openTask, setOpenTask] = useState<TaskRow | null>(null);
  const [exactError, setExactError] = useState<string | null>(null);

  const queueNames = useMemo(() => (queues ?? []).map((q) => String(q.name)), [queues]);

  useEffect(() => {
    const timer = window.setTimeout(() => setFilters((f) => ({ ...f, search: searchInput })), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => setAction(actionInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [actionInput]);

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
          onSearchChange={(s) => { setSearchInput(s); setFilters((f) => ({ ...f, search: s })); }}
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
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
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
          <button className="btn" onClick={() => setShowAdvanced((v) => !v)} aria-expanded={showAdvanced}>
            {showAdvanced ? "收起高级筛选" : "高级筛选"}
          </button>
        </div>
      )}

      {!queueSel && showAdvanced && (
        <div className="filter-bar" aria-label="高级筛选">
          <label>精确 action <input className="input" value={actionInput} onChange={(e) => setActionInput(e.target.value)} placeholder="例如 fetch_stock" /></label>
          <label>发布起 <input className="input" type="datetime-local" value={filters.createdAfter ?? ""} onChange={(e) => setFilters((f) => ({ ...f, createdAfter: e.target.value }))} /></label>
          <label>发布止 <input className="input" type="datetime-local" value={filters.createdBefore ?? ""} onChange={(e) => setFilters((f) => ({ ...f, createdBefore: e.target.value }))} /></label>
          <label>完成起 <input className="input" type="datetime-local" value={filters.completedAfter ?? ""} onChange={(e) => setFilters((f) => ({ ...f, completedAfter: e.target.value }))} /></label>
          <label>完成止 <input className="input" type="datetime-local" value={filters.completedBefore ?? ""} onChange={(e) => setFilters((f) => ({ ...f, completedBefore: e.target.value }))} /></label>
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
          action={action}
          limit={limit}
          onOpenTask={(t) => t.task_id && setOpenTask(t)}
          reloadKey={reloadKey}
        />
      )}

      {openTask?.task_id && (
        <TaskDrawer
          taskId={String(openTask.task_id)}
          stateHint={String(openTask._state ?? openTask.state ?? "")}
          onClose={() => setOpenTask(null)}
          onChanged={() => { setReloadKey((k) => k + 1); reload(); }}
        />
      )}
    </div>
  );
}

function GlobalTaskTable({
  filters,
  action,
  limit,
  onOpenTask,
  reloadKey,
}: {
  filters: TaskFilters;
  action: string;
  limit: number;
  onOpenTask: (t: TaskRow) => void;
  reloadKey: number;
}) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [scanLimited, setScanLimited] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const requestSeq = useRef(0);

  const queryFor = (cursor?: string) => ({
    state: filters.state,
    action: action.trim() || undefined,
    search: filters.search.trim() || undefined,
    limit,
    cursor,
    createdAfter: toUnix(filters.createdAfter ?? ""),
    createdBefore: toUnix(filters.createdBefore ?? ""),
    completedAfter: toUnix(filters.completedAfter ?? ""),
    completedBefore: toUnix(filters.completedBefore ?? ""),
  });

  useEffect(() => {
    let stopped = false;
    const seq = ++requestSeq.current;
    const load = async () => {
      try {
        const data = await api.tasks(queryFor());
        if (stopped || seq !== requestSeq.current) return;
        setRows(data.tasks);
        setNextCursor(data.next_cursor ?? null);
        setScanLimited(Boolean(data.scan_limited));
        setError(null);
      } catch (e) {
        if (!stopped && seq === requestSeq.current) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!stopped && seq === requestSeq.current) setLoading(false);
      }
    };
    setRows(null);
    setNextCursor(null);
    setScanLimited(false);
    setError(null);
    setLoading(true);
    load();
    return () => {
      stopped = true;
    };
  }, [filters.state, filters.search, filters.createdAfter, filters.createdBefore,
      filters.completedAfter, filters.completedBefore, action, limit, reloadKey, retryKey]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    const seq = requestSeq.current;
    setLoadingMore(true);
    try {
      const data = await api.tasks(queryFor(nextCursor));
      if (seq !== requestSeq.current) return;
      setRows((current) => [...(current ?? []), ...data.tasks]);
      setNextCursor(data.next_cursor ?? null);
      setScanLimited(Boolean(data.scan_limited));
      setError(null);
    } catch (e) {
      if (seq === requestSeq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  if (loading && rows === null) return <Skeleton lines={10} height={16} />;
  if (error && rows === null) return <ErrorBanner error={error} onRetry={() => setRetryKey((key) => key + 1)} />;
  return (
    <div className="table-wrap">
      {error && <ErrorBanner error={error} onRetry={() => setRetryKey((key) => key + 1)} />}
      <div style={{ padding: "8px 12px" }}>
        <button className="btn" onClick={() => setRetryKey((key) => key + 1)}>刷新搜索</button>
      </div>
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
      {rows !== null && rows.length === 0 && <div style={{ padding: 24 }} className="muted">
        {scanLimited ? "本页没有匹配任务，可继续搜索。" : "没有匹配的任务。"}
      </div>}
      {scanLimited && <div className="muted" style={{ padding: "8px 12px" }}>本页已达到扫描上限，可继续搜索。</div>}
      {nextCursor && (
        <div style={{ padding: "10px 12px" }}>
          <button className="btn" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "加载中…" : `继续搜索（已显示 ${rows?.length ?? 0} 条）`}
          </button>
        </div>
      )}
    </div>
  );
}
