# PLAN-005: Dashboard UX 优化

## 背景

用户反馈 dashboard 存在以下问题：

1. 任务详情直接暴露内部元数据（`_queue`、`_state`、`_source`），信息不人性化
2. 用户最关心的「完成了多少」「失败了」「发布了多少」等统计信息缺失
3. 「历史」标签含义模糊，用户不清楚是完成还是失败记录
4. 没有删除不再使用的空队列的入口
5. 缺少跨队列的全局总览，用户需要逐个队列查看才能拼凑出整体状态

## 目标

从用户场景出发，让 dashboard 回答运维人员的核心问题：
- 我投递了多少任务？
- 完成了多少？失败了多少？
- 还有多少在处理中 / 排队中？
- 有哪些死信需要关注？

同时提供更友好的任务详情展示和队列管理能力。

---

## 实施步骤

### P0: 增加完成/失败统计（核心需求）

#### 后端改动

**文件**: `qtask_list/admin.py`

1. `queue_stats()` 增加历史状态统计
   - 遍历 `qtask:hist:{queue_name}` ZSET → 逐个 `hget status` → 聚合 completed / failed 计数
   - 考虑到性能，对 ZSET 做采样（取最新 N 条）而非全量扫描
   - 返回新增字段：`"completed"`、`"failed"`
   - history 字段保留为总数（= completed + failed + pending/retry）

2. 可选：新增独立 API `GET /api/queue/{name}/summary` 返回更详细的统计

**文件**: `dashboard/main.py`
- `api_queues()` 和 `api_queue(name)` 自动透传新增字段，无需改动

#### 前端改动

**文件**: `dashboard/static/js/components.js`

1. `StatsGrid` — 当前 7 个卡片，调整为 9 个：
   ```
   待处理 | 处理中 | 待重试 | 死信 | 延迟 | Worker | 已完成 | 已失败 | 历史
   ```
   - 「已完成」绿色底色
   - 「已失败」红色底色
   - 「历史」变为较小 / muted（作为参考）

**文件**: `dashboard/static/js/utils.js`

2. 队列排序权重更新：`queuePriority()` 中加入 failed 权重，让有失败任务的队列排在前面

**文件**: `dashboard/static/js/app.js`

3. 默认 fallback stats 增加 completed / failed 字段

---

### P1: 任务详情去内部字段

**文件**: `dashboard/static/js/components.js`

`TaskDrawer` 组件改造：

1. 将当前单一的 "Raw" JSON 展示拆为两部分：
   - **任务数据**（默认展开）：task_id, action, status, created_at, updated_at, payload
   - **内部元数据**（折叠 / details）：_queue, _state, _source, _raw, decode_error, retry

2. 实现方式：对 `prettyJson(task)` 做字段过滤
   - 用户可见字段白名单：`task_id`, `action`, `status`, `payload`, `created_at`, `updated_at`, `run_at`
   - 其余字段归入内部元数据区

---

### P1: 「历史」标签明确化

**文件**: `dashboard/static/js/utils.js`

1. `stateLabel("history")` 改为更明确的标签（考虑两种方案）：
   - 方案 A：保持 "历史"，添加 tooltip "含已完成、已失败的历史记录"
   - 方案 B：拆分为两个独立 state tab
   - **推荐方案 A**（改动最小，不增加 UI 复杂度）

2. `StatsGrid` 中 history 卡片添加 title/hint 说明

---

### P2: 删除队列功能

#### 后端改动

**文件**: `qtask_list/admin.py`

1. 新增 `delete_queue(queue_name)` 方法：
   - 扫描所有相关 key：主队列 + `:processing:*` + `:retry` + `:dlq` + `:delay` + `:worker:*` + `qtask:hist:{name}` + 关联的 `qtask:task:*`
   - 使用 Pipeline 批量删除
   - 返回删除的 key 数量

**文件**: `dashboard/main.py`

2. 新增 API：
   ```python
   @app.delete("/api/queue/{name}")
   def api_delete_queue(name: str, _auth = Depends(require_auth)):
       return admin.delete_queue(name)
   ```

#### 前端改动

**文件**: `dashboard/static/js/api.js`
- 新增 `api.deleteQueue(queue)`

**文件**: `dashboard/static/js/components.js`

3. `QueueList` 侧栏改造：
   - 每个空队列（liveCount === 0 && history === 0）的条目右侧增加删除图标/按钮
   - 点击弹出二次确认：`确认删除队列 {name}？此操作不可撤销。`
   - 有活跃任务的队列不显示删除按钮

**文件**: `dashboard/static/js/app.js`
- 新增 `deleteQueue` action handler

---

### P2: 全局总览卡片

**文件**: `dashboard/static/js/components.js`

1. 新增 `GlobalOverview` 组件：
   - 跨所有队列聚合：ready + processing + retry + dlq + delay + completed + failed
   - 显示为紧凑的横条统计栏，位于 StatsGrid 上方
   - 样式：一条水平汇总条，类似：
     ```
     📊 全部队列: 1,234 待处理 | 56 处理中 | 12 重试 | 3 死信 | 8,901 已完成 | 45 已失败
     ```

2. 数据来源：遍历 `queues` 数组聚合 stats 字段

**文件**: `dashboard/static/js/app.js`
- 在 `effectiveQueue` 存在时，`StatsGrid` 上方插入 `GlobalOverview`
- 使用 `useMemo` 聚合 queues 的所有 stats

---

### 验证

1. 后端测试：`tests/test_dashboard_api.py`
   - 验证 `queue_stats` 返回 completed / failed 字段
   - 验证 `delete_queue` API 正常删除

2. 前端验证
   - StatsGrid 新增卡片显示正确数字
   - TaskDrawer 不显示内部字段（或折叠）
   - 删除空队列后侧栏消失
   - 全局总览数字正确聚合

---

## 涉及文件

| 文件 | 改动类型 |
|------|---------|
| `qtask_list/admin.py` | queue_stats 增强、新增 delete_queue |
| `dashboard/main.py` | 新增 DELETE /api/queue/{name} |
| `dashboard/static/js/app.js` | stats fallback、deleteQueue handler、GlobalOverview |
| `dashboard/static/js/components.js` | StatsGrid、TaskDrawer、QueueList、GlobalOverview |
| `dashboard/static/js/utils.js` | queuePriority 权重、stateLabel |
| `dashboard/static/js/api.js` | 新增 deleteQueue API |
| `dashboard/static/css/app.css` | 新增卡片样式、总览样式 |
| `tests/test_dashboard_api.py` | 新增测试用例 |
