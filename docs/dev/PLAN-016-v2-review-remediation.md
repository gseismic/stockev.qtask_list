# PLAN-016：V2 评审问题修复与交付闭环

- 创建时间：2026-09-20（Asia/Shanghai）
- 依据：`docs/dev/PLAN-015-qtask-v2.md`、`docs/design/qtask-overall-20260920-v2.md`、本轮 review 结果
- 目标：修复已确认的 V2 兼容性、状态统计、API 错误语义和 Worker 停止问题，并让测试、静态检查、文档和示例与实际实现一致
- 工作树保护：`AGENTS.md` 是用户已有未提交改动，不修改、不提交

## 1. 修复范围

1. 为旧 V1 任务补齐双读与管理操作降级路径：旧 DLQ 记录无 V2 action 时仍可安全 requeue；payload resolve 保留可恢复的 action 和兼容提示。
2. 统一 `retry_wait` 统计：由 `QueueAdmin` 使用注入的 Redis 客户端计算，Dashboard、CLI、SDK 和前端使用同一字段。
3. 将 REST 入口中的用户可恢复输入错误转换为明确的 4xx 响应，避免把 `ValueError`/`TypeError` 泄露成 500。
4. 修复 Worker 优雅 drain 时停止信号被清除的问题，确保 `TaskContext.stop_requested` 在停止期间保持可观察。
5. 按 V2 状态机和历史/身份语义迁移旧测试，补充兼容性和入口回归覆盖；修复手动验证脚本的 V2 假设及 lint 问题。
6. 同步 README、仓库内 skill、stockev 示例，并生成 PLAN-015/016 结果、索引和交接记录。

## 2. 验收标准

- `python -m pytest tests/ -q` 全绿；旧 V1 消息兼容用例和 V2 新语义均有直接测试。
- `python -m ruff check .` 全绿。
- `python -m mypy qtask_list cli dashboard remote_storage` 全绿。
- `node --check dashboard/static/js/*.js` 全绿。
- `retry_wait` 在不同 Redis DB/注入客户端场景下统计正确，前端 live count、tab、总览和告警一致。
- 无 action、非法 datetime、无效 replay 等用户输入返回 4xx，系统异常仍保留 5xx。
- Worker drain 期间 handler 能持续观察停止请求，且线程池和 maintenance 线程最终退出。
- 文档、示例、skill 与 0.2.0 实际签名和状态语义一致；结果文件和索引已登记。

## 3. 实施顺序

1. 先修核心 Python/API 和前端统计，建立最小回归测试。
2. 再迁移全量测试与手动脚本，逐轮运行测试和静态检查。
3. 最后同步文档/示例和交接记录，review diff，提交并 push。
