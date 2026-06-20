# PLAN-004 Dashboard 登录与远程查看

## 目标

为 Dashboard 增加后端登录保护，使其可以配置账号密码后安全地远程查看。

## 实施方案

1. 新增 Dashboard session 认证模块：
   - 读取认证环境变量。
   - 生成和校验 HMAC session token。
   - 提供 `require_auth` FastAPI dependency。
2. 新增登录/登出接口：
   - `GET /login`
   - `POST /api/login`
   - `POST /api/logout`
   - `GET /api/auth`
3. 保护页面和 API：
   - `/` 未登录跳转 `/login`。
   - `/api/*` 管理接口未登录返回 401。
   - `/api/login`、`/api/logout`、`/api/auth` 保持公开。
4. 增加登录页模板和前端请求封装：
   - 401 时跳转 `/login`。
   - 顶部增加登出按钮。
5. CLI 支持远程部署参数：
   - `--host`
   - `--user`
   - `--password`
   - `--session-ttl`
   - `--secure-cookie`
6. 更新 README。
7. 补充认证测试。

## 验证

- `python -m pytest -q`
- `python -m ruff check .`
- `python -m mypy qtask_list cli dashboard`
- JS 模块语法检查
- 浏览器验证登录、登出和远程保护路径
