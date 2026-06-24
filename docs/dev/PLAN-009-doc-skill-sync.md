# PLAN-009 README 与 qtask-list skill 同步

## 目标

同步 README 和 `skills/qtask-list-usage/SKILL.md`，让用户文档与 Agent 使用指南反映当前代码状态，避免后续 Agent 或用户按过期说明使用 CLI、QueueAdmin 或 RemoteStorage。

## 当前证据

1. `pyproject.toml` 已新增 `storage` optional extra，但 skill 安装说明仍只列出基础、dev、dashboard。
2. CLI 已新增 `qtask storage`，README 和 skill 的 CLI 命令清单尚未完整展示。
3. `QueueAdmin` 已覆盖 CLI 主要管理语义，并新增 `clean_history()`，README 和 skill 示例尚未体现。
4. skill 的项目结构缺少 `remote_storage/` 服务端目录说明。

## 实施方案

1. 更新 README：
   - QueueAdmin 示例补充 `QueueState`、历史读取、清理历史和更完整的控制能力。
   - RemoteStorage 章节补充服务端启动命令。
   - CLI 命令清单补充 `qtask storage` 和全队列 `clean-history`。
2. 更新 `skills/qtask-list-usage/SKILL.md`：
   - 安装说明补充 storage extra。
   - QueueAdmin 示例补充 `clean_history()`。
   - CLI 命令清单补充 `qtask storage`。
   - 项目结构补充 `remote_storage/`。
   - 常见陷阱补充服务端依赖安装提示。

## 验证

- `rg` 检查 README 和 skill 中的关键命令与接口说明。
- `git diff -- README.md skills/qtask-list-usage/SKILL.md docs/dev/PLAN-009-doc-skill-sync.md`
