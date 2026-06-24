# PLAN-005 验证入口稳定性

## 目标

稳定本项目的日常验证入口，确保 `pytest -q`、`ruff`、`mypy` 能直接反映本库代码质量，而不会被本地工作区 symlink 或隐式类型边界干扰。

## 当前证据

1. `pytest -q` 会递归进入根目录下未跟踪的 `iosql` 和 `stockev.qtask_list` symlink，收集外部测试和自引用路径后失败。
2. `mypy qtask_list cli dashboard remote_storage` 在 `remote_storage/server.py` 报告上传文件类型不明确。
3. `qtask storage --data-dir/--ttl-days` 只在 CLI 中计算和打印配置，没有把配置传入 `remote_storage.server` 的全局运行状态。

## 实施方案

1. 在 `pyproject.toml` 中增加 pytest 配置：
   - 默认只收集 `tests/`。
   - 明确跳过本地 symlink 和常见构建/缓存目录。
2. 在 `.gitignore` 中忽略本地工作区 symlink：
   - `/iosql`
   - `/stockev.qtask_list`
3. 改造 RemoteStorage 服务端上传接口：
   - 使用 FastAPI `UploadFile` 声明 multipart 文件字段。
   - 避免 `request.form()` 返回 `str | UploadFile` 的类型歧义。
4. 增加 RemoteStorage 服务端配置函数：
   - CLI 启动时将 `data_dir`、`ttl_days` 写入服务端运行状态。
   - CLI 启动时启动 TTL 清理线程。
5. 补充测试：
   - 覆盖上传、下载、删除。
   - 覆盖 CLI storage 命令会把配置传给服务端且不真实启动 uvicorn。

## 验证

- `pytest -q`
- `ruff check .`
- `mypy qtask_list cli dashboard remote_storage`
