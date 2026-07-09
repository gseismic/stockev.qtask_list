# PLAN-013 结果: 系统 Review 问题修复

## 完成内容

1. 修复过期任务放回重复投递和 payload 丢失：
   - `requeue_expired()` 先查找 live 队列中的真实任务消息；
   - ready 中的过期任务只清理 `expires_at`，不重复 `LPUSH`；
   - retry / dlq / delay 中的过期任务移动真实 raw message；
   - processing 中的过期任务默认不自动抢回，避免影响活跃 Worker；
   - 历史记录缺完整 `payload` 时不再仅凭 `action` 重建任务。
2. 修复纯净安装依赖缺口：
   - `pyproject.toml` 增加运行时依赖 `orjson`；
   - dev 依赖增加 `httpx`，覆盖 FastAPI `TestClient` 测试需要；
   - `requirements.txt` 同步 `orjson` / `zstandard`。
3. 修复旧 String 格式 history 统计报错：
   - `_history_stats()` 复用兼容 Hash/String 的 `_read_history_records()`；
   - 避免对 String key 执行 `HGET` 触发 Redis `WRONGTYPE`。
4. 收窄队列发现边界：
   - Redis 中任意 List 不再默认视为 qtask 队列；
   - 只有历史索引、qtask 状态 key 或消息形态符合 qtask 协议的 List/ZSET 会被发现。
5. 收窄默认恢复边界：
   - `QueueAdmin.recover(include_active=False)` 默认跳过 legacy `{queue}:processing`；
   - CLI 增加 legacy processing 被跳过时的提示；
   - `--force-active` / `include_active=True` 仍可在人工确认后恢复全部 processing。
6. 同步运行时版本：
   - `qtask_list.__version__` 更新为 `0.1.1`。

## 涉及文件

| 文件 | 变更 |
|------|------|
| `qtask_list/admin.py` | 修复过期任务放回、历史统计、队列发现和 recovery 默认边界 |
| `cli/__main__.py` | recovery 输出补充 legacy processing 跳过提示 |
| `pyproject.toml` | 增加 `orjson` 与 dev `httpx` 依赖 |
| `requirements.txt` | 同步运行时依赖 |
| `qtask_list/__init__.py` | 同步 `__version__` |
| `tests/test_dashboard_api.py` | 增加 Dashboard/API 回归测试 |
| `tests/test_cli.py` | 更新 CLI 状态和 recovery 边界测试 |
| `docs/dev/PLAN-013-review-fixes.md` | 本轮计划 |
| `docs/dev/INDEX.md` | 补建历史计划索引并记录本轮 |

## 验证结果

- `python -m pytest -q`：94 passed
- `python -m ruff check .`：通过
- `python -m mypy qtask_list cli dashboard remote_storage`：通过
- 临时 venv 中执行 `pip install -e .` 后 `import qtask_list; qtask_list.__version__`：返回 `0.1.1`
- `git diff --check -- <本轮修改文件>`：通过

## 已知说明

全仓 `git diff --check` 仍会报告 `AGENTS.md` 第 14 行尾随空格。该文件是本轮开始前已有用户修改，本轮未触碰。

## 后续建议

后续可以继续审查 Dashboard 前端对 `moved=0 / updated=1 / note` 的展示，让批量过期任务放回时的“已在 ready，仅清除过期标记”等结果对运维人员更可见。
