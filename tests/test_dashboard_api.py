import json
import time

import pytest
import redis
from fastapi.testclient import TestClient

from dashboard.main import app


@pytest.fixture(autouse=True)
def dashboard_auth_env(monkeypatch):
    for name in [
        "QTASK_DASHBOARD_AUTH",
        "QTASK_DASHBOARD_USER",
        "QTASK_DASHBOARD_PASSWORD",
        "QTASK_DASHBOARD_SECRET",
        "QTASK_DASHBOARD_SESSION_TTL",
        "QTASK_DASHBOARD_SECURE_COOKIE",
    ]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def r():
    client = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    cleanup(client)
    yield client
    cleanup(client)


def cleanup(client):
    for hist_key in client.scan_iter("qtask:hist:qtask_dash_test:*"):
        for task_id in client.zrange(hist_key, 0, -1):
            client.delete(f"qtask:task:{task_id}")
        client.delete(hist_key)
    for task_id in ["retry-with-history", "retry-missing-history"]:
        client.delete(f"qtask:task:{task_id}")
    for key in client.scan_iter("qtask_dash_test:*"):
        client.delete(key)


def make_msg(task_id: str, payload: dict | None = None) -> str:
    return json.dumps({"task_id": task_id, "payload": json.dumps(payload or {})})


def seed_expired_tasks(client, queue: str, count: int, prefix: str, status: str = "pending") -> list[str]:
    now = time.time()
    task_ids = []
    pipe = client.pipeline()
    for index in range(count):
        task_id = f"{prefix}-{index:03d}"
        task_ids.append(task_id)
        pipe.hset(
            f"qtask:task:{task_id}",
            mapping={
                "task_id": task_id,
                "status": status,
                "action": "expired_action",
                "expires_at": str(now - 10),
                "payload": json.dumps({"action": "expired_action", "index": index}),
            },
        )
        pipe.zadd(f"qtask:hist:{queue}", {task_id: now - index})
    pipe.execute()
    return task_ids


def message_ids(client, key: str) -> list[str]:
    return [json.loads(raw)["task_id"] for raw in client.lrange(key, 0, -1)]


def test_dashboard_lists_queues_and_state_tasks(client, r):
    queue = "qtask_dash_test:day-kline:fetch"
    r.lpush(queue, make_msg("ready-1", {"action": "scrape_day_kline", "symbol": "sh600000"}))
    r.lpush(f"{queue}:dlq", make_msg("dlq-1", {"action": "scrape_day_kline"}))

    queues = client.get("/api/queues")
    assert queues.status_code == 200
    data = queues.json()
    item = next(q for q in data if q["name"] == queue)
    assert item["queue"] == 1
    assert item["dlq"] == 1

    ready = client.get(f"/api/queue/{queue}/tasks", params={"state": "ready"})
    assert ready.status_code == 200
    assert ready.json()["tasks"][0]["task_id"] == "ready-1"

    dlq = client.get(f"/api/queue/{queue}/tasks", params={"state": "dlq"})
    assert dlq.status_code == 200
    assert dlq.json()["tasks"][0]["task_id"] == "dlq-1"


def test_dashboard_requeues_single_dlq_task(client, r):
    queue = "qtask_dash_test:sector-em:fetch"
    r.hset("qtask:task:dlq-move", mapping={"task_id": "dlq-move", "status": "failed"})
    r.zadd(f"qtask:hist:{queue}", {"dlq-move": 1})
    r.lpush(f"{queue}:dlq", make_msg("dlq-keep"))
    r.lpush(f"{queue}:dlq", make_msg("dlq-move"))

    response = client.post(
        "/api/task/dlq-move/requeue",
        json={"queue": queue, "from_state": "dlq"},
    )

    assert response.status_code == 200
    assert response.json()["moved"] == 1
    assert message_ids(r, queue) == ["dlq-move"]
    assert message_ids(r, f"{queue}:dlq") == ["dlq-keep"]
    assert r.hget("qtask:task:dlq-move", "status") == "pending"


def test_dashboard_bulk_retry_and_requeue_dlq(client, r):
    queue = "qtask_dash_test:sector-sina:fetch"
    r.lpush(f"{queue}:retry", make_msg("retry-1"))
    r.lpush(f"{queue}:dlq", make_msg("dlq-1"))

    retry = client.post(f"/api/queue/{queue}/retry")
    dlq = client.post(f"/api/queue/{queue}/requeue-dlq", json={"task_id": None})

    assert retry.status_code == 200
    assert retry.json()["moved"] == 1
    assert dlq.status_code == 200
    assert dlq.json()["moved"] == 1
    assert r.llen(queue) == 2
    assert r.llen(f"{queue}:retry") == 0
    assert r.llen(f"{queue}:dlq") == 0


def test_dashboard_bulk_retry_updates_existing_history_without_orphans(client, r):
    queue = "qtask_dash_test:sector-sina:store"
    r.hset(
        "qtask:task:retry-with-history",
        mapping={"task_id": "retry-with-history", "status": "retry"},
    )
    r.expire("qtask:task:retry-with-history", 60)
    r.zadd(f"qtask:hist:{queue}", {"retry-with-history": 1})
    r.lpush(f"{queue}:retry", make_msg("retry-with-history"))
    r.lpush(f"{queue}:retry", make_msg("retry-missing-history"))

    response = client.post(f"/api/queue/{queue}/retry")

    assert response.status_code == 200
    assert response.json()["moved"] == 2
    assert r.hget("qtask:task:retry-with-history", "status") == "pending"
    assert r.ttl("qtask:task:retry-with-history") > 60
    assert r.zscore(f"qtask:hist:{queue}", "retry-with-history") is not None
    assert r.exists("qtask:task:retry-missing-history") == 0


def test_dashboard_recover_skips_active_worker(client, r):
    queue = "qtask_dash_test:day-kline:store"
    r.lpush(f"{queue}:processing", make_msg("legacy"))
    r.lpush(f"{queue}:processing:active", make_msg("active"))
    r.lpush(f"{queue}:processing:stale", make_msg("stale"))
    r.set(f"{queue}:worker:active", "1", ex=60)

    response = client.post(f"/api/queue/{queue}/recover", json={"include_active": False})

    assert response.status_code == 200
    assert response.json() == {"recovered": 2, "skipped_active": 1}
    assert r.llen(queue) == 2
    assert r.llen(f"{queue}:processing:active") == 1


def test_dashboard_deletes_task_from_queue_and_history(client, r):
    queue = "qtask_dash_test:fin-sheet-em:fetch"
    r.lpush(queue, make_msg("delete-me", {"action": "scrape_fin_sheet"}))
    r.hset("qtask:task:delete-me", mapping={"task_id": "delete-me", "status": "pending"})
    r.zadd(f"qtask:hist:{queue}", {"delete-me": 1})

    response = client.delete("/api/task/delete-me", params={"queue": queue})

    assert response.status_code == 200
    assert response.json()["queue_messages"] == 1
    assert r.llen(queue) == 0
    assert r.exists("qtask:task:delete-me") == 0
    assert r.zscore(f"qtask:hist:{queue}", "delete-me") is None


def test_dashboard_push_task(client, r):
    queue = "qtask_dash_test:day-kline-xq:fetch"
    response = client.post(
        f"/api/queue/{queue}/tasks",
        json={"payload": {"action": "scrape_day_kline", "symbol": "SZ000001"}},
    )

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert r.llen(queue) == 1
    assert r.exists(f"qtask:task:{task_id}") == 1


def test_dashboard_auth_disabled_by_default(client):
    response = client.get("/api/auth")
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    queues = client.get("/api/queues")
    assert queues.status_code == 200


def test_dashboard_auth_requires_login(monkeypatch):
    monkeypatch.setenv("QTASK_DASHBOARD_USER", "ops")
    monkeypatch.setenv("QTASK_DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("QTASK_DASHBOARD_SECRET", "test-secret")
    client = TestClient(app)

    index = client.get("/", follow_redirects=False)
    assert index.status_code in {302, 307}
    assert index.headers["location"] == "/login"

    queues = client.get("/api/queues")
    assert queues.status_code == 401

    failed = client.post("/api/login", json={"username": "ops", "password": "bad"})
    assert failed.status_code == 401
    assert "qtask_dashboard_session" not in client.cookies

    logged_in = client.post("/api/login", json={"username": "ops", "password": "secret"})
    assert logged_in.status_code == 200
    assert logged_in.json()["authenticated"] is True
    assert "qtask_dashboard_session" in client.cookies

    auth = client.get("/api/auth")
    assert auth.status_code == 200
    assert auth.json()["authenticated"] is True

    queues = client.get("/api/queues")
    assert queues.status_code == 200

    logout = client.post("/api/logout")
    assert logout.status_code == 200
    assert "qtask_dashboard_session" not in client.cookies

    queues = client.get("/api/queues")
    assert queues.status_code == 401


def test_dashboard_queue_stats_includes_completed_failed(client, r):
    queue = "qtask_dash_test:perf-sina:fetch"
    r.lpush(queue, make_msg("perf-1", {"action": "scrape_perf"}))

    r.hset("qtask:task:hist-ok", mapping={
        "task_id": "hist-ok", "status": "completed", "action": "scrape_perf",
    })
    r.hset("qtask:task:hist-fail", mapping={
        "task_id": "hist-fail", "status": "failed", "action": "scrape_perf",
    })
    r.hset("qtask:task:hist-pending", mapping={
        "task_id": "hist-pending", "status": "pending", "action": "scrape_perf",
    })
    r.zadd(f"qtask:hist:{queue}", {"hist-ok": 1, "hist-fail": 2, "hist-pending": 3})

    response = client.get(f"/api/queue/{queue}")
    assert response.status_code == 200
    stats = response.json()["stats"]
    assert stats["history"] == 3
    assert stats["completed"] == 1
    assert stats["failed"] == 1
    # pending should not be counted as completed or failed
    assert stats["completed"] + stats["failed"] <= stats["history"]


def test_dashboard_delete_queue(client, r):
    queue = "qtask_dash_test:delete-me:fetch"
    r.lpush(queue, make_msg("del-1"))
    r.lpush(f"{queue}:dlq", make_msg("del-dlq"))
    r.hset("qtask:task:del-hist", mapping={
        "task_id": "del-hist", "status": "completed",
    })
    r.zadd(f"qtask:hist:{queue}", {"del-hist": 1})

    queues_before = client.get("/api/queues").json()
    assert any(q["name"] == queue for q in queues_before)

    response = client.delete(f"/api/queue/{queue}")
    assert response.status_code == 200
    result = response.json()
    assert result["history_records"] == 1
    assert result["deleted_keys"] > 0

    queues_after = client.get("/api/queues").json()
    assert not any(q["name"] == queue for q in queues_after)
    assert r.exists(queue) == 0
    assert r.exists(f"{queue}:dlq") == 0
    assert r.exists("qtask:task:del-hist") == 0


def test_dashboard_supplements_action_from_history_for_large_payload(client, r):
    queue = "qtask_dash_test:large-payload:fetch"
    large_payload = {"_large": True, "key": "abc123"}
    msg = json.dumps({"task_id": "large-1", "payload": json.dumps(large_payload)})
    r.lpush(queue, msg)
    r.hset("qtask:task:large-1", mapping={
        "task_id": "large-1", "status": "pending", "action": "scrape_day_kline",
    })
    r.zadd(f"qtask:hist:{queue}", {"large-1": 1})

    response = client.get(f"/api/queue/{queue}/tasks", params={"state": "ready"})
    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["task_id"] == "large-1"
    assert task["action"] == "scrape_day_kline"


def test_dashboard_resolves_compressed_payload(client, r):
    import base64
    import zstandard

    queue = "qtask_dash_test:compressed:fetch"
    original = {"action": "scrape_day_kline", "symbol": "sh600000"}
    data = json.dumps(original).encode()
    compressed = zstandard.ZstdCompressor().compress(data)
    payload = {"_compressed": True, "data": base64.b64encode(compressed).decode()}
    msg = json.dumps({"task_id": "comp-1", "payload": json.dumps(payload)})
    r.lpush(queue, msg)

    response = client.get("/api/task/comp-1/payload", params={"queue": queue, "state": "ready"})
    assert response.status_code == 200
    result = response.json()
    assert result["action"] == "scrape_day_kline"
    assert result["payload"]["symbol"] == "sh600000"


def test_dashboard_payload_endpoint_for_large_without_storage(client, r):
    queue = "qtask_dash_test:large-nostorage:fetch"
    payload = {"_large": True, "key": "abc123"}
    msg = json.dumps({"task_id": "large-ns-1", "payload": json.dumps(payload)})
    r.lpush(queue, msg)
    r.hset("qtask:task:large-ns-1", mapping={
        "task_id": "large-ns-1", "status": "pending", "action": "scrape_fin_sheet",
    })
    r.zadd(f"qtask:hist:{queue}", {"large-ns-1": 1})

    response = client.get("/api/task/large-ns-1/payload", params={"queue": queue, "state": "ready"})
    assert response.status_code == 200
    result = response.json()
    assert result["action"] == "scrape_fin_sheet"
    assert result["payload"]["_large"] is True
    assert "未配置" in result["_note"]


# ── 过期任务测试 ──

def test_dashboard_push_with_expire(client, r):
    """推送带过期时间的任务。"""
    import time
    queue = "qtask_dash_test:expire:fetch"
    r.delete(queue)
    r.delete(f"qtask:hist:{queue}")

    response = client.post(
        f"/api/queue/{queue}/tasks",
        json={"payload": {"action": "test_expire"}, "expire_seconds": 1},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    record = r.hgetall(f"qtask:task:{task_id}")
    assert "expires_at" in record
    assert float(record["expires_at"]) > time.time()

    r.delete(f"qtask:task:{task_id}")


def test_dashboard_list_expired(client, r):
    """列出过期任务。"""
    import time
    queue = "qtask_dash_test:expired:fetch"
    r.delete(queue)
    r.delete(f"qtask:hist:{queue}")

    task_id = "exp-task-01"
    now = time.time()
    r.hset(f"qtask:task:{task_id}", mapping={
        "task_id": task_id, "status": "pending", "action": "expired_action",
        "expires_at": str(now - 10),
    })
    r.zadd(f"qtask:hist:{queue}", {task_id: now - 10})

    response = client.get(f"/api/queue/{queue}/expired")
    assert response.status_code == 200
    assert response.json()["count"] >= 1
    tasks = response.json()["tasks"]
    assert any(t["task_id"] == task_id for t in tasks)

    r.delete(f"qtask:task:{task_id}")
    r.delete(f"qtask:hist:{queue}")


def test_dashboard_list_expired_respects_limit_above_fifty(client, r):
    """过期任务列表应尊重 API limit，而不是固定截断到 50 条。"""
    queue = "qtask_dash_test:expired:limit"
    seed_expired_tasks(r, queue, 60, "exp-limit")

    response = client.get(f"/api/queue/{queue}/expired", params={"limit": 60})

    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 60
    assert len(result["tasks"]) == 60

    state_response = client.get(
        f"/api/queue/{queue}/tasks",
        params={"state": "expired", "limit": 60},
    )
    assert state_response.status_code == 200
    state_result = state_response.json()
    assert state_result["count"] == 60
    assert len(state_result["tasks"]) == 60


def test_dashboard_requeue_expired(client, r):
    """放回过期任务。"""
    import time
    queue = "qtask_dash_test:expired:requeue"
    r.delete(queue)
    r.delete(f"qtask:hist:{queue}")

    task_id = "exp-task-02"
    now = time.time()
    r.hset(f"qtask:task:{task_id}", mapping={
        "task_id": task_id, "status": "pending", "action": "expired_action",
        "expires_at": str(now - 10), "payload": json.dumps({"action": "expired_action"}),
    })
    r.zadd(f"qtask:hist:{queue}", {task_id: now - 10})

    response = client.post(
        f"/api/queue/{queue}/requeue-expired",
        json={"task_id": task_id},
    )
    assert response.status_code == 200
    assert response.json()["moved"] == 1

    record = r.hgetall(f"qtask:task:{task_id}")
    assert record["status"] == "pending"

    r.delete(queue)
    r.delete(f"qtask:task:{task_id}")
    r.delete(f"qtask:hist:{queue}")


def test_dashboard_requeue_expired_bulk_handles_more_than_fifty(client, r):
    """批量放回过期任务不应被内部 50 条常量截断。"""
    queue = "qtask_dash_test:expired:bulk-requeue"
    task_ids = seed_expired_tasks(r, queue, 60, "exp-bulk", status="expired")

    response = client.post(f"/api/queue/{queue}/requeue-expired", json={"task_id": None})

    assert response.status_code == 200
    assert response.json()["moved"] == 60
    assert r.llen(queue) == 60
    assert all(r.hget(f"qtask:task:{task_id}", "status") == "pending" for task_id in task_ids)

    expired_again = client.get(f"/api/queue/{queue}/expired", params={"limit": 60})
    assert expired_again.status_code == 200
    assert expired_again.json()["count"] == 0
    assert all(r.hget(f"qtask:task:{task_id}", "expires_at") == "" for task_id in task_ids)


def test_dashboard_queue_stats_includes_expired(client, r):
    """queue_stats 包含 expired 计数。"""
    import time
    queue = "qtask_dash_test:stats:expired"
    r.delete(queue)
    r.delete(f"qtask:hist:{queue}")

    task_id = "exp-task-03"
    now = time.time()
    r.hset(f"qtask:task:{task_id}", mapping={
        "task_id": task_id, "status": "pending", "action": "expired_action",
        "expires_at": str(now - 10),
    })
    r.zadd(f"qtask:hist:{queue}", {task_id: now - 10})

    response = client.get("/api/queues")
    assert response.status_code == 200
    stats = next((q for q in response.json() if q["name"] == queue), None)
    assert stats is not None
    assert "expired" in stats
    assert stats["expired"] >= 1

    r.delete(f"qtask:task:{task_id}")
    r.delete(f"qtask:hist:{queue}")


def test_dashboard_list_completed_tasks(client, r):
    """按已完成状态过滤任务。"""
    queue = "qtask_dash_test:completed:tasks"
    r.delete(queue)
    r.delete(f"qtask:hist:{queue}")

    task_id = "comp-task-01"
    now = time.time()
    r.hset(f"qtask:task:{task_id}", mapping={
        "task_id": task_id, "status": "completed", "action": "done_action",
        "created_at": str(now - 100), "updated_at": str(now - 50),
    })
    r.zadd(f"qtask:hist:{queue}", {task_id: now - 50})

    response = client.get(f"/api/queue/{queue}/tasks", params={"state": "completed"})
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert any(t["task_id"] == task_id and t["_state"] == "completed" for t in tasks)

    r.delete(f"qtask:task:{task_id}")
    r.delete(f"qtask:hist:{queue}")


def test_dashboard_completed_tasks_scan_beyond_recent_pending(client, r):
    """completed 视图不应被最近 pending 任务遮挡。"""
    queue = "qtask_dash_test:completed:sparse"
    now = time.time()
    completed_id = "sparse-completed"
    r.hset(
        f"qtask:task:{completed_id}",
        mapping={
            "task_id": completed_id,
            "status": "completed",
            "action": "done_action",
            "created_at": str(now - 1000),
            "updated_at": str(now - 900),
        },
    )
    r.zadd(f"qtask:hist:{queue}", {completed_id: now - 900})

    pipe = r.pipeline()
    for index in range(320):
        task_id = f"sparse-pending-{index:03d}"
        pipe.hset(
            f"qtask:task:{task_id}",
            mapping={
                "task_id": task_id,
                "status": "pending",
                "action": "pending_action",
                "created_at": str(now - index),
            },
        )
        pipe.zadd(f"qtask:hist:{queue}", {task_id: now - index})
    pipe.execute()

    response = client.get(f"/api/queue/{queue}/tasks", params={"state": "completed", "limit": 1})

    assert response.status_code == 200
    assert response.json()["tasks"][0]["task_id"] == completed_id


def test_dashboard_completed_time_filter_scans_beyond_recent_completed(client, r):
    """completed 时间筛选不应先被较新的 completed 截断。"""
    queue = "qtask_dash_test:completed:time-sparse"
    now = time.time()
    target_id = "time-sparse-completed"
    r.hset(
        f"qtask:task:{target_id}",
        mapping={
            "task_id": target_id,
            "status": "completed",
            "action": "target_action",
            "created_at": str(now - 1000),
            "updated_at": str(now - 900),
        },
    )
    r.zadd(f"qtask:hist:{queue}", {target_id: now - 900})

    pipe = r.pipeline()
    for index in range(320):
        task_id = f"time-sparse-recent-{index:03d}"
        pipe.hset(
            f"qtask:task:{task_id}",
            mapping={
                "task_id": task_id,
                "status": "completed",
                "action": "recent_action",
                "created_at": str(now - index),
                "updated_at": str(now - index),
            },
        )
        pipe.zadd(f"qtask:hist:{queue}", {task_id: now - index})
    pipe.execute()

    response = client.get(
        f"/api/queue/{queue}/tasks",
        params={"state": "completed", "limit": 1, "created_before": now - 900},
    )

    assert response.status_code == 200
    assert response.json()["tasks"][0]["task_id"] == target_id


def test_dashboard_time_filter(client, r):
    """时间范围筛选任务。"""
    queue = "qtask_dash_test:timefilter"
    r.delete(queue)
    r.delete(f"qtask:hist:{queue}")

    task_id = "time-task-01"
    now = time.time()
    r.hset(f"qtask:task:{task_id}", mapping={
        "task_id": task_id, "status": "completed", "action": "time_action",
        "created_at": str(now - 200), "updated_at": str(now - 50),
    })
    r.zadd(f"qtask:hist:{queue}", {task_id: now - 50})

    response = client.get(
        f"/api/queue/{queue}/tasks",
        params={"state": "completed", "created_after": now - 300, "created_before": now - 100},
    )
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert any(t["task_id"] == task_id for t in tasks)

    response = client.get(
        f"/api/queue/{queue}/tasks",
        params={"state": "completed", "created_after": now - 50},
    )
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert not any(t["task_id"] == task_id for t in tasks)

    r.delete(f"qtask:task:{task_id}")
    r.delete(f"qtask:hist:{queue}")
