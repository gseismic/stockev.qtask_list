import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { DiagnoseInfo, QueueStats, TaskRow } from "../types";
import { STATE_KEYS, STATE_LABELS, type StateKey, statForState } from "../types";
import { ConfirmDialog, ErrorBanner, OpsMenu, StateBadge } from "../components/ui";
import { TaskFilterBar, TaskTable, type TaskFilters } from "../components/TaskTable";
import { PushTaskDialog } from "../components/PushTaskDialog";
import { TaskDrawer } from "../components/TaskDrawer";

const EMPTY_HINTS: Partial<Record<StateKey, string>> = {
  dlq: "dlq 队列是空的——任务重试耗尽才会进入这里。",
  processing: "当前没有任务在执行。检查 Worker 是否在运行。",
  retry_wait: "没有等待自动重试的任务。任务失败后按退避策略会出现在这里。",
  deadline_missed: "没有过期任务。任务超过执行截止时间（expire）会被标记到这里。",
};

export function QueueDetailPage() {
  const { name = "" } = useParams();
  const queueName = decodeURIComponent(name);
  const [params, setParams] = useSearchParams();
  const stateParam = (params.get("state") as StateKey) || "all";

  const [stats, setStats] = useState<QueueStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [filters, setFilters] = useState<TaskFilters>({
    state: STATE_KEYS.includes(stateParam) ? stateParam : "all",
    search: "",
    createdAfter: "",
    createdBefore: "",
    completedAfter: "",
    completedBefore: "",
  });
  const [limit, setLimit] = useState(50);
  const [reloadKey, setReloadKey] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [openTask, setOpenTask] = useState<TaskRow | null>(null);
  const [diagnose, setDiagnose] = useState<DiagnoseInfo | null>(null);
  const [pushOpen, setPushOpen] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);

  useEffect(() => {
    if (STATE_KEYS.includes(stateParam)) setFilters((f) => ({ ...f, state: stateParam }));
  }, [stateParam]);

  const loadStats = async () => {
    try {
      const data = await api.diagnose(queueName);
      setStats(data.stats);
      setStatsError(null);
    } catch (e) {
      setStatsError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queueName, reloadKey]);

  const badges = useMemo(() => {
    if (!stats) return [];
    const items: Array<{ state: StateKey; count: number }> = [];
    if (stats.queue) items.push({ state: "ready", count: stats.queue });
    if (stats.processing) items.push({ state: "processing", count: stats.processing });
    if (stats.retry) items.push({ state: "retry", count: stats.retry });
    if (stats.retry_wait) items.push({ state: "retry_wait", count: stats.retry_wait });
    if (stats.delay) items.push({ state: "delay", count: stats.delay });
    if (stats.dlq) items.push({ state: "dlq", count: stats.dlq });
    if (stats.deadline_missed) items.push({ state: "deadline_missed", count: stats.deadline_missed });
    return items;
  }, [stats]);

  const setFilterState = (state: StateKey) => {
    const next = new URLSearchParams(params);
    next.set("state", state);
    setParams(next, { replace: true });
    setFilters((f) => ({ ...f, state }));
  };

  const runOp = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setOpError(null);
    try {
      await fn();
      setConfirm(null);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setOpError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const totalCount = stats && filters.state !== "all" ? statForState(stats, filters.state) : null;

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 className="page-title mono">{queueName}</h1>
        <span style={{ flex: 1 }} />
        <Link to="/queues">← 队列列表</Link>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "4px 0 14px" }}>
        {badges.map((b) => (
          <button
            key={b.state}
            className="badge"
            style={{
              background: b.state === "dlq" ? "var(--danger-dim)" : "var(--bg-surface-2)",
              color: b.state === "dlq" ? "var(--c-danger)" : "var(--text-secondary)",
              border: "none",
              cursor: "pointer",
            }}
            onClick={() => setFilterState(b.state)}
            title={`查看 ${STATE_LABELS[b.state]}`}
          >
            {b.state} {b.count}
          </button>
        ))}
        {stats && (stats.active_workers > 0 || stats.stale_workers > 0) && (
          <span className={`badge ${stats.stale_workers > 0 ? "c-danger" : "c-success"}`}>
            worker {stats.active_workers} 在线{stats.stale_workers ? ` / ${stats.stale_workers} 失联` : ""}
          </span>
        )}
      </div>

      {statsError && <ErrorBanner error={statsError} onRetry={loadStats} />}
      {opError && <ErrorBanner error={opError} onRetry={() => setOpError(null)} />}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <button className="btn primary" onClick={() => setPushOpen(true)}>
          投递测试任务
        </button>
        <OpsMenu
          label="操作"
          items={[
            { label: "drain 手动重试（retry → ready）", onClick: () => setConfirm("retry") },
            { label: "重放全部 DLQ", onClick: () => setConfirm("requeueDlq") },
            { label: "恢复失联 processing", onClick: () => setConfirm("recover") },
            { label: "诊断", onClick: async () => setDiagnose(await api.diagnose(queueName)) },
            { label: "清理 15 天前历史", onClick: () => setConfirm("cleanHistory") },
          ]}
        />
        <span style={{ flex: 1 }} />
        <OpsMenu
          label="⚠ 危险操作"
          items={[
            { label: "清空队列…", danger: true, onClick: () => setConfirm("clear") },
            { label: "删除队列…", danger: true, onClick: () => setConfirm("delete") },
          ]}
        />
      </div>

      <div className="tabs">
        {STATE_KEYS.map((s) => {
          const count = stats && s !== "all" ? statForState(stats, s) : null;
          const hot = s === "dlq" || s === "deadline_missed";
          return (
            <button
              key={s}
              className={`tab${filters.state === s ? " active" : ""}`}
              onClick={() => setFilterState(s)}
            >
              {STATE_LABELS[s]}
              {count !== null && count > 0 && <span className={`cnt${hot ? " hot" : ""}`}>{count}</span>}
            </button>
          );
        })}
      </div>

      <TaskFilterBar
        state={filters.state}
        onStateChange={setFilterState}
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

      {totalCount !== null && (
        <div className="faint" style={{ marginBottom: 6 }}>
          该状态共 {totalCount.toLocaleString("zh-CN")} 条
        </div>
      )}

      <TaskTable
        queue={queueName}
        filters={filters}
        limit={limit}
        onLimitChange={setLimit}
        onOpenTask={setOpenTask}
        reloadKey={reloadKey}
        autoRefresh={autoRefresh}
        emptyHint={
          <div>
            <p>{EMPTY_HINTS[filters.state] ?? "该筛选下没有任务。"}</p>
            {filters.state !== "all" && (
              <button className="btn sm" onClick={() => setFilterState("all")}>
                查看 {STATE_LABELS.all}
              </button>
            )}
          </div>
        }
      />

      {diagnose && (
        <div className="overlay" style={{ justifyContent: "center" }} onClick={() => setDiagnose(null)}>
          <div className="dialog" style={{ width: 560 }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>诊断 · {queueName}</h3>
            <ul>
              {diagnose.suggestions.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
            {diagnose.workers.length > 0 && (
              <>
                <div className="faint" style={{ marginTop: 10 }}>Worker</div>
                {diagnose.workers.map((w) => (
                  <div key={`${w.queue}:${w.worker_id}`} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0" }}>
                    <span className="mono">{w.worker_id}</span>
                    <StateBadge state={w.active ? "processing" : "failed"} label={w.active ? "在线" : "失联"} />
                    <span className="faint num">processing {w.processing}</span>
                  </div>
                ))}
              </>
            )}
            <div style={{ textAlign: "right", marginTop: 12 }}>
              <button className="btn" onClick={() => setDiagnose(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {pushOpen && (
        <PushTaskDialog queue={queueName} open onClose={() => setPushOpen(false)} onPushed={() => setReloadKey((k) => k + 1)} />
      )}

      {openTask?.task_id && (
        <TaskDrawer
          taskId={openTask.task_id}
          stateHint={String(openTask._state ?? openTask.state ?? "")}
          onClose={() => setOpenTask(null)}
          onChanged={() => setReloadKey((k) => k + 1)}
        />
      )}

      <ConfirmDialog
        open={confirm === "retry"}
        title="drain 手动重试"
        confirmText="执行"
        busy={busy}
        body={<p>retry 队列中的任务将移回 ready。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => runOp(() => api.retryQueue(queueName))}
      />
      <ConfirmDialog
        open={confirm === "requeueDlq"}
        title="重放全部 DLQ"
        confirmText="重放"
        busy={busy}
        body={<p>DLQ 中所有任务将重置重试计数并回到 ready。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => runOp(() => api.requeueDlq(queueName, null))}
      />
      <ConfirmDialog
        open={confirm === "recover"}
        title="恢复失联 processing"
        confirmText="恢复"
        busy={busy}
        body={<p>将把失联 Worker processing 中的任务移回主队列。活跃 Worker 不受影响。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => runOp(() => api.recoverQueue(queueName, false))}
      />
      <ConfirmDialog
        open={confirm === "cleanHistory"}
        title="清理 15 天前历史"
        confirmText="清理"
        busy={busy}
        body={<p>将删除该队列 15 天之前的历史记录（含任务 hash 与索引）。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => runOp(() => api.cleanHistory(queueName, 15))}
      />
      <ConfirmDialog
        open={confirm === "clear"}
        title={`清空队列 ${queueName}`}
        danger
        confirmKeyword={queueName}
        confirmText="清空"
        busy={busy}
        body={<p>将清空 ready/retry/delay/dlq 中所有消息（含 DLQ，不含历史）。此操作不可恢复。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => runOp(() => api.clearQueue(queueName, true, false, false))}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        title={`删除队列 ${queueName}`}
        danger
        confirmKeyword={queueName}
        confirmText="删除"
        busy={busy}
        body={<p>将删除该队列全部数据（含历史索引）。请确认没有 Worker 正在使用。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => runOp(async () => {
          await api.deleteQueue(queueName);
          window.location.href = "/queues";
        })}
      />
    </div>
  );
}
