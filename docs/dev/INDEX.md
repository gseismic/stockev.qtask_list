# 开发计划执行索引

本文件此前缺失；根据项目约定从 PLAN-013 起开始维护。更早计划和结果文件仍保留在 `docs/dev/` 目录，可通过文件名追溯。

## 2026-07-09 08:17

- 计划文件：`PLAN-013-review-fixes.md`
- 结果文件：`PLAN-013-review-fixes-OUTCOME.md`
- 摘要：修复系统 review 发现的过期任务重复投递、依赖声明缺失、旧历史格式统计报错、队列发现过宽、legacy recovery 默认边界和运行时版本号不一致问题。

## 2026-07-09 08:21

- 计划文件：`PLAN-013-review-fixes.md`
- 结果文件：`PLAN-013-review-fixes-OUTCOME.md`
- 摘要：已完成代码、测试和依赖元数据修复；全量测试 94 passed，ruff/mypy 通过，临时 venv 最小安装导入通过。本轮未修改用户已有的 `AGENTS.md` 工作树变更。

## 2026-09-20 14:05

- 计划文件：`PLAN-014-review-fixes.md`
- 结果文件：`PLAN-014-review-fixes-OUTCOME.md`
- 摘要：落实三轮评审（qtask-design-review-20260920-scenarios / review-20260920-design-flaws / REVIEW-20260920-stock-data-ingestion）合并修复：logical_key 业务身份去重、重试指数退避、expire 执行截止强制（skipped 状态）、zstd 线程局部化、外存错误分类、归档仅 terminal、维护线程周期 stale recovery、DLQ 重放重置重试计数、push_batch 补参数、stop 卡死修复；另修复实施中发现的 maintenance 初始化竞态等 3 个新问题。测试 114 passed（新增 22），ruff/mypy 通过，端到端三场景验证通过。配套设计定稿 `docs/design/task-identity-20260920-logical-key.md`。本轮未提交用户已有的 `AGENTS.md` 工作树变更。
