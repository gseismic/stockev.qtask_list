# PLAN-002 Dashboard 易用性优化结果

## 完成内容

- 默认队列选择改为按运维优先级选择：死信、待重试、失联 Worker、处理中、待处理、延迟、历史。
- 左侧队列列表改为同一优先级排序，并默认开启“只看当前”。
- 状态文案中文化，`all` 在界面中展示为“当前”，避免和历史混淆。
- 状态 tab 增加数量展示，减少用户反复切换确认的成本。
- 队列批量操作根据当前统计值禁用无效按钮：
  - 无 retry 时禁用“重试队列”
  - 无死信时禁用“重放死信”
  - 无处理中任务时禁用恢复操作
  - 无当前任务时禁用清空
- 空状态文案根据搜索、历史、当前状态给出更准确的提示。
- 投递任务表单改为默认折叠，降低监控和处理场景下的视觉干扰。
- 修复移动宽度下表格 `min-width` 撑开页面的问题，让横向滚动限制在表格容器内。
- 为 Dashboard HTML 添加空 favicon，避免浏览器默认请求 `/favicon.ico` 产生无关 404。

## 验证结果

- `node --check dashboard/static/js/api.js && node --check dashboard/static/js/utils.js && node --check dashboard/static/js/components.js && node --check dashboard/static/js/app.js`：通过
- `python -m ruff check .`：通过
- `python -m mypy qtask_list cli dashboard`：通过
- `python -m pytest -q`：64 passed
- 浏览器检查：
  - 桌面宽度无 console error
  - 临时样例死信队列会被默认选中
  - 无效批量操作按钮按状态禁用
  - 投递任务默认折叠
  - 390px 移动宽度无页面级横向溢出

## 说明

本次只调整 Dashboard 前端的信息架构和交互，不修改后端 API、Redis key 结构或队列生命周期语义。
