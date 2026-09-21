# 09 Admin Ops：QueueAdmin 运维与诊断

面向 Dashboard / CLI / 运维脚本的统一管理 API。

## 运行

先制造一些数据（含 DLQ 任务）：

```bash
# 终端 1
python examples/02_worker/worker.py

# 终端 2
python examples/02_worker/producer.py
# 等 bad_job / no_such_handler 处理完（进 DLQ）

# 终端 2 继续
python examples/09_admin_ops/demo.py
```

## 学习点

- `list_queues()`：全量队列发现，含各子队列深度与 Worker 统计
- `diagnose(queue)`：stats + 运维建议（如 DLQ 积压、stale processing）
- `list_tasks(state=QueueState.*)`：按 `ready`/`dlq`/`completed`/`failed`/`expired`/`history`/`all` 等视图浏览，支持 `search`、时间过滤
- 单任务操作：`get_task` / `requeue_task`（新 task_id，原终态保留）/ `delete_task`
- `recover()` 默认**安全**：只恢复 heartbeat 过期的失联 Worker；`include_active=True` 才强制恢复
- 这些能力在 CLI（`qtask status/peek/requeue/recover`）与 Dashboard 中都有对应入口
