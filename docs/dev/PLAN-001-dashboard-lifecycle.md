# PLAN-001 Dashboard 生命周期控制台

## 目标

从 `stockev.spiders` 的真实使用场景出发，优化 qtask_list 的管理接口与 Dashboard，让用户可以方便查看和控制队列任务，包括手动重试、恢复、删除和诊断。

## 实施步骤

1. 增加共享管理接口 `qtask_list.admin.QueueAdmin`。
2. 重写 Dashboard 后端 API，使其调用 `QueueAdmin`。
3. 将 Dashboard 前端替换为 React 模块化实现。
4. 更新 README 中 Dashboard 与管理接口说明。
5. 增加 Dashboard API 测试，覆盖主要状态迁移。
6. 运行全量质量检查。

## 设计约束

- 默认操作必须安全，恢复 processing 默认只恢复 stale worker。
- 危险操作必须显式传参或由前端二次确认。
- 保持 qtask_list 轻量，不增加前端构建链。
- 不改 `stockev.spiders` 源码，仅作为场景输入。

## 验证命令

```bash
python -m pytest -q
python -m ruff check .
python -m mypy qtask_list cli dashboard
```
