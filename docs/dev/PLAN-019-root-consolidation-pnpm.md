# PLAN-019 根目录收敛 + 前端切换 pnpm

- 日期：2026-09-21
- 状态：已完成
- 前置：PLAN-017（React 前端）、PLAN-018（SPA 鉴权）

## 目标

1. 根目录收敛：`cli/`、`dashboard/`、`remote_storage/`、`frontend/` 全部并入
   `qtask_list/` 命名空间，根目录从 15 项降到 9 项。
2. 删除冗余 `requirements.txt`（与 pyproject dependencies 完全重复）。
3. 前端工具链从 npm 切换为 pnpm（用户明确要求全链路 pnpm）。
4. `skills/` 保持原位不动（用户明确要求）。

## 目标结构

```
qtask_list/
├── cli/             # Typer CLI，入口改为 qtask_list.cli.__main__:app
├── dashboard/       # FastAPI 服务端 + templates/static（含 SPA 构建产物 static/spa）
├── remote_storage/  # 外存服务端
└── frontend/        # Vite+React 源码，pip 打包 exclude，产物输出 ../dashboard/static/spa
tests/  docs/  examples/  skills/  AGENTS.md  README.md  LICENSE  pyproject.toml  .gitignore
```

## 变更清单

1. `git mv` 四个目录进 `qtask_list/`。
2. import 路径：`dashboard.*` / `remote_storage.*` → `qtask_list.dashboard.*` /
   `qtask_list.remote_storage.*`（含测试）。
3. `pyproject.toml`：
   - `[project.scripts]` → `qtask_list.cli.__main__:app`
   - `packages.find` 收敛为 `include = ["qtask_list*"]`，exclude `frontend`
   - `package-data` 键改为 `qtask_list.dashboard`
4. pnpm：`git rm` `package-lock.json`，`pnpm install` 生成 `pnpm-lock.yaml` 入库，
   `package.json` 增加 `"packageManager": "pnpm@10.5.2"`。
5. 文档路径与命令同步：README（目录结构、前端命令 npm→pnpm）、HANDOFF 验证命令、
   skills 路径描述；历史 PLAN/OUTCOME 文档不追改。
6. 重装 `pip install -e .` 刷新入口点与 egg-info。

## 验证

- `python -m pytest tests/ -q` 全量
- `python -m ruff check .`、`python -m mypy qtask_list`
- `pnpm install` + `pnpm build`（产物落 `qtask_list/dashboard/static/spa/`）
- uvicorn 冒烟：无密码直接访问 / 设密码强制登录两条路径

## 风险与边界

- 行为不变，纯搬家 + 包管理器切换；`frontend/node_modules` 与各缓存目录不迁移，
  由 pnpm/工具链重建。
- 历史文档中的旧路径仅作历史记录保留。
