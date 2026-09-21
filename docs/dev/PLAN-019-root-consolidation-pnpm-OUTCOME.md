# PLAN-019 结果：根目录收敛 + 前端切换 pnpm

- 日期：2026-09-21
- 计划文件：`docs/dev/PLAN-019-root-consolidation-pnpm.md`

## 结论

仓库收敛为单包结构，前端工具链切换 pnpm，全部验证通过。

## 变更清单

1. 目录搬家（git mv，保留历史）：`cli/`、`dashboard/`、`remote_storage/`、
   `frontend/` → `qtask_list/{cli,dashboard,remote_storage,frontend}`。
2. import 路径：`dashboard.auth` / `remote_storage.server` / `cli.__main__` →
   `qtask_list.*`（含 cli、测试）。
3. `pyproject.toml`：入口点 `qtask_list.cli.__main__:app`；`packages.find`
   收敛为 `include = ["qtask_list*"]` + exclude frontend；package-data 键改
   `qtask_list.dashboard`。
4. pnpm：删除 `package-lock.json`，新增 `pnpm-lock.yaml`（入库）与
   `package.json` 的 `"packageManager": "pnpm@10.5.2"`、`pnpm.onlyBuiltDependencies`
   白名单（pnpm 10 默认拦截 esbuild 构建脚本）。
5. 删除冗余 `requirements.txt`（与 pyproject dependencies 完全重复）。
6. 文档同步：README（核心模块表、项目结构图、前端命令 pnpm、uvicorn 部署路径）、
   `skills/qtask-list-usage/SKILL.md`（结构图与路径）、`docs/HANDOFF.md`（验证基线、
   管理侧描述）；examples 中 `python -m cli` 等模块调用路径全部改为
   `python -m qtask_list.cli` 等。历史 PLAN/OUTCOME 不追改。
7. 重装 `pip install -e ".[dev,dashboard]"` 刷新入口点。

## 验证

- pytest 119 passed；ruff、mypy（`python -m mypy qtask_list`）通过；examples compileall 通过。
- `pnpm install` + `pnpm build`：产物正确落 `qtask_list/dashboard/static/spa/`
  （vite outDir 相对路径在新结构下无需修改）。
- uvicorn 冒烟：无密码 `/api/queues` 200；设 `QTASK_DASHBOARD_PASSWORD` 后
  `/api/queues` 401、`/queues` 307 → `/login`，登录后 200。
- `qtask --help` 入口点正常。
