import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAppData } from "../appData";
import { RateSampler, fmtInt, fmtRate } from "../sampler";
import { EmptyState, ErrorBanner, KpiCard, ProgressBar, Skeleton } from "../components/ui";

type FilterKey = "all" | "active" | "abnormal";

export function OverviewPage() {
  const navigate = useNavigate();
  const { queues, alerts, error, loading, reload } = useAppData();
  const samplerRef = useRef(new RateSampler());
  const [sampleVersion, setSampleVersion] = useState(0);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (queues) {
      samplerRef.current.observe(queues);
      setSampleVersion((value) => value + 1);
    }
  }, [queues]);

  const rows = useMemo(() => {
    if (!queues) return [];
    return queues.map((item) => {
      const stats = item as unknown as Record<string, number>;
      const progress = samplerRef.current.progress(String(item.name), stats);
      const noWorker = (stats.queue ?? 0) > 0 && (stats.active_workers ?? 0) === 0;
      const scheduledOnly = (stats.queue ?? 0) === 0 && (stats.processing ?? 0) === 0 &&
        (stats.retry ?? 0) === 0 && (stats.delay ?? 0) > 0;
      const abnormal =
        (stats.dlq ?? 0) > 0 || progress.stalled || noWorker || (stats.stale_workers ?? 0) > 0 || (stats.deadline_missed ?? 0) > 0;
      return { name: String(item.name), stats, progress, abnormal, noWorker, scheduledOnly };
    });
  }, [queues, sampleVersion]);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof rows>();
    for (const row of rows) {
      const ns = row.name.includes(":") ? row.name.slice(0, row.name.indexOf(":")) : "(默认)";
      if (!map.has(ns)) map.set(ns, []);
      map.get(ns)!.push(row);
    }
    for (const group of map.values()) {
      group.sort((a, b) => {
        if (a.abnormal !== b.abnormal) return a.abnormal ? -1 : 1;
        return b.progress.remaining - a.progress.remaining;
      });
    }
    return [...map.entries()].sort((a, b) => {
      const abn = (g: typeof rows) => g.filter((r) => r.abnormal).length;
      if (abn(b[1]) !== abn(a[1])) return abn(b[1]) - abn(a[1]);
      return a[0].localeCompare(b[0]);
    });
  }, [rows]);

  const totalRemaining = rows.reduce((acc, r) => acc + r.progress.remaining, 0);
  const activeCount = rows.filter((r) => r.progress.remaining > 0).length;
  const abnormalCount = rows.filter((r) => r.abnormal).length;
  const totalRate = rows.reduce((acc, r) => acc + (r.progress.ratePerMin ?? 0), 0);
  const hasRateSample = rows.some((r) => r.progress.ratePerMin !== null);

  const visibleGroups = grouped
    .map(([ns, group]) => {
      let g = group;
      if (filter === "active") g = g.filter((r) => r.progress.remaining > 0);
      if (filter === "abnormal") g = g.filter((r) => r.abnormal);
      return [ns, g] as const;
    })
    .filter(([, g]) => g.length > 0);

  return (
    <div className="page">
      <h1 className="page-title">总览</h1>
      <p className="page-desc">盯盘与巡检：当前待处理量、浏览器观测期完成量和估计速率。图形是观测参考比，不代表本轮批次完成率；延迟任务不参与 ETA。</p>
      {error && <ErrorBanner error={error} onRetry={reload} />}

      {loading && !queues ? (
        <Skeleton lines={6} height={40} />
      ) : !queues ? (
        <EmptyState icon="⚠" title="监控数据不可用">连接恢复后点击顶栏“刷新”。</EmptyState>
      ) : queues && queues.length === 0 ? (
        <EmptyState icon="📭" title="还没有任何队列">
          用 <code>qtask push</code> 或者在队列页投递第一条任务后，这里会出现运行总览。
        </EmptyState>
      ) : (
        <>
          <div className="kpi-row">
            <KpiCard
              label="活跃队列"
              value={`${activeCount}/${rows.length}`}
              hint="剩余>0 的队列数"
              onClick={() => navigate("/queues?filter=active")}
              title="点击查看队列列表（进行中）"
            />
            <KpiCard
              label="总剩余"
              value={fmtInt(totalRemaining)}
              hint="ready+processing+retry+delay"
              onClick={() => navigate("/queues?filter=active")}
            />
            <KpiCard
              label="合计速率"
              value={fmtRate(hasRateSample ? totalRate : null)}
              hint="观测至少 30 秒后估算（条/min）"
              title="浏览器采样估算；未采满 30 秒时显示 —"
            />
            <KpiCard
              label="异常队列"
              value={abnormalCount}
              danger={abnormalCount > 0}
              hint="DLQ/无 Worker/疑似停滞/失联/过期"
              onClick={() => setFilter("abnormal")}
              title="点击在本页只看异常队列"
            />
          </div>

          {alerts.length > 0 && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
                <h2 className="card-title" style={{ margin: 0 }}>最新告警</h2>
                <span style={{ flex: 1 }} />
                <Link to="/alerts">查看全部 →</Link>
              </div>
              {alerts.slice(0, 5).map((a) => (
                <div key={a.key} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0" }}>
                  <span className={`badge ${a.severity === "danger" ? "c-danger" : "c-warning"}`}>
                    <span className="dot" style={{ background: "currentColor" }} />
                    {a.ruleLabel}
                  </span>
                  <span style={{ minWidth: 160, fontWeight: 600 }}>{a.target}</span>
                  <span className="muted" style={{ flex: 1 }}>{a.detail}</span>
                  {a.locate.page === "queue" && a.locate.queue && (
                    <Link to={`/queues/${encodeURIComponent(a.locate.queue)}?state=${a.locate.state ?? "all"}`}>定位</Link>
                  )}
                  {a.locate.page === "workers" && <Link to="/workers">查看</Link>}
                </div>
              ))}
            </div>
          )}

          <div className="section-head">
            <h2>队列运行矩阵</h2>
            <div className="seg">
              {(
                [
                  ["all", "全部"],
                  ["active", "进行中"],
                  ["abnormal", "仅异常"],
                ] as Array<[FilterKey, string]>
              ).map(([key, label]) => (
                <button key={key} className={filter === key ? "on" : ""} onClick={() => setFilter(key)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="matrix-columns" aria-hidden="true">
            <span>队列</span><span title="绿：观测期完成；蓝：待处理；红：DLQ">构成 ⓘ</span>
            <span>待处理</span><span>观测完成</span><span>条/min</span><span>预计/状态</span><span>异常</span>
          </div>

          {visibleGroups.map(([ns, group]) => {
            const isCollapsed = collapsed.has(ns);
            const gRemaining = group.reduce((a, r) => a + r.progress.remaining, 0);
            const gDone = group.reduce((a, r) => a + (r.progress.completedObserved ?? 0), 0);
            const gAbn = group.filter((r) => r.abnormal).length;
            return (
              <div className="matrix-group" key={ns}>
                <button
                  className="group-head"
                  onClick={() =>
                    setCollapsed((prev) => {
                      const next = new Set(prev);
                      if (next.has(ns)) next.delete(ns);
                      else next.add(ns);
                      return next;
                    })
                  }
                >
                  <span>{isCollapsed ? "▸" : "▾"}</span>
                  <span>{ns}</span>
                  <span className="muted">· {group.length} 个队列</span>
                  <span className="muted num">· 剩余 {fmtInt(gRemaining)}</span>
                  <span className="muted num">· 观测期完成 {fmtInt(gDone)}</span>
                  {gAbn > 0 && <span className="badge c-danger">⚠ {gAbn}</span>}
                  {gRemaining === 0 && <span className="badge c-muted">空闲</span>}
                </button>
                {!isCollapsed &&
                  group.map((r) => (
                    <div
                      key={r.name}
                      className={`matrix-row${r.abnormal ? " abnormal" : ""}`}
                      onClick={() => navigate(`/queues/${encodeURIComponent(r.name)}`)}
                    >
                      <span className="qname" title={r.name}>{r.name}</span>
                      <ProgressBar
                        done={r.progress.completedObserved ?? 0}
                        remaining={r.progress.remaining}
                        dlq={r.stats.dlq ?? 0}
                      />
                      <span className="num">{fmtInt(r.progress.remaining)}</span>
                      <span className="num">{fmtInt(r.progress.completedObserved)}</span>
                      <span className="num">{fmtRate(r.progress.ratePerMin)}</span>
                      <span className="num" style={{ color: r.progress.stalled ? "var(--c-danger)" : undefined }}>
                        {r.noWorker ? "无 Worker" : r.progress.stalled ? "疑似停滞 ⚠" : r.scheduledOnly ? "等待调度" : r.progress.etaText ?? "—"}
                      </span>
                      <span>
                        {(r.stats.dlq ?? 0) > 0 && <span className="badge c-danger">DLQ {r.stats.dlq}</span>}
                        {(r.stats.stale_workers ?? 0) > 0 && <span className="badge c-danger">失联 {r.stats.stale_workers}</span>}
                        {(r.stats.deadline_missed ?? 0) > 0 && <span className="badge c-warning">过期 {r.stats.deadline_missed}</span>}
                        {r.noWorker && <span className="badge c-danger">无 Worker</span>}
                        {r.stats.observation_indexed === 0 && <span className="badge c-warning" title="旧队列需运行 rebuild-observation 回填监控索引">指标需回填</span>}
                        {!r.abnormal && r.scheduledOnly && <span className="badge c-warning">等待调度</span>}
                        {!r.abnormal && !r.scheduledOnly && r.progress.remaining > 0 && <span className="badge c-primary">进行中</span>}
                        {!r.abnormal && r.progress.remaining === 0 && <span className="badge c-success">空闲</span>}
                      </span>
                    </div>
                  ))}
              </div>
            );
          })}
          {queues && queues.length > 0 && visibleGroups.length === 0 && (
            <EmptyState icon="🔍" title="当前筛选下没有队列">
              <button className="btn" onClick={() => setFilter("all")}>切回全部</button>
            </EmptyState>
          )}
        </>
      )}
    </div>
  );
}
