# 05 Idempotency：任务身份与去重

一条命令演示 `logical_key` 的去重语义。

## 运行

```bash
python examples/05_idempotency/demo.py
```

## 学习点

- **身份构造**：用确定性字段（业务类型 + 主体 + 时间桶），不用进程启动时刻 —— 重复运行/补跑天然幂等（参考 `06_pipeline` 的 scheduler）
- 两段去重窗口：live 期间 `duplicate_active`；终态后到 `dedup_until` 之前 `duplicate_retained`
- `EnqueueResult.reason` 是调用方处理重复的唯一依据，`enqueue_many` 不抛异常
- `DuplicateAction.ALLOW_NEW`：显式放行同键新任务（保留期重跑）
- `dedup_until` 必须搭配 `logical_key` 使用；`scheduled_for` 可与身份分离（身份=计划时刻，实际消费受 `not_before_at` 约束）
