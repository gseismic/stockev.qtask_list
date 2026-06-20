import json

import pytest
import redis
from fastapi.testclient import TestClient

from dashboard.main import app


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
    for key in client.scan_iter("qtask_dash_test:*"):
        client.delete(key)


def make_msg(task_id: str, payload: dict | None = None) -> str:
    return json.dumps({"task_id": task_id, "payload": json.dumps(payload or {})})


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
