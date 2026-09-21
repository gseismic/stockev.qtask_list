# PLAN-018 Dashboard SPA 回退路由鉴权补齐

- 日期：2026-09-21
- 状态：已完成
- 关联：PLAN-004-dashboard-auth（登录系统本体）、PLAN-017-dashboard-react（React SPA）

## 背景

用户要求："启动管理后台服务时默认不需要登录，但如果设置了登录密码则只有登录才能查看和使用"。

经核查，该需求已由 PLAN-004 实现并通过测试：

- `dashboard/auth.py`：基于环境变量的认证（`QTASK_DASHBOARD_PASSWORD` 启用、
  HMAC-SHA256 签名 session cookie、`QTASK_DASHBOARD_SESSION_TTL` 有效期）。
- `dashboard/main.py`：`/api/*` 全部 `Depends(require_auth)`，`/` 未登录 307 跳 `/login`，
  `/api/login`、`/api/logout`、`/api/auth` 配套。
- CLI `qtask dashboard` 支持 `--user/--password/--secure-cookie/--session-ttl`。

实测确认（uvicorn 启动 + curl）：

- 未设密码：`/api/auth` 返回 `enabled:false`，`/` 与 `/api/queues` 均 200，无需登录。
- 设密码：`/api/queues` 401，`/` 307 → `/login`，登录成功后 200。

## 发现的问题

PLAN-017 引入 SPA 路由回退 `spa_fallback`（`GET /{full_path:path}`）时未做鉴权：
认证开启后直接访问 `/queues` 等客户端路由仍能取得 SPA 壳页面（数据接口虽 401，
但"只有登录才能查看"的边界不完整）。

## 变更内容

1. `dashboard/main.py`：`spa_fallback` 增加 `Request` 参数；认证开启且未登录时
   307 重定向到 `/login`，与 `/` 行为一致。
2. `tests/test_dashboard_api.py`：新增 `test_dashboard_auth_guards_spa_fallback`，
   覆盖未登录 307 → 登录后 200 的完整路径。
3. `README.md`：管理后台认证章节补充 SPA 路由同样受保护、默认不设密码无需登录的说明。

## 验证

- `python -m pytest tests/ -q`：119 passed
- `python -m ruff check .`：通过
- `python -m mypy qtask_list cli dashboard remote_storage`：通过

## 明确不做

- 多用户/RBAC、HTTPS 终结、反向代理访问控制（见 PLAN-004 OUTCOME 的边界说明）。
