# PLAN-003 Dashboard 问题处理控制台结果

## 完成内容

- 新增问题处理控制台设计文档：`docs/design/qtask-dashboard-20260620-operations.md`。
- 增加全局连接异常横幅：
  - 请求失败时显示“连接异常，当前数据可能已过期”。
  - 保留已加载的只读数据作为参考。
  - 禁用批量操作、危险操作、单任务写操作和投递任务。
- 增加当前队列主问题横幅：
  - DLQ 优先提示失败任务。
  - retry 提示待重试任务。
  - stale Worker 提示安全恢复。
  - `ready > 0 && active_workers = 0` 时提示“积压但没有活跃 Worker”。
- 明确统计卡片口径为“当前队列状态”。
- 将任务表降级为“任务样本”：
  - 在大量 ready 积压且无 Worker 时默认折叠。
  - 搜索或切换到异常状态时自动展开。
  - 表格操作列移到前面，避免横向滚动时看不到动作。
- 将 `强制恢复` 和 `清空队列` 移入右侧“危险操作”折叠面板。
- 投递任务保持折叠，并在连接异常时禁用。

## 验证结果

- `node --check dashboard/static/js/api.js && node --check dashboard/static/js/utils.js && node --check dashboard/static/js/components.js && node --check dashboard/static/js/app.js`：通过
- `python -m ruff check .`：通过
- `python -m mypy qtask_list cli dashboard`：通过
- `python -m pytest -q`：64 passed
- 浏览器验证：
  - 桌面宽度无 console error。
  - `sina:todo` 场景显示“待处理队列积压 5615 个任务，但没有活跃 Worker”。
  - 任务样本默认折叠。
  - 危险操作默认折叠。
  - 投递任务默认折叠。
  - 模拟连接失败后，全局错误横幅出现，旧数据保留，写操作禁用。
  - 390px 移动宽度无页面级横向溢出。

## 后续建议

- 增加 Dashboard API 分页或游标，避免大队列场景一次读取固定 120 条样本。
- 为 Worker 增加“建议启动命令”配置入口，但不要在 qtask_list 内直接管理业务进程。
