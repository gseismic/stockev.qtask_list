import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAppData } from "../appData";
import { ALERT_RULES_DOC, clearResolved, resolveAlert } from "../alerts";
import { EmptyState, ErrorBanner, Skeleton } from "../components/ui";
import { fmtTime } from "../components/ui";

export function AlertsPage() {
  const { alerts, error, loading } = useAppData();
  const [tab, setTab] = useState<"open" | "resolved">("open");
  const [showRules, setShowRules] = useState(false);
  const [version, setVersion] = useState(0);

  const resolvedMap = useMemo(() => {
    void version;
    try {
      return JSON.parse(localStorage.getItem("qtask.alerts.resolved.v1") || "{}") as Record<string, number>;
    } catch {
      return {};
    }
  }, [version, alerts]);

  const resolvedKeys = Object.keys(resolvedMap);

  return (
    <div className="page">
      <h1 className="page-title">告警中心</h1>
      <p className="page-desc">DLQ 堆积、失败率、失联 Worker、积压、过期与 Redis 内存的聚合信号。</p>
      {error && <ErrorBanner error={error} onRetry={() => window.location.reload()} />}

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <div className="seg">
          <button className={tab === "open" ? "on" : ""} onClick={() => setTab("open")}>
            未处理 ({alerts.length})
          </button>
          <button className={tab === "resolved" ? "on" : ""} onClick={() => setTab("resolved")}>
            已处理 ({resolvedKeys.length})
          </button>
        </div>
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => setShowRules((v) => !v)}>
          规则说明 ⓘ
        </button>
      </div>

      {showRules && (
        <div className="card" style={{ marginBottom: 16 }}>
          <table className="ops-table">
            <thead>
              <tr>
                <th>规则</th>
                <th>条件</th>
                <th>严重度</th>
                <th>数据源</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {ALERT_RULES_DOC.map((r) => (
                <tr key={r.name}>
                  <td>{r.name}</td>
                  <td className="mono">{r.rule}</td>
                  <td>
                    <span className={`badge ${r.severity === "danger" ? "c-danger" : "c-warning"}`}>{r.severity}</span>
                  </td>
                  <td className="faint">{r.source}</td>
                  <td className="muted">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="faint">阈值为初始假设，待实际运行校准；"标记已处理"仅存本浏览器，同规则复发会重新出现。</p>
        </div>
      )}

      {loading && !alerts ? (
        <Skeleton lines={5} height={30} />
      ) : tab === "open" ? (
        alerts.length === 0 ? (
          <EmptyState icon="🎉" title="当前没有告警">
            <p className="muted">
              告警规则：DLQ&gt;0、累计失败率&gt;5%、stale worker、ready&gt;10000 持续 10 分钟、任务过期、Worker 心跳超时、Redis 内存超限。
            </p>
          </EmptyState>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {alerts.map((a) => (
              <div key={a.key} className="card" style={{ padding: 12, borderColor: a.severity === "danger" ? "var(--danger-border)" : undefined }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <span className={`badge ${a.severity === "danger" ? "c-danger" : "c-warning"}`}>
                    <span className="dot" style={{ background: "currentColor" }} />
                    {a.ruleLabel}
                  </span>
                  <b>{a.target}</b>
                  <span style={{ flex: 1 }}>{a.detail}</span>
                  <span className="faint num">{fmtTime(a.firstSeen / 1000)}</span>
                  {a.locate.page === "queue" && a.locate.queue && (
                    <Link className="btn sm" to={`/queues/${encodeURIComponent(a.locate.queue)}?state=${a.locate.state ?? "all"}`}>
                      定位
                    </Link>
                  )}
                  {a.locate.page === "workers" && (
                    <Link className="btn sm" to="/workers">
                      查看 Worker
                    </Link>
                  )}
                  <button
                    className="btn sm"
                    onClick={() => {
                      resolveAlert(a.key);
                      setVersion((v) => v + 1);
                    }}
                  >
                    标记已处理
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      ) : resolvedKeys.length === 0 ? (
        <EmptyState icon="🗂" title="还没有已处理记录" />
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {resolvedKeys.map((key) => (
            <div key={key} className="card" style={{ padding: 12 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span className="badge c-muted">已处理</span>
                <code className="mono">{key}</code>
                <span style={{ flex: 1 }} />
                <span className="faint num">{fmtTime(resolvedMap[key] / 1000)}</span>
              </div>
            </div>
          ))}
          <div>
            <button
              className="btn sm"
              onClick={() => {
                clearResolved();
                setVersion((v) => v + 1);
              }}
            >
              清空已处理记录
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
