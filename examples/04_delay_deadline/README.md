# 04 Delay & Deadline：延迟执行与业务截止

一条命令演示两种时间语义。

## 运行

```bash
python examples/04_delay_deadline/demo.py
```

## 学习点

| 字段 | 含义 | 到期/超期行为 |
|---|---|---|
| `not_before_at`（兼容 `delay_seconds`） | 最早可消费时刻 | 放 delay ZSET，到点由 `move_delay()` 搬回主队列 |
| `start_deadline_at`（兼容 `expire_seconds`） | 最晚必须**开始**执行的时刻 | pop 时已超期 → 拒绝执行，任务进 `deadline_missed`（`expired` 视图） |

- 截止时间管的是「是否还值得做」，不是「何时做」—— 两者可组合使用
- replay 错过截止的任务必须**显式提供新截止时间**（`admin.requeue_expired(..., start_deadline_at=...)`），防止旧任务瞬间被重新消费又立刻过期
- `clean-history` 清理的是历史记录 TTL，与 deadline 是两回事
