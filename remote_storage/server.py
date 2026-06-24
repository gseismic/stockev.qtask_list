"""qtask_list RemoteStorage 服务端。

协议与客户端 qtask_list/storage.py 匹配：
  POST /api/storage/upload          → 上传 bytes，返回 {"key": "..."}
  GET  /api/storage/download/{key}  → 下载原始 bytes
  DELETE /api/storage/delete/{key}  → 删除

特性：
- SHA256 内容寻址，相同内容自动去重
- 按 key 前两位分子目录，避免单目录文件过多
- TTL 自动清理（默认 7 天），后台线程定期执行

启动：
  python -m remote_storage.server --port 8096
  uvicorn remote_storage.server:app --port 8096
"""

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from loguru import logger

app = FastAPI(title="qtask RemoteStorage")

DEFAULT_DIR = Path(os.environ.get("QTASK_STORAGE_DIR", Path.home() / ".qtask-storage"))
DATA_DIR = DEFAULT_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _key_path(key: str) -> Path:
    return DATA_DIR / key[:2] / key


def _generate_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


_ttl_seconds: float = float(os.environ.get("QTASK_STORAGE_TTL", 7 * 86400))
_cleanup_interval: float = 3600


def configure(data_dir: str | Path | None = None, ttl_days: float | None = None) -> None:
    """配置服务端运行目录和 TTL。"""
    global DATA_DIR, _ttl_seconds

    if data_dir is not None:
        DATA_DIR = Path(data_dir)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    if ttl_days is not None:
        _ttl_seconds = ttl_days * 86400 if ttl_days > 0 else 0


def _cleanup_expired():
    """删除超过 TTL 的缓存文件。"""
    if _ttl_seconds <= 0:
        return
    now = time.time()
    removed = 0
    for path in DATA_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime > _ttl_seconds:
                path.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"TTL 清理完成，删除 {removed} 个过期文件")


def _start_cleanup_thread():
    def _loop():
        while True:
            time.sleep(_cleanup_interval)
            try:
                _cleanup_expired()
            except Exception:
                logger.exception("TTL 清理异常")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


@app.post("/api/storage/upload")
async def upload(file: Annotated[UploadFile | None, File()] = None):
    if file is None:
        raise HTTPException(400, "missing 'file' field")

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty payload")

    key = _generate_key(data)
    path = _key_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = path.write_bytes(data)
    logger.info(f"upload: key={key} size={size}")
    return {"key": key}


@app.get("/api/storage/download/{key}")
async def download(key: str):
    path = _key_path(key)
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    data = path.read_bytes()
    logger.debug(f"download: key={key} size={len(data)}")
    return Response(content=data, media_type="application/octet-stream")


@app.delete("/api/storage/delete/{key}")
async def delete(key: str):
    path = _key_path(key)
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    path.unlink()
    logger.info(f"delete: key={key}")
    return {"deleted": key}


@app.get("/api/storage/health")
async def health():
    files = [p for p in DATA_DIR.rglob("*") if p.is_file()]
    total_size = sum(p.stat().st_size for p in files)
    return {
        "status": "ok",
        "files": len(files),
        "total_size": total_size,
        "ttl_days": _ttl_seconds / 86400 if _ttl_seconds > 0 else None,
        "data_dir": str(DATA_DIR),
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="qtask RemoteStorage server")
    p.add_argument("--port", type=int, default=8096)
    p.add_argument("--data-dir", type=str, default=str(DEFAULT_DIR))
    p.add_argument("--host", type=str, default="0.0.0.0")
    p.add_argument("--ttl-days", type=float, default=7.0, help="文件保留天数，0=永不过期")
    args = p.parse_args()

    configure(args.data_dir, args.ttl_days)

    logger.info(f"RemoteStorage 启动: data_dir={DATA_DIR}, ttl={args.ttl_days}天, port={args.port}")
    _start_cleanup_thread()

    uvicorn.run(app, host=args.host, port=args.port)
