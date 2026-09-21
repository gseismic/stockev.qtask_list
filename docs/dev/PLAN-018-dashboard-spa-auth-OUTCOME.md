# PLAN-018 结果：Dashboard SPA 回退路由鉴权补齐

- 日期：2026-09-21
- 计划文件：`docs/dev/PLAN-018-dashboard-spa-auth.md`

## 结论

登录系统本体已由 PLAN-004 交付并实测符合"默认免登录、设密码强制登录"的需求；
本轮补齐 PLAN-017 引入的 SPA 路由回退缺口。

## 变更清单

1. `dashboard/main.py`：`spa_fallback` 增加认证守卫，认证开启且未登录时 307 跳 `/login`。
2. `tests/test_dashboard_api.py`：新增 `test_dashboard_auth_guards_spa_fallback`（32 个 dashboard 用例 → 33）。
3. `README.md`：认证章节补充 SPA 路由保护与默认免登录说明。

## 验证

- pytest 119 passed（全量）
- ruff、mypy 通过
- 手工实测：无密码直接访问 200；设 `QTASK_DASHBOARD_PASSWORD` 后 `/api/queues` 401、
  `/` 与 `/queues` 307 → `/login`，登录后恢复 200（测试后服务已停止）。

## 配置速查

| 环境变量 | 默认 | 说明 |
|---------|------|------|
| `QTASK_DASHBOARD_PASSWORD` | 空 | 设置后启用登录认证 |
| `QTASK_DASHBOARD_USER` | `admin` | 登录用户名 |
| `QTASK_DASHBOARD_SECRET` | 空则用密码 | session 签名 secret |
| `QTASK_DASHBOARD_SESSION_TTL` | `86400` | 会话有效期（秒） |
| `QTASK_DASHBOARD_SECURE_COOKIE` | `0` | HTTPS 部署设为 `1` |
