# PLAN-009 README 与 qtask-list skill 同步结果

## 实施结果

1. 更新 README：
   - 安装说明补充 `pip install -e ".[storage]"`。
   - QueueAdmin 示例补充 `QueueState.history`、`push_task()`、`clear_queue()`、`clean_history()`。
   - RemoteStorage 章节补充 `qtask storage` 服务端启动命令和 storage extra 安装方式。
   - CLI 命令清单补充全队列 `clean-history` 和 `qtask storage`。
2. 更新 `skills/qtask-list-usage/SKILL.md`：
   - 安装说明补充 storage extra 和 `python-multipart` 依赖来源。
   - QueueAdmin 示例补充 history 读取和 `clean_history()`。
   - CLI 命令清单补充全队列历史清理和 `qtask storage`。
   - 项目结构补充 `remote_storage/` 服务端目录。
   - 常见陷阱补充 RemoteStorage 服务端需要安装 `qtask_list[storage]` 并启动 `qtask storage`。

## 验证结果

- `rg` 检查 README / skill 中的 `storage`、`qtask storage`、`clean_history`、`QueueState.history`、`remote_storage/`：通过。
- `pytest -q`：74 passed。
- `ruff check .`：All checks passed。
- `mypy qtask_list cli dashboard remote_storage`：Success。

## 后续建议

1. 下一步可以从 `QueueAdmin.diagnose()` 入手增强诊断语义，让 CLI / Dashboard 不只展示状态，还能更明确解释积压、失败和 stale worker 的处理建议。
2. 累计变更已经较多，适合在继续大改前做一次提交和 push。
