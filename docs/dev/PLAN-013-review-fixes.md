# PLAN-013: 系统 Review 问题修复

## 背景

本轮系统 review 发现 6 个问题：

1. `requeue_expired()` 会重复投递仍在 live 队列中的过期任务，且重建消息时可能丢失 payload 字段。
2. `orjson` 是运行时必需依赖，但未写入 `pyproject.toml` / `requirements.txt`，纯净安装后无法 `import qtask_list`。
3. `_history_stats()` 只使用 `HGET` 读取状态，遇到旧 String 格式历史记录会触发 Redis `WRONGTYPE`。
4. `queue_names()` 会把 Redis 中任意 List 当成 qtask 队列，Dashboard/CLI 可能误操作非 qtask 数据。
5. 默认 `recover()` 会恢复 legacy `{queue}:processing`，对直接使用 `SmartQueue.pop()` 的消费者不是安全恢复。
6. `qtask_list.__version__` 与包版本不一致。

## 目标

- 修复会造成重复处理、纯净安装失败或统计接口报错的问题。
- 收窄队列发现和恢复操作的默认风险边界。
- 保持现有 CLI/Dashboard 的主要用户接口兼容，必要时通过返回字段提供更准确的动作说明。
- 增加回归测试覆盖 review 复现路径。

## 方案

1. 过期任务放回：
   - `requeue_expired()` 先查找任务是否仍存在于 ready/processing/retry/dlq/delay。
   - 若已在 ready，只清理 `expires_at` 并标记 pending，不重复 `LPUSH`。
   - 若在 retry/dlq/delay/processing，复用现有 `requeue_task()` 移动真实 raw message。
   - 若不在 live 队列，仅在历史记录中有完整 payload 时才重建消息；否则返回 `moved=0` 和 note。
2. 历史统计：
   - `_history_stats()` 复用兼容 Hash/String 的历史读取 helper，避免 `WRONGTYPE`。
3. 队列发现：
   - 默认只从 qtask 历史索引和 qtask 状态 key 发现队列。
   - 对裸 list key，只有消息形态像 qtask 消息时才加入队列。
4. 恢复边界：
   - `QueueAdmin.recover(..., include_active=False)` 默认不恢复 legacy processing。
   - `include_active=True` 时恢复所有 processing，包括 legacy。
5. 发布元数据：
   - 增加 `orjson`、测试所需 `httpx` 依赖声明。
   - 同步 `__version__` 为 `0.1.1`。
6. 文档：
   - 新建 `docs/dev/INDEX.md`，说明历史索引此前缺失并追加本轮计划。
   - 完成后生成 outcome。

## 验证

- `python -m pytest -q`
- `python -m ruff check .`
- `python -m mypy qtask_list cli dashboard remote_storage`
- 临时 venv 最小安装后 `import qtask_list`
