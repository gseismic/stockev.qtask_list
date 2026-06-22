# PLAN-006: Dashboard UX 综合优化 v2

基于设计文档 `docs/design/dashboard-ux-20260622-overview.md` 的实施计划。

---

## 改动顺序

后端先行（API 支撑前端），前端随后。

### 阶段 1: 后端改造

#### S1.1: QueueState 扩展 (admin.py)

```python
class QueueState(str, Enum):
    ready = "ready"
    processing = "processing"
    retry = "retry"
    dlq = "dlq"
    delay = "delay"
    history = "history"
    completed = "completed"   # 新增
    failed = "failed"         # 新增
    expired = "expired"       # 新增
    all = "all"
```

#### S1.2: 任务过期支持 (queue.py + history.py)

**queue.py `push()` 增加 `expire_seconds` 参数**:
```python
def push(self, payload, delay_seconds=0, expire_seconds=0):
    # ...
    history_data = {"action": original_action, "status": "pending"}
    if expire_seconds > 0:
        history_data["expires_at"] = time.time() + expire_seconds
    self.history.record(task_id, history_data)
```

**history.py `record()` 透传 expires_at**:
- `serialize_value` 已支持所有类型，expires_at (float) 可直接写入。

#### S1.3: QueueAdmin 过期任务方法 (admin.py)

新增方法:

```python
def list_expired(self, queue_name: str, limit: int = 50) -> List[Dict]:
    """列出已过期但未完成的任务（status=pending 且 expires_at < now）"""
    hist_key = f"qtask:hist:{queue_name}"
    task_ids = self.r.zrevrange(hist_key, 0, min(limit * 3, 1000) - 1)
    expired = []
    now = time.time()
    for tid in task_ids:
        data = self.get_task(tid)
        if not data: continue
        expires_at = data.get("expires_at")
        if expires_at and float(expires_at) < now and data.get("status") != "completed":
            data["_queue"] = queue_name
            data["_state"] = "expired"
            data["_source"] = hist_key
            expired.append(data)
            if len(expired) >= limit: break
    return expired

def requeue_expired(self, queue_name: str, task_id: Optional[str] = None) -> Dict:
    """将过期任务移回 ready 队列（类似 requeue_dlq）"""
    # 从 Redis 队列中查找并移回
    ...
```

#### S1.4: 时间范围筛选 (admin.py)

`list_tasks()` 增加参数:
```python
def list_tasks(self, queue_name, state=..., limit=50, search=None,
               created_after=None, created_before=None,
               completed_after=None, completed_before=None):
```

`completed`/`failed` 状态查询:
- 从 `qtask:hist:{name}` ZSET 中获取 task_id，再 `hgetall` 过滤 status。

#### S1.5: Dashboard API 扩展 (main.py)

新增/修改端点:

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/queue/{name}/tasks` | GET | 增加 `created_after`, `created_before`, `completed_after`, `completed_before` 参数 |
| `/api/queue/{name}/expired` | GET | 获取过期任务列表 |
| `/api/queue/{name}/requeue-expired` | POST | 过期任务放回 ready |

#### S1.6: history_stats 性能优化 (admin.py)

将 `_history_stats` 中逐个 `hget` 改为 Pipeline 批量获取：
```python
def _history_stats(self, queue_name, sample_limit=2000):
    # 已使用 Pipeline，保持不变
    # 额外优化：对超大 ZSET（>10000 条）做随机采样而非取最新
```

---

### 阶段 2: 前端改造

#### S2.1: Loading 状态 (app.js)

- 新增 `loading` state，初始 `true`。
- `refresh()` 成功后设 `false`。
- `SystemBanner` 扩展：loading 时显示骨架屏或 "加载中..."。

#### S2.2: 命名空间修复 (app.js + components.js + utils.js)

**utils.js**:
```js
// namespaceFilter 使用 null 表示"全部"
// extractNamespace 对无命名空间返回 ""
```

**app.js**:
```js
const [namespaceFilter, setNamespaceFilter] = useState(null);  // null = 全部
```

**components.js QueueList**:
- "全部" chip: `onClick={() => onNamespaceFilter(null)}`
- namespace 过滤: `namespaceFilter === null ? source : source.filter(q => extractNamespace(q.name) === namespaceFilter)`
- 无命名空间 chip: `onClick={() => onNamespaceFilter("")}`, 单独的 label "无命名空间"

#### S2.3: 状态标签页扩展 (utils.js + components.js)

**utils.js**:
```js
export const states = ["all", "ready", "processing", "retry", "dlq", "delay", "completed", "failed", "expired"];

// stateLabel 新增
completed: "已完成",
failed: "已失败",
expired: "已过期",

// stateCount 新增
if (state === "completed") return Number(stats.completed || 0);
if (state === "failed") return Number(stats.failed || 0);
if (state === "expired") return Number(stats.expired || 0);
```

#### S2.4: 时间列 + 时间筛选 (components.js + app.js + api.js)

**表格列扩展**:
```jsx
// 表头增加
h("th", {}, "发布时间"),
h("th", {}, "完成时间"),

// 行数据
h("td", { className: "muted" }, formatTime(task.created_at)),
h("td", { className: "muted" }, task.status === "completed" ? formatTime(task.updated_at) : "-"),
```

**时间筛选 UI**:
- 在 TaskToolbar 中增加两个 date input：`created_after`, `created_before`。
- 或简化为：时间范围快速选择（最近 1h / 24h / 7d / 30d）。

#### S2.5: "只看当前" 重命名 (components.js)

将 chip label 从 "只看当前" 改为 "有活动"。
Tooltip: "仅显示有待处理任务或活跃 Worker 的队列"。

#### S2.6: 过期任务面板 (components.js + app.js)

**ExpiredTaskList 组件**:
- 显示过期任务表格（同 TaskTable 结构）。
- 每行增加 "放回" 按钮。
- 批量放回按钮。

#### S2.7: 布局优化 (app.js + components.js + css)

- 全局总览移至 TopBar 下方（full width），用 border-bottom 分隔。
- 队列卡片 mini-stat 增加已完成/已失败行。
- StatsGrid 增加 "已过期" 卡片（红色 warning 色调）。

---

### 阶段 3: 验证

#### 后端测试 (tests/test_dashboard_api.py)
- 测试 `expired` 状态查询
- 测试时间范围筛选
- 测试 `requeue_expired`

#### 前端手动验证
- 启动 `qtask dashboard`，验证各功能
- 确认 loading 骨架屏出现
- 确认命名空间筛选正确
- 确认时间显示和筛选

---

## 涉及文件清单

| 文件 | 改动 |
|------|------|
| `qtask_list/admin.py` | +QueueState values, +list_expired, +requeue_expired, time filter, performance |
| `qtask_list/queue.py` | push() +expire_seconds |
| `qtask_list/history.py` | record() 透传 expires_at |
| `dashboard/main.py` | +expired API, time filter params |
| `dashboard/static/js/utils.js` | states, stateLabel, stateCount, namespace |
| `dashboard/static/js/components.js` | QueueList ns fix, StateTabs, TaskTable cols, ExpiredPanel, layout |
| `dashboard/static/js/api.js` | +expired APIs, time params |
| `dashboard/static/js/app.js` | loading, ns fix, expired handlers, layout |
| `dashboard/static/css/app.css` | loading styles, expired styles, layout tweaks |
| `tests/test_dashboard_api.py` | new test cases |

---

## 风险与取舍

1. **过期任务扫描性能**: `list_expired` 需要遍历 history ZSET 并检查 `expires_at`。对大量历史记录（>10000）可能慢。**缓解**: 限制扫描上限（1000），需要时用户可搜索特定 task_id。
2. **过期任务"放回"机制**: 过期任务可能不在任何 List/ZSET 队列中（仅存于 history Hash）。放回时需构造完整消息重新 push。**缓解**: 从 history 中读取原始 payload，调用 `push_task` 重新入队。
3. **向后兼容**: `expires_at` 为可选字段，旧任务没有该字段时视为永不过期。
