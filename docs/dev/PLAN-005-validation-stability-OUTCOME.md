# PLAN-005 验证入口稳定性结果

## 实施结果

1. 稳定 pytest 默认收集范围：
   - 在 `pyproject.toml` 中设置 `testpaths = ["tests"]`。
   - 设置 `pythonpath = ["."]`，保证裸 `pytest` 与 `python -m pytest` 的本地包解析一致。
   - 设置 `norecursedirs`，避免递归进入本地 symlink、构建目录和缓存目录。
2. 忽略本地工作区 symlink：
   - `.gitignore` 新增 `/iosql`、`/stockev.qtask_list`。
3. 修复 RemoteStorage 上传接口类型边界：
   - 上传接口改为 FastAPI `UploadFile` 声明。
   - 保留缺失文件返回 400 的旧语义。
4. 修复 `qtask storage` 配置未生效问题：
   - 新增 `remote_storage.server.configure()`。
   - CLI 启动时将 `--data-dir`、`--ttl-days` 写入服务端运行状态。
   - CLI 启动时启动 TTL 清理线程。
   - 缺少服务端依赖时提示安装 `qtask_list[storage]`。
5. 补充 RemoteStorage 测试：
   - 覆盖上传、下载、删除。
   - 覆盖缺失/空文件错误。
   - 覆盖 CLI storage 配置注入且不真实启动 uvicorn。
6. 更新安装文档：
   - README 新增 `pip install -e ".[storage]"`。
   - `pyproject.toml` 新增 `storage` optional extra，包含 FastAPI、uvicorn、python-multipart。

## 验证结果

- `pytest -q`：69 passed
- `ruff check .`：All checks passed
- `mypy qtask_list cli dashboard remote_storage`：Success

## 后续建议

1. 可以进一步把 RemoteStorage 服务端配置和启动逻辑封装为公开 `run_server()`，减少 CLI 对服务端模块细节的了解。
2. 当前 Dashboard 和 RemoteStorage 分属不同 optional extra；后续发布文档可补充“完整运维安装”组合示例。
