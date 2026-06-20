# PLAN-004 Dashboard 登录与远程查看结果

## 完成内容

- 新增认证设计文档：`docs/design/qtask-dashboard-20260620-auth.md`。
- 新增后端认证模块：`dashboard/auth.py`。
  - 通过 `QTASK_DASHBOARD_PASSWORD` 或 `QTASK_DASHBOARD_AUTH=1` 启用认证。
  - 使用 HMAC 签名 session token。
  - session cookie 使用 `HttpOnly`、`SameSite=Lax`。
  - 支持 `QTASK_DASHBOARD_SECURE_COOKIE=1` 适配 HTTPS 部署。
- 新增认证接口：
  - `GET /login`
  - `GET /api/auth`
  - `POST /api/login`
  - `POST /api/logout`
- 后端保护：
  - 认证启用后，未登录访问 `/` 会跳转 `/login`。
  - 认证启用后，未登录访问管理 API 返回 401。
  - 登录、登出和认证状态接口保持公开。
- 新增登录页：`dashboard/templates/login.html`。
- 前端更新：
  - API 收到 401 自动跳转登录页。
  - 登录模式下顶部显示“退出”。
  - 登录后跳回原页面。
- CLI 更新：
  - 新增 `--host`
  - 新增 `--user`
  - 新增 `--password`
  - 新增 `--session-ttl`
  - 新增 `--secure-cookie`
  - 默认监听地址改为 `127.0.0.1`，远程查看需要显式传 `--host 0.0.0.0`。
- Python API `start_dashboard()` 同步支持远程登录参数。
- README 增加远程查看和环境变量示例。
- Dashboard API 测试新增认证覆盖。

## 验证结果

- `python -m pytest tests/test_dashboard_api.py -q`：8 passed
- `python -m pytest -q`：66 passed
- `python -m ruff check .`：通过
- `python -m mypy qtask_list cli dashboard`：通过
- JS 模块语法检查：通过
- HTTP 验证：
  - 未登录访问 `/` 返回跳转 `/login`。
  - 未登录访问 `/api/queues` 返回 401。
  - 正确账号密码登录后返回 `Set-Cookie: qtask_dashboard_session=...; HttpOnly; SameSite=lax`。
- 浏览器验证：
  - 未登录打开 `/` 自动进入 `/login`。
  - 错误密码显示“用户名或密码错误”。
  - 正确密码登录后进入 Dashboard。
  - 前端 `document.cookie` 读不到 session cookie，说明 HttpOnly 生效。
  - 顶部显示“退出”。
  - 退出后回到 `/login`，API 再次返回 401。

## 安全说明

本次实现的是 Dashboard 应用层登录保护，不替代 HTTPS、反向代理访问控制、Redis 网络访问控制或多用户 RBAC。远程公网部署仍建议放在 HTTPS 反向代理之后，并开启 `QTASK_DASHBOARD_SECURE_COOKIE=1`。
