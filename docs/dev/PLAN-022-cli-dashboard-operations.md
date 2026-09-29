# PLAN-022：CLI 与 Dashboard 运维场景优化

- 日期：2026-09-29
- 设计依据：`docs/design/cli-dashboard-20260929-operations-review.md`
- 目标：让值班巡检、跨队列排查和故障识别的展示与实际状态一致，并使 CLI 可用于脚本。

## 实施步骤

1. 扩展 `qtask status`：全局 namespace 筛选、异常筛选、JSON 输出；表格显示 retry_wait、过期、在线/失联 Worker，并复用纯函数判定异常。扩展 `watch` 的状态字段，限制刷新间隔为正数；把 `worker` 入口收敛为必填业务模块，移除实际无效的配置参数；远程无认证 Dashboard 需显式 `--allow-unauthenticated`；校验 CLI 使用帮助与 JSON 可解析。
2. 统一 Dashboard 全局任务查询到 `api.ts`；后端 `/api/tasks` 增加已支持的创建/完成时间范围参数；前端给全局搜索增加 action 和时间高级筛选，切换条件时重置分页，检查 401/4xx 错误语义。
3. 修正采样口径：不把浏览器观察值称为一小时结果或绝对进度；停滞有观察门槛，仅针对可运行 backlog；总览和队列卡说明估计值与未知值。把 ready 有任务但无在线 Worker 纳入告警，纯延迟队列除外。
4. 修正刷新与故障状态：全局 `reload()` 实际触发请求，操作成功后刷新，手动刷新可用；Redis 健康异常、首次请求失败和过期数据明确呈现；修正 Worker 启动示例与退出登录导航。分页过期视图正确传递展示状态，任务抽屉按真实时间字段呈现时间线，并让过期任务走专用的“更新截止并重放”接口；拒绝已过去的新截止时间且不得取消旧任务。总览矩阵补列名；手工投递降为次级操作，表单验证 action/秒数并正确反馈 `accepted=false`。
5. 补充与行为对应的 CLI/API 回归测试，并对浏览器采样做独立场景验证；在隔离 Redis 上运行相关与全量测试，运行 ruff、mypy、前端构建，检查构建产物与文档。
6. 代码复查后写 `PLAN-022-cli-dashboard-operations-OUTCOME.md`，追加 `docs/dev/INDEX.md`，中文详细提交并立刻推送。

## 验收

- `qtask status --namespace stockev --problems-only --json` 输出纯 JSON，只包含该 namespace 的异常队列；默认表格可见重要状态。
- Redis 故障或登录过期时，Dashboard 不把未知状态显示为健康；操作后无需等待自动刷新即可看到新状态。
- 跨队列可按 action 和时间筛选，并按游标继续；错误经统一鉴权处理。
- 纯延迟队列和刚打开的队列不会被标为停滞；所有采样标签真实反映观测范围。
