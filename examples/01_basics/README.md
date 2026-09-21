# 01 Basics：最简生产/消费

不引入 Worker，直接用 `SmartQueue` 的底层 API 理解队列的可靠性模型。

## 文件

- `producer.py`：用 `enqueue(TaskSpec)` 投递一个任务
- `consumer.py`：`pop(timeout)` 阻塞消费 + `ack()` 确认

## 运行

```bash
# 终端 1：先启动消费者（阻塞等待）
python examples/01_basics/consumer.py

# 终端 2：投递任务
python examples/01_basics/producer.py
```

## 学习点

- `namespace + queue_name` 拼出 Redis 逻辑队列（如 `demo:hello`）
- `pop()` 基于 BRPOPLPUSH：任务先移入 processing，`ack()` 后才删除 —— 消费者崩溃任务不丢
- `raw_message` 是 ack/fail 的凭据；底层 API 需要自己管理它，这正是 `Worker`（见 02）替你封装的部分
