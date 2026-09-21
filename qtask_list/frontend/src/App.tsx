import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { AppDataProvider, useAppData } from "./appData";
import { useTheme } from "./theme";
import type { RefreshInterval } from "./hooks";

function SideNav() {
  const { alerts } = useAppData();
  const items: Array<[string, string, string]> = [
    ["/", "总览", "📊"],
    ["/queues", "队列", "📋"],
    ["/tasks", "任务", "🔍"],
    ["/workers", "Worker", "🧑‍🏭"],
    ["/alerts", "告警", "🔔"],
    ["/guide", "教程", "📖"],
  ];
  return (
    <aside className="sidenav">
      <div className="brand">
        <div className="brand-mark">Q</div>
        <div>
          <div className="brand-title">qtask_list</div>
          <div className="brand-subtitle">管理后台</div>
        </div>
      </div>
      <nav>
        {items.map(([to, label, icon]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <span>{icon}</span>
            <span>{label}</span>
            {to === "/alerts" && alerts.length > 0 && <span className="nav-badge">{alerts.length}</span>}
          </NavLink>
        ))}
      </nav>
      <div className="foot">v0.2.0 · Redis 任务队列</div>
    </aside>
  );
}

function TopBar() {
  const { interval, setInterval, health, error } = useAppData();
  const { theme, toggle } = useTheme();
  const [username, setUsername] = useState<string | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const nextRef = useRef(location.pathname + location.search);

  useEffect(() => {
    api
      .auth()
      .then((a) => setUsername(a.enabled ? a.username : null))
      .catch(() => undefined);
  }, []);

  const logout = async () => {
    await api.logout().catch(() => undefined);
    navigate("/login");
  };

  const conn = error ? "bad" : health?.status === "ok" ? "ok" : "warn";
  return (
    <header className="topbar">
      <span title={conn === "ok" ? "Redis 连接正常" : conn === "warn" ? "等待数据" : `连接异常：${error ?? ""}`}>
        <span
          className="dot"
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: conn === "ok" ? "var(--c-success)" : conn === "warn" ? "var(--c-warning)" : "var(--c-danger)",
            marginRight: 6,
          }}
        />
        {conn === "ok" ? "已连接" : conn === "warn" ? "等待…" : "连接异常"}
      </span>
      <span className="spacer" />
      <label className="faint" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        自动刷新
        <select className="input" value={interval} onChange={(e) => setInterval(Number(e.target.value) as RefreshInterval)}>
          <option value={0}>关</option>
          <option value={5}>5s</option>
          <option value={15}>15s</option>
          <option value={30}>30s</option>
        </select>
      </label>
      <button className="btn sm" onClick={toggle} title="切换白天/黑夜">
        {theme === "light" ? "🌙 黑夜" : "☀️ 白天"}
      </button>
      {username && (
        <>
          <span className="faint">{username}</span>
          <button className="btn sm" onClick={logout}>
            退出
          </button>
        </>
      )}
      {nextRef.current.length < 0 && <span />}
    </header>
  );
}

export default function App() {
  return (
    <AppDataProvider>
      <div className="app">
        <SideNav />
        <div className="main">
          <TopBar />
          <Outlet />
        </div>
      </div>
    </AppDataProvider>
  );
}
