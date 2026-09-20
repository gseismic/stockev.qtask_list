# 任务身份（logical_key）与执行截止（deadline）设计

- 日期：2026-09-20
- 状态：定稿（源自三轮设计评审 + 用户讨论）
- 关联：`docs/design/qtask-design-review-20260920-scenarios.md`、`docs/dev/PLAN-014-review-fixes.md`

## 1. 背景问题

三个使用场景（批量下载、定时逐只抓取、动态增长的抓取列表）暴露出两个身份层面的缺口：

1. **重复投递无防线**：`push` 每次生成新 `uuid4`，同一业务对象重复推送 = 重复抓取、重复落库。
2. **过期任务照样执行**：`expire_seconds` 只写入历史视图，pop 不检查，陈旧行情会被抓取并存库。

## 2. 身份模型

```
logical_key = 任务类型 : 业务主体 : 数据时间桶
```

- **显式声明**：调用方传 `logical_key` 参数，库不做 payload 自动哈希。哪些字段构成"同一性"是业务判断（URL 带时间戳/query 参数会让自动哈希失效）。
- **时间桶编进 key 字符串**：跨期天然是新键。TTL 只做垃圾清理（建议桶大小的 2~3 倍），不承担窗口语义。
- **不进键的 TTL 方案被否决**：窗口边界由 Redis 过期时刻决定，不可预测、不可测试；且 DLQ 死任务会占住键阻塞后续轮次。

### 各场景键模板

| 任务类型 | 时间桶粒度 | logical_key 示例 | 语义 |
|---|---|---|---|
| 日 K 线 | 交易日 | `kline:AAPL:1d:2026-09-19` | 当日重复推送去重；回补晚采合法 |
| 5min 行情快照 | 采样周期 | `quote:AAPL:20260920T1310` | 同桶去重，下桶自动新任务 |
| 股票列表发现 | 固定窗口（如 4h） | `universe:20260920-am` | 早晚列表是不同任务，IPO 由下桶发现 |
| 新闻抓取 | 条目即身份 | `news:sha256(url)` | 一篇文章永远是同一任务 |

### 去重语义

- 实现：`SET {ns}:{queue}:dedup:{logical_key} {task_id} NX EX {dedup_ttl}`，命中则跳过投递，`push` 返回 `None`。
- `dedup_ttl` 默认：有 `expire_seconds` 时取其 2 倍，否则 86400 秒。
- `force=True` 显式绕过（强制重抓场景），危险操作必须显式。
- 键值存 `task_id` 便于 Dashboard/排查。不设 ack 释放逻辑：TTL 是唯一清理途径，DLQ 死任务只占住自己的桶，下个桶自动自愈。
- 同一 `logical_key` 建议同时作为数据库唯一约束（如 kline 表 symbol+period+trade_date），队列挡投递、唯一键挡落库，两层一个身份。

## 3. 执行截止（deadline）

身份键回答"是不是同一个任务"，deadline 回答"现在执行还有没有价值"——两个正交参数，三个生命周期不得混淆：

| 生命周期 | 参数 | 职责 |
|---|---|---|
| dedup 键 TTL | `dedup_ttl` | 垃圾清理 |
| 执行截止 | `expire_seconds` → 信封 `expires_at` | pop 时强制检查 |
| 历史保留 | `ttl_days` | 记录留存 |

- `expires_at` 写入消息信封（`{"task_id", "payload", "expires_at"}`），pop 检查零额外 RTT；旧格式消息无此字段则不强制（向后兼容）。
- 过期任务在 pop 时**直接丢弃**（LREM processing + 历史标记 `skipped`），**不进 DLQ**——DLQ 语义是"失败需人工决定重放"，过期任务是"执行无意义"，重放也无意义。
- 各数据类型的 deadline 策略（业务侧配置，随 logical_key 模板一起维护）：

| 数据类型 | deadline |
|---|---|
| 日 K 线 | 无或很长（如 7 天），回补合法 |
| 5min 快照 | 桶结束 + 容忍度（如 +2min） |
| 新闻 | 无或数天 |
| 列表发现 | 下个桶开始前 |

## 4. 重试退避（同一改动包内）

`fail()` 不再立即回主队列：按 `_retry` 次数写入 delay ZSET，`delay = min(base * 2^(retry-1), max)` 加 ±10% 抖动，到点由现有 `move_delay` 迁回主队列。`retry_backoff_base=0` 保留旧的立即重试行为（兼容路径）。带 backoff 的重试任务在 delay 视图中可见（payload 携带 `_retry`），历史状态仍为 `retry`。

## 5. 明确不做

- 库内不做 cron/交易日历调度：外置 cron + `logical_key` 去重覆盖场景 2（详见 README 部署模式）。
- dedup 键不做 ack 释放、不做跨队列语义。
- 任务信封元数据（`_retry`/`_large`/`_compressed`/`expires_at`）与业务 payload 的彻底分离（信封化重构）留待后续版本。
