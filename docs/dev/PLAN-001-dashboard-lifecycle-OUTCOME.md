# PLAN-001 Dashboard 生命周期控制台实施结果

## 完成内容

1. 新增 `qtask_list.admin.QueueAdmin`：
   - 统一队列发现、统计、任务列表、history 查询。
   - 支持 retry drain、DLQ 重放、单任务重试、stale recover、删除任务、清空队列、诊断。
   - 默认恢复 processing 时跳过 active worker。

2. 重写 Dashboard 后端 API：
   - `GET /api/queues`
   - `GET /api/workers`
   - `GET /api/queue/{name}/tasks`
   - `GET /api/queue/{name}/diagnose`
   - `POST /api/queue/{name}/retry`
   - `POST /api/queue/{name}/requeue-dlq`
   - `POST /api/queue/{name}/recover`
   - `POST /api/task/{task_id}/requeue`
   - `DELETE /api/task/{task_id}`

3. 将 Dashboard 前端改为 React 模块化实现：
   - `dashboard/static/js/api.js`
   - `dashboard/static/js/utils.js`
   - `dashboard/static/js/components.js`
   - `dashboard/static/js/app.js`
   - `dashboard/static/css/app.css`

4. 改善 Dashboard 用户体验：
   - 首屏为队列控制台，不做 landing page。
   - 左侧队列搜索与选择。
   - 中间按 ready/processing/retry/dlq/delay/history 查看任务。
   - 右侧诊断、Worker 状态、手动投递。
   - 单任务查看、重试、删除。
   - 批量 retry、DLQ 重放、安全恢复、强制恢复、清空队列。
   - `全部` 状态只显示实时队列任务，避免和 history 重复。

5. 更新 README：
   - 增加 `QueueAdmin` 用法。
   - 增加 React Dashboard 能力说明。

6. 增加测试：
   - 新增 `tests/test_dashboard_api.py`。
   - 使用隔离 Redis 前缀 `qtask_dash_test:*`，避免误删真实 `stockev:*` 队列。

## 验证结果

```bash
python -m pytest -q
# 100 passed

python -m ruff check .
# All checks passed

python -m mypy qtask_list cli dashboard
# Success: no issues found in 11 source files

node --input-type=module --check < dashboard/static/js/*.js
# all modules passed

git diff --check
# passed
```

## 页面验证

使用本地服务 `http://127.0.0.1:8766` 打开 Dashboard：

- React 页面成功渲染。
- `/static/js/*.js` 与 `/static/css/app.css` 正常加载。
- `/api/queues`、`/api/queue/{name}/tasks`、`/api/workers`、`/api/queue/{name}/diagnose` 正常请求。
- 浏览器 console 无业务错误。

## 后续建议

1. 将 CLI 逐步改为复用 `QueueAdmin`，减少 CLI 与 Dashboard 的实现重复。
2. 为 Dashboard 增加按 payload 字段过滤和批量删除能力，例如按 `symbol` 删除。
3. 增加权限/只读模式，避免生产环境误操作。
4. 支持用户保存常用队列分组，例如 `stockev.spiders` 的 spider 分组。
