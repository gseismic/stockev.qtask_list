"""qtask_list RemoteStorage 服务端 —— 最小实现。

协议与 qtask_list/storage.py 的 RemoteStorage 客户端匹配：
  POST /api/storage/upload   → 上传 bytes，返回 {"key": "..."}
  GET  /api/storage/download/<key> → 下载 bytes
  DELETE /api/storage/delete/<key> → 删除

启动:
  python remote_storage/server.py --port 8096 --data-dir /var/lib/qtask-storage
  uvicorn remote_storage.server:app --port 8096
"""

import hashlib
import os
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="qtask RemoteStorage")

DATA_DIR = Path(os.environ.get("QTASK_STORAGE_DIR", Path.home() / ".qtask-storage"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _key_path(key: str) -> Path:
    # 用 key 的前两位做子目录，避免单目录文件过多
    return DATA_DIR / key[:2] / key


def _generate_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


@app.post("/api/storage/upload")
async def upload(request: Request):
    """上传 bytes，返回 key。客户端用 multipart/form-data 发送。"""
    form = await request.form()
    file = form.get("file")
    if file is None:
        raise HTTPException(400, "missing 'file' field")

    data = await file.read()
    key = _generate_key(data)
    path = _key_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"key": key}


@app.get("/api/storage/download/{key}")
async def download(key: str):
    """根据 key 下载原始 bytes。"""
    path = _key_path(key)
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    return Response(content=path.read_bytes(), media_type="application/octet-stream")


@app.delete("/api/storage/delete/{key}")
async def delete(key: str):
    """删除指定 key 的数据。"""
    path = _key_path(key)
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    path.unlink()
    return {"deleted": key}


@app.get("/api/storage/health")
async def health():
    files = sum(1 for _ in DATA_DIR.rglob("*") if _.is_file())
    return {"status": "ok", "files": files}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8096)
    p.add_argument("--data-dir", type=str, default=str(DATA_DIR))
    p.add_argument("--host", type=str, default="0.0.0.0")
    args = p.parse_args()

    global DATA_DIR
    DATA_DIR = Path(args.data_dir)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    uvicorn.run(app, host=args.host, port=args.port)
