import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAppData } from "../appData";
import { RateSampler, fmtInt, fmtRate } from "../sampler";
import { EmptyState, ErrorBanner, KpiCard, ProgressBar, Skeleton } from "../components/ui";

type FilterKey = "all" | "active" | "abnormal";

export function OverviewPage() {
  const navigate = useNavigate();
  const { queues, alerts, error, loading } = useAppData();
  const samplerRef = useRef(new RateSampler());
  const [filter, setFilter] = useState<FilterKey>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  if (queues) samplerRef.current.observe(queues);

  const rows = useMemo(() => {
    if (!queues) return [];
    return queues.map((item) => {
      const stats = item as unknown as Record<string, number>;
      const progress = samplerRef.current.progress(String(item.name), stats);
      const abnormal =
        (stats.dlq ?? 0) > 0 || progress.stalled || (stats.stale_workers ?? 0) > 0 || (stats.deadline_missed ?? 0) > 0;
      return { name: String(item.name), stats, progress, abnormal };
    });
  }, [queues]);

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
      <p className="page-desc">盯盘与巡检：每条队列完成多少、剩多少、预计多久、是否停滞。</p>
      {error && <ErrorBanner error={error} onRetry={() => window.location.reload()} />}

      {loading && !queues ? (
        <Skeleton lines={6} height={40} />
      ) : queues && queues.length === 0 ? (
        <EmptyState icon="📭" title="还没有任何队列">
          用 <code>qtask push</code> 或者在队列页投递第一条任务后，这里会出现进度总览。
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
              hint="ready+processing+retry_wait+delay"
              onClick={() => navigate("/queues?filter=active")}
            />
            <KpiCard
              label="合计速率"
              value={fmtRate(totalRate)}
              hint="前端采样估算（条/min）"
              title="口径：对 /api/queues 的周期采样差值；冷启动约 10s 内显示 —"
            />
            <KpiCard
              label="异常队列"
              value={abnormalCount}
              danger={abnormalCount > 0}
              hint="DLQ/停滞/失联/过期"
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
            <h2>队列进度矩阵</h2>
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

          {visibleGroups.map(([ns, group]) => {
            const isCollapsed = collapsed.has(ns);
            const gRemaining = group.reduce((a, r) => a + r.progress.remaining, 0);
            const gDone = group.reduce((a, r) => a + (r.progress.completed1h ?? 0), 0);
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
                  <span className="muted num">· 完成/1h {fmtInt(gDone)}</span>
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
                        done={r.progress.completed1h ?? 0}
                        remaining={r.progress.remaining}
                        dlq={r.stats.dlq ?? 0}
                      />
                      <span className="num">
                        {r.progress.progressPct === null ? "—" : `${Math.round(r.progress.progressPct)}%`}
                      </span>
                      <span className="num">{fmtInt(r.progress.remaining)}</span>
                      <span className="num">{fmtInt(r.progress.completed1h)}</span>
                      <span className="num">{fmtRate(r.progress.ratePerMin)}</span>
                      <span className="num" style={{ color: r.progress.stalled ? "var(--c-danger)" : undefined }}>
                        {r.progress.stalled ? "停滞 ⚠" : r.progress.etaText ?? "—"}
                      </span>
                      <span>
                        {(r.stats.dlq ?? 0) > 0 && <span className="badge c-danger">DLQ {r.stats.dlq}</span>}
                        {(r.stats.stale_workers ?? 0) > 0 && <span className="badge c-danger">失联 {r.stats.stale_workers}</span>}
                        {(r.stats.deadline_missed ?? 0) > 0 && <span className="badge c-warning">过期 {r.stats.deadline_missed}</span>}
                        {!r.abnormal && r.progress.remaining > 0 && <span className="badge c-primary">进行中</span>}
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
