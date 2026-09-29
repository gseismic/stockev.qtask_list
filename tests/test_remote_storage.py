import json
import time
from pathlib import Path

import pytest
import redis
from fastapi.testclient import TestClient

from qtask_list import SmartQueue, TaskSpec
from qtask_list.remote_storage import server as storage_server
from qtask_list.storage import StoredObject


@pytest.fixture
def storage_client(tmp_path, monkeypatch):
    monkeypatch.setenv("QTASK_STORAGE_TOKEN", "review-token")
    monkeypatch.setattr(storage_server, "DATA_DIR", tmp_path)
    client = TestClient(storage_server.app)
    client.headers.update({"Authorization": "Bearer review-token"})
    return client


def test_remote_storage_upload_download_delete(tmp_path, storage_client):
    # 鉴权开启后，上传、读取和删除仍能完成同一对象的生命周期。
    client = storage_client

    upload = client.post(
        "/api/storage/upload",
        files={"file": ("payload.bin", b"hello qtask")},
    )

    assert upload.status_code == 200
    key = upload.json()["key"]
    stored_path = tmp_path / key[:2] / key
    assert stored_path.read_bytes() == b"hello qtask"

    download = client.get(f"/api/storage/download/{key}")
    assert download.status_code == 200
    assert download.content == b"hello qtask"

    delete = client.delete(f"/api/storage/delete/{key}")
    assert delete.status_code == 200
    assert delete.json() == {"deleted": key}
    assert not stored_path.exists()


def test_remote_storage_upload_rejects_missing_and_empty_file(storage_client):
    # 合法认证不能绕过请求体校验。
    client = storage_client

    missing = client.post("/api/storage/upload")
    assert missing.status_code == 400

    empty = client.post(
        "/api/storage/upload",
        files={"file": ("payload.bin", b"")},
    )
    assert empty.status_code == 400


def test_remote_storage_requires_auth_for_nonlocal_client(monkeypatch):
    # TestClient 的客户端地址是 testclient，模拟非 loopback 请求。
    monkeypatch.delenv("QTASK_STORAGE_TOKEN", raising=False)
    assert TestClient(storage_server.app).get("/api/storage/health").status_code == 403
    monkeypatch.setenv("QTASK_STORAGE_TOKEN", "review-token")
    assert TestClient(storage_server.app).get("/api/storage/health").status_code == 401


def test_pending_gc_promotion_survives_file_deletion_crash(tmp_path, monkeypatch):
    # 先持久移交 pending，再删文件；模拟删文件后进程退出仍不能接受悬空引用。
    monkeypatch.setattr(storage_server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_server, "_gc_redis_url", "redis://localhost:6379/0")
    r = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    storage_id = storage_server._storage_id()
    key = "a" * 32
    path = tmp_path / key[:2] / key
    path.parent.mkdir()
    path.write_bytes(b"payload")
    path.with_name(f"{key}.meta.json").write_text(
        json.dumps({"dedicated": True, "retain_until": 0}), encoding="utf-8"
    )
    pending_key = f"qtask:storage:pending:{storage_id}"
    gc_key = f"qtask:storage:gc:{storage_id}"
    r.zadd(pending_key, {key: time.time() - 1})

    original_unlink = Path.unlink

    def fail_first_unlink(self, *args, **kwargs):
        if self == path:
            raise OSError("simulated deletion failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    assert storage_server._cleanup_storage_gc() == 0
    assert r.zscore(pending_key, key) is None
    assert r.zscore(gc_key, key) is not None
    monkeypatch.setattr(Path, "unlink", original_unlink)
    path.unlink()  # 模拟删除成功、尚未从 GC 日志确认时的进程退出。

    stored = StoredObject(key, created=True, retain_until=0, dedicated=True,
                          storage_id=storage_id, pending=True)

    class ExistingStorage:
        def save(self, data, retain_until=None):
            return stored

        def save_managed(self, data, retain_until=None):
            return stored

        def delete(self, key):
            return False

    q = SmartQueue("redis://localhost:6379/0", "gc_crash_test", namespace="qtask_gc_test",
                   redis_client=r, storage=ExistingStorage(), large_threshold=1)
    with pytest.raises(RuntimeError, match="payload_expired"):
        q.enqueue(TaskSpec(action="test", payload={"value": "large"}))
    assert r.llen(q.queue) == 0
    assert storage_server._cleanup_storage_gc() == 1
    assert r.zscore(gc_key, key) is None


def test_manual_delete_revokes_pending_before_file_deletion(storage_client, tmp_path, monkeypatch):
    # 对象尚在 24 小时宽限期时，手动删除也先撤销入队资格。
    monkeypatch.setattr(storage_server, "_gc_redis_url", "redis://localhost:6379/0")
    uploaded = storage_client.post(
        "/api/storage/upload",
        data={"managed": "1"},
        files={"file": ("payload.bin", b"managed payload")},
    )
    assert uploaded.status_code == 200
    key = uploaded.json()["key"]
    storage_id = uploaded.json()["storage_id"]
    path = tmp_path / key[:2] / key
    r = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    pending_key = f"qtask:storage:pending:{storage_id}"
    gc_key = f"qtask:storage:gc:{storage_id}"
    assert r.zscore(pending_key, key) is not None

    original_unlink = Path.unlink

    def fail_file_delete(self, *args, **kwargs):
        if self == path:
            raise OSError("simulated process interruption")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_file_delete)
    with pytest.raises(OSError, match="simulated process interruption"):
        storage_client.delete(f"/api/storage/delete/{key}")
    assert r.zscore(pending_key, key) is None
    assert r.zscore(gc_key, key) is not None
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert storage_server._cleanup_storage_gc() == 1
    assert not path.exists()
    assert r.zscore(gc_key, key) is None
