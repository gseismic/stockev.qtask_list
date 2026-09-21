# 03 Retry & DLQ：重试、退避与死信队列

一条命令演示任务从失败到重放的全部状态转换。

## 运行

```bash
python examples/03_retry_dlq/demo.py
```

## 学习点

- `fail()` 自动分流：`attempt < max_attempts` 进 delay ZSET 指数退避（`retry_backoff_base` 起，带 ±10% 抖动），耗尽后进 DLQ
- `RetryableTaskError.retry_after` 可以覆盖默认退避间隔
- `move_delay()` 把到期的延迟/重试任务原子搬回主队列（Worker 维护线程会自动做）
- **DLQ replay 创建新 task_id**，原终态记录不变 —— 历史审计与重放互不干扰
- 配套命令：`qtask requeue demo:dlq-demo --task-id <id>`（单条重放）、`qtask recover`（crash recovery）
