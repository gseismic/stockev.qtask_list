# 交接文档（HANDOFF）

- 更新时间：2026-09-20 22:08（Asia/Shanghai）
- 当前基线：本轮 PLAN-016 提交并推送后的 `main`（提交号以 git log 为准）
- 交接范围：qtask_list V2 核心、管理后台、前端、测试、文档和 stockev 示例
- 工作树保护：`AGENTS.md` 是用户已有未提交修改，始终不修改、不暂存、不提交

## 1. 项目背景

qtask_list 是只依赖 Redis 的分布式任务队列库，面向股票数据采集的批量回补、7×24
定时抓取和动态新闻列表。库负责可靠执行、状态转换和重试；调度策略、幂等落库和业务
持久化由使用方负责。

V2（版本 `0.2.0`）将业务 payload 与运行元数据分离：`TaskSpec` 描述 action、payload、
身份键、调度和截止时间；V2 信封支持 inline/zstd/external；Redis Lua 状态机维护
attempt、deadline、outcome、location、replay 血缘和 live identity。V1 信封、旧 retry
List、`expires_at`/`_retry` 仍可双读或安全降级。

## 2. 当前已完成

本轮 `PLAN-016` 已完成上一交接中所有 Python 侧收尾：

1. 旧 V1 DLQ/历史记录可以解析并在缺少 V2 payload descriptor 时安全 fallback；V2 replay
   始终创建新 task_id，原终态不可重新打开。
2. `retry_wait` 统计由注入的 Redis 客户端计算，Dashboard、CLI、SDK 和前端使用同一
   字段；V2 重试存于 delay ZSET，旧 retry List 仅用于兼容迁移。
3. Dashboard 用户输入错误返回 4xx；Worker graceful drain 不再清掉绑定到
   `TaskContext.stop_requested` 的停止事件。
4. 测试已迁移为 V2 语义，手工验证脚本可执行并清理测试数据；README、仓库 skill 和
   examples 已同步。
5. examples 新增：
   - `examples/stockev/scheduler.py`：确定性 5 分钟 slot、早晚 universe、历史分区；
   - `examples/stockev/news_discover.py`：`TaskResult.emissions` 新闻 fan-out；
   - `examples/stockev/reconciler.py`：JSONL 期望清单的幂等补齐；
   - 原 pipeline producer/worker 已改用 `TaskSpec`/`TaskResult`。

关键文档：

- `docs/design/qtask-overall-20260920-v2.md`：V2 设计定稿；
- `docs/dev/PLAN-015-qtask-v2.md` 与 `PLAN-015-qtask-v2-OUTCOME.md`：V2 实施和整体结果；
- `docs/dev/PLAN-016-v2-review-remediation.md` 与对应 `-OUTCOME.md`：本轮修复证据；
- `docs/dev/INDEX.md`：计划-结果历史索引；
- `README.md`、`skills/qtask-list-usage/SKILL.md`：当前 API 和运维语义。

## 3. 验证基线

在提交前应确认以下命令均通过；结果文件记录实际输出：

```bash
python -m pytest tests/ -q
python -m ruff check .
python -m mypy qtask_list cli dashboard remote_storage
for f in dashboard/static/js/*.js; do node --check "$f"; done
python -m compileall -q examples
```

Redis 默认连接 `redis://localhost:6379/0`。手工脚本为
`tests/manual/verify_review_claims.py`，运行后必须检查测试 namespace 已清理。

## 4. 后续可选方向

- 在生产 Redis 上压测 `QueueAdmin.queue_names()` 的 SCAN 发现路径；
- 将 RemoteStorage orphan cleanup、DLQ 告警回调接入实际部署；
- 根据真实股票源完善 scheduler 的交易日历、分区和新闻 URL 规范化；
- 继续为 consistency report、lease 和 archive leader 增加故障注入测试。

这些方向不阻塞本轮目标。任何新的实施都应新增计划文件、结果文件并追加到
`docs/dev/INDEX.md`；提交前仍需排除 `AGENTS.md`。
