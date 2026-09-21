# 02 Worker：handler 注册与错误分类

单进程使用 `Worker` 消费任务，演示三种错误路径。

## 文件

- `worker.py`：注册 3 个 handler —— 正常 / 抖动（可重试）/ 永久失败
- `producer.py`：投递 4 种任务（含未知 action）

## 运行

```bash
# 终端 1
python examples/02_worker/worker.py

# 终端 2
python examples/02_worker/producer.py
```

## 学习点

- `@worker.on(action)` 按 action 路由；handler 返回值写入历史 result
- `RetryableTaskError`：指数退避重试（`retry_backoff_base` 起步），耗尽 `max_attempts` 后进 DLQ
- `PermanentTaskError`：跳过剩余重试直接进 DLQ（参数错误等无意义重试的场景）
- 未分类异常与未知 action：分别按 `handler_unclassified` / `unknown_action` 处理
- 观察工具：`qtask watch demo:jobs`、`qtask peek demo:jobs --state dlq`
