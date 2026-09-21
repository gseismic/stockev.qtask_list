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
  python -m qtask_list.remote_storage.server --port 8096
  uvicorn remote_storage.server:app --port 8096
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from loguru import logger
from pydantic import BaseModel

app = FastAPI(title="qtask RemoteStorage")

DEFAULT_DIR = Path(os.environ.get("QTASK_STORAGE_DIR", Path.home() / ".qtask-storage"))
DATA_DIR = DEFAULT_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _key_path(key: str) -> Path:
    if len(key) != 32 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("invalid storage key")
    return DATA_DIR / key[:2] / key


def _meta_path(key: str) -> Path:
    """外存保留元数据与内容分离，避免修改共享内容本身。"""
    return _key_path(key).with_name(f"{key}.meta.json")


def _generate_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


_ttl_seconds: float = float(os.environ.get("QTASK_STORAGE_TTL", 7 * 86400))
_cleanup_interval: float = 3600
_metadata_lock = threading.Lock()


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
        if not path.is_file() or path.name.endswith(".meta.json"):
            continue
        try:
            meta_path = path.with_name(f"{path.name}.meta.json")
            retain_until = None
            if meta_path.exists():
                try:
                    retain_until = json.loads(meta_path.read_text(encoding="utf-8")).get(
                        "retain_until"
                    )
                except (OSError, ValueError, TypeError):
                    retain_until = None
            # 0 表示显式持久保留；未来绝对时刻优先于全局 TTL。
            if retain_until == 0 or (retain_until and float(retain_until) > now):
                continue
            if retain_until or now - path.stat().st_mtime > _ttl_seconds:
                path.unlink()
                meta_path.unlink(missing_ok=True)
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
async def upload(
    file: Annotated[UploadFile | None, File()] = None,
    retain_until: Annotated[str | None, Form()] = None,
):
    if file is None:
        raise HTTPException(400, "missing 'file' field")

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty payload")

    key = _generate_key(data)
    path = _key_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if created:
        size = path.write_bytes(data)
    else:
        size = path.stat().st_size

    # 空字符串由 V2 客户端显式发送，表示无界任务需要持久对象。
    requested_retain: float | None
    if retain_until == "":
        requested_retain = 0
    elif retain_until is None:
        requested_retain = None
    else:
        try:
            requested_retain = float(retain_until)
        except ValueError as exc:
            raise HTTPException(400, "invalid retain_until") from exc
    effective_retain = _extend_retention(key, requested_retain)
    logger.info(f"upload: key={key} size={size}")
    return {
        "key": key,
        "created": created,
        "retain_until": effective_retain,
        "size": size,
    }


@app.get("/api/storage/download/{key}")
async def download(key: str):
    try:
        path = _key_path(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    data = path.read_bytes()
    logger.debug(f"download: key={key} size={len(data)}")
    return Response(content=data, media_type="application/octet-stream")


@app.delete("/api/storage/delete/{key}")
async def delete(key: str):
    try:
        path = _key_path(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    path.unlink()
    _meta_path(key).unlink(missing_ok=True)
    logger.info(f"delete: key={key}")
    return {"deleted": key}


class RetentionRequest(BaseModel):
    """共享内容对象的单调保留期延长请求。"""

    retain_until: float | None = None


def _extend_retention(key: str, requested: float | None) -> float | None:
    """单调延长 retain_until；0 表示持久保留，None 表示沿用服务端 TTL。"""
    meta_path = _meta_path(key)
    with _metadata_lock:
        existing: float | None = None
        if meta_path.exists():
            try:
                raw_existing = json.loads(meta_path.read_text(encoding="utf-8")).get(
                    "retain_until"
                )
                existing = float(raw_existing) if raw_existing is not None else None
            except (OSError, ValueError, TypeError):
                existing = None
        if existing == 0 or requested == 0:
            effective: float | None = 0
        elif existing is None:
            effective = requested
        elif requested is None:
            effective = existing
        else:
            effective = max(existing, requested)
        if effective is not None:
            meta_path.write_text(
                json.dumps({"retain_until": effective}),
                encoding="utf-8",
            )
        return effective


@app.post("/api/storage/retain/{key}")
async def retain(key: str, request: RetentionRequest):
    try:
        path = _key_path(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.exists():
        raise HTTPException(404, f"key not found: {key}")
    return {"key": key, "retain_until": _extend_retention(key, request.retain_until or 0)}


@app.get("/api/storage/health")
async def health():
    files = [
        path
        for path in DATA_DIR.rglob("*")
        if path.is_file() and not path.name.endswith(".meta.json")
    ]
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
