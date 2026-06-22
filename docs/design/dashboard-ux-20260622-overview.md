# Dashboard UX 综合优化 - 设计文档

## 问题诊断

用户反馈 7 个问题，逐一分析根因：

### 问题 1: 页面加载慢，初始显示空白

**根因**:
- `admin.list_queues()` 调用 `queue_names()` 全量 SCAN Redis keys，再对每个队列调用 `queue_stats()`。
- `queue_stats()` 的 `_history_stats()` 对每个队列采样 2000 条计算 completed/failed 比例。
- 前端初始 state 为空数组/空字符串，渲染"没有队列"等占位。
- 3s 自动刷新后数据才到达，但首屏体验差。

**方案**:
- 后端：`_history_stats` 改为异步批量采集（Pipeline 聚合），减少 RTT。
- 前端：增加 loading 骨架屏，`health` 和 `queues` 请求未完成前显示加载状态。

### 问题 2: 命名空间"无命名空间"不可点击

**根因**:
- `extractNamespace("myqueue")` 返回 `""`（空字符串）。
- 命名空间 chips 渲染时，"全部" button 调用 `onNamespaceFilter("")`，"无命名空间" button 也调用 `onNamespaceFilter("")`。
- 由于 `namespaceFilter` 初始值就是 `""`，且过滤逻辑 `namespaceFilter ? filter : all` 中 `""` 为 falsy，导致两个按钮行为完全一样——都无法实现"仅显示无命名空间队列"。

**方案**:
- 使用哨兵值区分"全部"（`null`/`undefined`）和"无命名空间"（`""`）。
- 前端: `namespaceFilter` 初始 `null`。过滤逻辑: `namespaceFilter === null ? all : filter by ns`。
- "全部" chip 发送 `null`，"无命名空间" chip 发送 `""`。

### 问题 3: 标签页状态与队列状态不一致，缺少"已完成"

**根因**:
- `states` 数组: `["all", "ready", "processing", "retry", "dlq", "delay", "history"]`。
- `stateLabel("all")` = "当前"，但 `list_tasks(..., state=all)` 只查 ready/processing/retry/dlq/delay，不含 history。
- 侧栏队列卡片显示 completed/failed 计数，但标签页没有独立"已完成"/"已失败"tab。
- "history" 标签混含完成和失败，无法单独查看已完成任务。

**方案**:
- states 增加 `"completed"` 和 `"failed"` 两个独立 tab。
- "all"（当前）保持现有语义：只含活跃生命周期中的任务。
- 后端 `list_tasks` 支持 `QueueState.completed` 和 `QueueState.failed`（通过 history 筛选 status）。

### 问题 4: 缺少发布时间/完成时间，不支持时间筛选

**根因**:
- 表格仅显示一行时间: `formatTime(task.created_at || task.updated_at || task.run_at)`。
- API `/api/queue/{name}/tasks` 无时间范围参数。
- history 记录中有 `created_at` 和 `updated_at`，但未在前端展示。

**方案**:
- 表格增加"发布时间"和"完成时间"两列。
- API 增加 `created_after` / `created_before` / `completed_after` / `completed_before` 查询参数。
- 任务详情抽屉中展示完整时间线。

### 问题 5: "只看当前"语义模糊

**根因**:
- "只看当前" toggle 实际功能是：仅显示有活跃任务或 Worker 的队列。
- 用户不理解"当前"指什么——是当前队列？当前有任务的？

**方案**:
- 改为更清晰的交互：下拉选择或更名。
- 推荐方案：将 toggle chip 改为三态切换——"全部队列" / "有活动" / "仅异常"（有 DLQ/stale worker）。
- 或更简单：改名为"有任务的队列"，Tooltip 说明。

### 问题 6: 任务过期时间

**现状分析**:
- `SmartQueue.push()` 无 `expires_at` 参数。
- `TaskHistory.record()` 记录 `created_at` 和 `status`，但不跟踪过期。
- 没有"已过期"状态。

**方案**:
- `push()` 新增 `expire_seconds` 参数。
- `TaskHistory.record()` 存储 `expires_at` 字段。
- 新增 `QueueState.expired` 状态。
- `QueueAdmin` 新增 `list_expired()` 和 `requeue_expired()` 方法。
- Dashboard: 新增"已过期"tab，显示过期任务并提供"放回"（移回 ready）按钮。

### 问题 7: 综合页面布局优化

**现状**:
- 布局为左侧栏 + 右侧主内容区，右侧又分为左（任务列表）+ 右（诊断/危险/投递）。
- 信息密度适中但可优化：全局总览位置不够突出。

**方案**:
- 全局总览移至顶部（TopBar 下方），更醒目。
- 队列卡片的 mini-stat 增加 completed/failed 行。
- 任务表格支持列排序（按时间）。
- 移动端响应式优化。

---

## 涉及模块

| 模块 | 文件 | 改动类型 |
|------|------|---------|
| 后端 | `qtask_list/admin.py` | `QueueState` 增加 expired/completed/failed；`list_tasks` 支持时间筛选；新增 `list_expired`/`requeue_expired` |
| 后端 | `qtask_list/queue.py` | `push()` 增加 `expire_seconds` 参数 |
| 后端 | `qtask_list/history.py` | `record()` 支持 `expires_at` 字段 |
| 后端 | `dashboard/main.py` | 新增 API: time filter params, expired 相关端点 |
| 前端 | `dashboard/static/js/utils.js` | states 增加 completed/failed/expired；namespace 哨兵值 |
| 前端 | `dashboard/static/js/components.js` | 队列列表、状态标签、表格列、过期面板 |
| 前端 | `dashboard/static/js/api.js` | 新增过期相关 API |
| 前端 | `dashboard/static/js/app.js` | loading 状态、namespace 逻辑、过期 handler |
| 前端 | `dashboard/static/css/app.css` | 新样式 |
| 测试 | `tests/test_dashboard_api.py` | 新增测试 |

## 产出文件

- 设计文档: `docs/design/dashboard-ux-20260622-overview.md`（本文件）
- 计划文档: `docs/dev/PLAN-006-dashboard-ux-v2.md`
- 结果文档: `docs/dev/PLAN-006-dashboard-ux-v2-OUTCOME.md`
