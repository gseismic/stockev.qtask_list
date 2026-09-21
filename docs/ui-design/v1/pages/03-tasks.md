# 页面设计：任务浏览 `/tasks` + 任务详情抽屉

## 任务浏览 `/tasks`

- 服务场景：S2（跨队列排查"这个 task_id / 这个 action 发生了什么"）· 频率：中
- P1 = 搜索与结果表；P2 = 队列维度筛选；P3 = 导出（开放问题，V1 不做）

与队列详情共用 `<TaskFilterBar>` + `<TaskTable>` 组件，差异：

- 队列维度为下拉多选（默认"全部队列"），而不是固定单队列。
- URL 同步筛选条件（`?q=&state=&queue=`），支持把排查现场粘给同事。
- 搜索 task_id 精确命中时直接打开 `<TaskDrawer>`（跨队列定位任务，复用 `get_task` 的全局查找能力）。

---

## 任务详情抽屉 `<TaskDrawer>`

- 右侧滑出，宽 560px，`Esc`/遮罩点击关闭。服务场景：S2、S3 · 频率：高

### 布局

```
┌─────────────────────────────────────────────┐
│ fetch_quote  8f3ac2…c2        [状态徽章]  ✕ │
│ 队列 stockev:quote:5min · attempt 2/3       │
├─────────────────────────────────────────────┤
│ [时间线]                                     │
│ ● 10:00:05 投递 (logical_key=quote:AAPL:…)  │
│ ● 10:00:07 开始执行 worker=fetch-3           │
│ ● 10:00:12 失败 ConnectionError → 30s 重试  │
│ ● 10:00:42 重试执行 attempt=2                │
│ ○ 下次执行预估 10:30（delay 队列中）          │
├─────────────────────────────────────────────┤
│ Payload                    [复制] [原始JSON] │
│ { "symbol": "AAPL", "slot": "…" }           │
│ Result                                       │
│ （尚无结果）                                 │
│ 血缘: replay_of 91bd…7e ↗                    │
├─────────────────────────────────────────────┤
│ [重放为新任务]  [从 dlq 重入队]  [删除]      │  ← 按状态显隐，危险项红色+确认
└─────────────────────────────────────────────┘
```

### 控制操作（按任务状态显隐）

| 操作 | 可用条件 | API |
|---|---|---|
| 重放为新任务（replay） | 终态 | `POST /api/task/{id}/replay` |
| 从 dlq 重入队 | state=dlq | `POST /api/task/{id}/requeue` |
| 删除 | 非活跃 processing | `DELETE /api/task/{id}` |
| 修改截止时间重放（过期任务） | deadline_missed/expired | replay 接口的 `start_deadline_at` 参数 |

- 所有操作成功后表格原地刷新，抽屉保持打开并显示新状态（避免重复搜索定位）。
- 活跃 processing 任务只读（与"从活跃 Worker processing 重放会被拒绝"的语义一致），显示提示"任务正在执行，如需操作请先恢复/等待完成"。

### 状态

- payload/result 均懒加载（`/api/task/{id}/payload`），大 payload（外存）显示"从 RemoteStorage 拉取完整 payload"按钮。
- 抽屉加载失败：抽屉内错误态 + 重试，不关闭抽屉。
