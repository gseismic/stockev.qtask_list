# qtask_list Dashboard 登录与远程查看设计

## 背景

Dashboard 现在已经具备查看队列、重试、恢复、清空、投递等管理能力。如果直接暴露到远程网络，风险很高：

- 未授权用户可以看到 Redis 队列和任务 payload。
- 未授权用户可以执行重放、恢复、删除、清空、投递等写操作。
- 前端隐藏按钮不能作为安全边界，必须由后端保护页面和 API。

因此需要增加登录功能，让用户可以安全地远程查看 Dashboard。

## 真实使用场景

### 场景 1：本地开发

用户在本机运行：

```bash
qtask dashboard
```

默认不强制登录，保持原有本地开发体验。

### 场景 2：远程只给团队内部查看

用户在服务器运行：

```bash
qtask dashboard --host 0.0.0.0 --port 8765 --user admin --password '<strong-password>'
```

Dashboard 进入认证模式：

- 访问 `/` 时未登录跳转到 `/login`。
- 登录成功后写入 HttpOnly session cookie。
- 所有 `/api/*` 接口都校验 session。
- 登出后清除 cookie。

### 场景 3：使用进程管理器或容器部署

用户通过环境变量配置：

```bash
export QTASK_DASHBOARD_USER=admin
export QTASK_DASHBOARD_PASSWORD='<strong-password>'
export QTASK_DASHBOARD_SECRET='<random-secret>'
qtask dashboard --host 0.0.0.0 --no-open
```

`QTASK_DASHBOARD_SECRET` 用于签发会话；如果未提供，则使用密码派生签名 secret，服务重启后旧 cookie 仍可验证。

## 安全边界

本设计提供 Dashboard 应用层登录保护：

- 保护 HTML 页面。
- 保护所有 API。
- 登录 cookie 使用 `HttpOnly`、`SameSite=Lax`。
- 密码比较使用常量时间比较。
- session token 带过期时间和 HMAC 签名。

本设计不替代：

- HTTPS/TLS。
- 反向代理访问控制。
- Redis 网络访问控制。
- 多用户权限系统。

远程公网使用时仍建议放在 HTTPS 反向代理之后。

## 配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `QTASK_DASHBOARD_USER` | `admin` | 登录用户名，只有启用认证时使用 |
| `QTASK_DASHBOARD_PASSWORD` | 空 | 设置后启用登录认证 |
| `QTASK_DASHBOARD_SECRET` | 空 | session 签名 secret，未设置时使用密码 |
| `QTASK_DASHBOARD_SESSION_TTL` | `86400` | session 有效期，秒 |
| `QTASK_DASHBOARD_SECURE_COOKIE` | `0` | HTTPS 部署时设为 `1` |

## 非目标

- 不实现用户注册。
- 不实现多用户角色和 RBAC。
- 不保存密码到本地文件。
- 不在 Dashboard 内管理 HTTPS 证书。

## 验收标准

- 未启用认证时，现有 API 测试保持兼容。
- 启用认证时，未登录访问 `/` 跳转 `/login`。
- 启用认证时，未登录访问 `/api/queues` 返回 401。
- 登录失败返回 401，不设置 session cookie。
- 登录成功设置 HttpOnly session cookie。
- 登录后可访问页面和 API。
- 登出后 API 再次返回 401。
- CLI 支持 `--host`、`--user`、`--password`、`--session-ttl`、`--secure-cookie`。
