from fastapi.testclient import TestClient

from remote_storage import server as storage_server


def test_remote_storage_upload_download_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_server, "DATA_DIR", tmp_path)
    client = TestClient(storage_server.app)

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


def test_remote_storage_upload_rejects_missing_and_empty_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_server, "DATA_DIR", tmp_path)
    client = TestClient(storage_server.app)

    missing = client.post("/api/storage/upload")
    assert missing.status_code == 400

    empty = client.post(
        "/api/storage/upload",
        files={"file": ("payload.bin", b"")},
    )
    assert empty.status_code == 400
