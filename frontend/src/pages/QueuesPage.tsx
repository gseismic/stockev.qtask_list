import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAppData } from "../appData";
import { RateSampler, fmtInt, fmtRate } from "../sampler";
import { ConfirmDialog, EmptyState, ErrorBanner, OpsMenu, ProgressBar, Skeleton, StateBadge } from "../components/ui";
import { PushTaskDialog } from "../components/PushTaskDialog";
import type { QueueInfo } from "../types";

type FilterKey = "all" | "active" | "abnormal";

export function QueuesPage() {
  const navigate = useNavigate();
  const { queues, error, loading } = useAppData();
  const [params] = useSearchParams();
  const samplerRef = useMemo(() => new RateSampler(), []);
  const [nsFilter, setNsFilter] = useState("全部");
  const [nameSearch, setNameSearch] = useState("");
  const filter = (params.get("filter") as FilterKey) || "all";

  const [confirm, setConfirm] = useState<{ kind: "clear" | "delete" | "recover" | "requeueDlq" | "retry"; queue: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  const [pushQueue, setPushQueue] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  if (queues) samplerRef.observe(queues);

  const namespaces = useMemo(() => {
    const set = new Set<string>();
    for (const q of queues ?? []) {
      const name = String(q.name);
      set.add(name.includes(":") ? name.slice(0, name.indexOf(":")) : "(默认)");
    }
    return ["全部", ...[...set].sort()];
  }, [queues]);

  const cards = useMemo(() => {
    if (!queues) return [];
    const list = queues.map((item: QueueInfo) => {
      const stats = item as unknown as Record<string, number>;
      const name = String(item.name);
      const progress = samplerRef.progress(name, stats);
      const abnormal =
        (stats.dlq ?? 0) > 0 || progress.stalled || (stats.stale_workers ?? 0) > 0 || (stats.deadline_missed ?? 0) > 0;
      return { name, stats, progress, abnormal };
    });
    let out = list;
    if (filter === "active") out = out.filter((c) => c.progress.remaining > 0);
    if (filter === "abnormal") out = out.filter((c) => c.abnormal);
    if (nsFilter !== "全部") {
      out = out.filter((c) => (nsFilter === "(默认)" ? !c.name.includes(":") : c.name.startsWith(`${nsFilter}:`)));
    }
    if (nameSearch) out = out.filter((c) => c.name.toLowerCase().includes(nameSearch.toLowerCase()));
    return out.sort((a, b) => (a.abnormal === b.abnormal ? b.progress.remaining - a.progress.remaining : a.abnormal ? -1 : 1));
  }, [queues, filter, nsFilter, nameSearch, samplerRef]);

  const runOp = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setOpError(null);
    try {
      await fn();
      setConfirm(null);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setOpError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">队列</h1>
      <p className="page-desc">一个抓取任务类型 = 一个队列（ns:name）。这里管理与入口；盯盘优先用总览。</p>
      {error && <ErrorBanner error={error} onRetry={() => window.location.reload()} />}
      {opError && <ErrorBanner error={opError} onRetry={() => setOpError(null)} />}
      {refreshKey < 0 && <span />}

      {loading && !queues ? (
        <Skeleton lines={6} height={40} />
      ) : queues && queues.length === 0 ? (
        <EmptyState icon="📭" title="没有队列，去投递第一条任务">
          用 <code>qtask push</code> 投递，或启动你的 Worker 后回来看这里。
        </EmptyState>
      ) : (
        <>
          <div className="filter-bar">
            <select className="input" value={nsFilter} onChange={(e) => setNsFilter(e.target.value)}>
              {namespaces.map((ns) => (
                <option key={ns}>{ns}</option>
              ))}
            </select>
            <div className="seg">
              {(
                [
                  ["all", "全部"],
                  ["active", "进行中"],
                  ["abnormal", "仅异常"],
                ] as Array<[FilterKey, string]>
              ).map(([key, label]) => (
                <button key={key} className={filter === key ? "on" : ""} onClick={() => navigate(`/queues${key === "all" ? "" : `?filter=${key}`}`)}>
                  {label}
                </button>
              ))}
            </div>
            <input className="input" placeholder="搜索队列名" value={nameSearch} onChange={(e) => setNameSearch(e.target.value)} />
            <span className="faint">共 {cards.length} 个</span>
          </div>

          <div className="queue-grid">
            {cards.map((c) => (
              <div className="card queue-card" key={c.name}>
                <div className="qc-head">
                  <span className="qc-name" title={c.name}>{c.name}</span>
                  <span style={{ flex: 1 }} />
                  {c.abnormal && <StateBadge state="failed" label="异常" />}
                </div>
                <ProgressBar done={c.progress.completed1h ?? 0} remaining={c.progress.remaining} dlq={c.stats.dlq ?? 0} />
                <div className="qc-stats num">
                  剩余 {fmtInt(c.progress.remaining)} · 完成/1h {fmtInt(c.progress.completed1h)} · 速率 {fmtRate(c.progress.ratePerMin)} ·{" "}
                  {c.progress.stalled ? <span style={{ color: "var(--c-danger)" }}>停滞 ⚠</span> : c.progress.etaText ?? "ETA —"}
                  {(c.stats.dlq ?? 0) > 0 && <span style={{ color: "var(--c-danger)" }}> · DLQ {c.stats.dlq}</span>}
                </div>
                <div className="qc-actions">
                  <button className="btn sm" onClick={() => navigate(`/queues/${encodeURIComponent(c.name)}`)}>
                    详情
                  </button>
                  <OpsMenu
                    label="操作"
                    items={[
                      { label: "投递测试任务", onClick: () => setPushQueue(c.name) },
                      { label: "重放全部 DLQ", onClick: () => setConfirm({ kind: "requeueDlq", queue: c.name }) },
                      { label: "恢复失联 processing", onClick: () => setConfirm({ kind: "recover", queue: c.name }) },
                      { label: "drain 手动重试", onClick: () => setConfirm({ kind: "retry", queue: c.name }) },
                      { label: "清空队列", danger: true, onClick: () => setConfirm({ kind: "clear", queue: c.name }) },
                      { label: "删除队列", danger: true, onClick: () => setConfirm({ kind: "delete", queue: c.name }) },
                    ]}
                  />
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {pushQueue && (
        <PushTaskDialog
          queue={pushQueue}
          open
          onClose={() => setPushQueue(null)}
          onPushed={() => setRefreshKey((k) => k + 1)}
        />
      )}
      <ConfirmDialog
        open={confirm?.kind === "clear"}
        title={`清空队列 ${confirm?.queue ?? ""}`}
        danger
        confirmKeyword={confirm?.queue}
        confirmText="清空"
        busy={busy}
        body={<p>将清空 ready/retry/delay/dlq 中所有消息（默认含 DLQ，不含历史）。identity 保留。此操作不可恢复。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && runOp(() => api.clearQueue(confirm.queue, true, false, false))}
      />
      <ConfirmDialog
        open={confirm?.kind === "delete"}
        title={`删除队列 ${confirm?.queue ?? ""}`}
        danger
        confirmKeyword={confirm?.queue}
        confirmText="删除"
        busy={busy}
        body={<p>将删除该队列全部数据（含历史索引）。请确认没有 Worker 正在使用。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && runOp(() => api.deleteQueue(confirm.queue))}
      />
      <ConfirmDialog
        open={confirm?.kind === "recover"}
        title={`恢复失联 processing ${confirm?.queue ?? ""}`}
        confirmText="恢复"
        busy={busy}
        body={<p>将把失联 Worker processing 中的任务移回主队列。活跃 Worker 不受影响。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && runOp(() => api.recoverQueue(confirm.queue, false))}
      />
      <ConfirmDialog
        open={confirm?.kind === "requeueDlq"}
        title={`重放全部 DLQ ${confirm?.queue ?? ""}`}
        confirmText="重放"
        busy={busy}
        body={<p>DLQ 中所有任务将重置重试计数并回到 ready。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && runOp(() => api.requeueDlq(confirm.queue, null))}
      />
      <ConfirmDialog
        open={confirm?.kind === "retry"}
        title={`drain 手动重试 ${confirm?.queue ?? ""}`}
        confirmText="执行"
        busy={busy}
        body={<p>retry 队列中的任务将移回 ready。</p>}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && runOp(() => api.retryQueue(confirm.queue))}
      />
    </div>
  );
}
