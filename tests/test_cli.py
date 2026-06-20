import json

import pytest
import redis
from typer.testing import CliRunner

from cli.__main__ import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def r():
    client = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    cleanup_test_keys(client)
    yield client
    cleanup_test_keys(client)


def cleanup_test_keys(client):
    for hist_key in client.scan_iter("qtask:hist:stockev_list:*"):
        try:
            for task_id in client.zrange(hist_key, 0, -1):
                client.delete(f"qtask:task:{task_id}")
        finally:
            client.delete(hist_key)
    for hist_key in client.scan_iter("qtask:hist:testns:*"):
        try:
            for task_id in client.zrange(hist_key, 0, -1):
                client.delete(f"qtask:task:{task_id}")
        finally:
            client.delete(hist_key)
    for task_id in ["abc123", "clean1", "test1"]:
        client.delete(f"qtask:task:{task_id}")
    for key in client.scan_iter("stockev_list:*"):
        client.delete(key)
    for key in client.scan_iter("testns:*"):
        client.delete(key)


def make_msg(task_id: str, payload: dict | None = None) -> str:
    return json.dumps({"task_id": task_id, "payload": json.dumps(payload or {})})


class TestCLI:
    def test_status_empty(self, runner, r):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_with_queue(self, runner, r):
        r.lpush("stockev_list:test", "test")

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "stockev_list:test" in result.stdout

    def test_status_specific_queue_with_namespace(self, runner, r):
        r.lpush("stockev_list:specific", "test")

        result = runner.invoke(app, ["status", "specific", "-n", "stockev_list"])
        assert result.exit_code == 0
        assert "stockev_list:specific" in result.stdout

    def test_push_and_peek_ready_task(self, runner, r):
        result = runner.invoke(
            app,
            ["push", "stockev_list:push_test", '{"action":"fetch","symbol":"AAPL"}'],
        )

        assert result.exit_code == 0
        assert r.llen("stockev_list:push_test") == 1

        raw = r.lindex("stockev_list:push_test", 0)
        msg = json.loads(raw)
        payload = json.loads(msg["payload"])
        assert payload == {"action": "fetch", "symbol": "AAPL"}
        assert r.exists(f"qtask:task:{msg['task_id']}") == 1

        peek_result = runner.invoke(app, ["peek", "stockev_list:push_test", "--json"])
        assert peek_result.exit_code == 0
        assert "fetch" in peek_result.stdout
        assert "AAPL" in peek_result.stdout

    def test_clear_queue_can_include_history(self, runner, r):
        push_result = runner.invoke(
            app,
            ["push", "stockev_list:clear_test", '{"action":"clear_me"}'],
        )
        assert push_result.exit_code == 0
        assert r.exists("qtask:hist:stockev_list:clear_test") == 1

        result = runner.invoke(
            app,
            ["clear", "stockev_list:clear_test", "--include-history", "--force"],
        )

        assert result.exit_code == 0
        assert "Cleared" in result.stdout
        assert r.exists("stockev_list:clear_test") == 0
        assert r.exists("qtask:hist:stockev_list:clear_test") == 0

    def test_requeue_moves_dlq_to_ready(self, runner, r):
        r.lpush("stockev_list:dlq_test:dlq", make_msg("dlq-1"))
        r.lpush("stockev_list:dlq_test:dlq", make_msg("dlq-2"))

        result = runner.invoke(app, ["requeue", "stockev_list:dlq_test", "--force"])

        assert result.exit_code == 0
        assert r.llen("stockev_list:dlq_test:dlq") == 0
        assert r.llen("stockev_list:dlq_test") == 2

    def test_requeue_single_task_from_dlq(self, runner, r):
        r.lpush("stockev_list:single_dlq:dlq", make_msg("dlq-keep"))
        r.lpush("stockev_list:single_dlq:dlq", make_msg("dlq-move"))

        result = runner.invoke(
            app,
            ["requeue", "stockev_list:single_dlq", "--task-id", "dlq-move", "--force"],
        )

        assert result.exit_code == 0
        assert r.llen("stockev_list:single_dlq") == 1
        assert r.llen("stockev_list:single_dlq:dlq") == 1
        assert message_ids(r, "stockev_list:single_dlq") == ["dlq-move"]
        assert message_ids(r, "stockev_list:single_dlq:dlq") == ["dlq-keep"]

    def test_retry_moves_retry_to_ready(self, runner, r):
        r.lpush("stockev_list:retry_test:retry", make_msg("retry-1"))

        result = runner.invoke(app, ["retry", "stockev_list:retry_test"])

        assert result.exit_code == 0
        assert r.llen("stockev_list:retry_test:retry") == 0
        assert r.llen("stockev_list:retry_test") == 1

    def test_recover_skips_active_worker_processing_by_default(self, runner, r):
        queue = "stockev_list:proc_test"
        r.lpush(f"{queue}:processing", make_msg("legacy"))
        r.lpush(f"{queue}:processing:active", make_msg("active"))
        r.lpush(f"{queue}:processing:stale", make_msg("stale"))
        r.set(f"{queue}:worker:active", "1", ex=60)

        result = runner.invoke(app, ["recover", queue])

        assert result.exit_code == 0
        assert "Skipped 1 active" in result.stdout
        assert r.llen(queue) == 2
        assert r.llen(f"{queue}:processing") == 0
        assert r.llen(f"{queue}:processing:stale") == 0
        assert r.llen(f"{queue}:processing:active") == 1

        forced = runner.invoke(app, ["recover", queue, "--force-active"])

        assert forced.exit_code == 0
        assert r.llen(queue) == 3
        assert r.llen(f"{queue}:processing:active") == 0

    def test_history_can_get_task_without_queue_name(self, runner, r):
        r.hset("qtask:task:abc123", mapping={"task_id": "abc123", "action": "test"})

        result = runner.invoke(app, ["history", "-t", "abc123"])

        assert result.exit_code == 0
        assert "abc123" in result.stdout

    def test_task_get_and_delete(self, runner, r):
        push_result = runner.invoke(
            app,
            ["push", "stockev_list:task_delete", '{"action":"delete_me"}'],
        )
        assert push_result.exit_code == 0
        raw = r.lindex("stockev_list:task_delete", 0)
        task_id = json.loads(raw)["task_id"]

        get_result = runner.invoke(app, ["task", "get", task_id])
        assert get_result.exit_code == 0
        assert "delete_me" in get_result.stdout

        delete_result = runner.invoke(
            app,
            ["task", "delete", task_id, "--queue", "stockev_list:task_delete", "--force"],
        )

        assert delete_result.exit_code == 0
        assert r.llen("stockev_list:task_delete") == 0
        assert r.exists(f"qtask:task:{task_id}") == 0
        assert r.zscore("qtask:hist:stockev_list:task_delete", task_id) is None

    def test_task_requeue_moves_single_task_from_retry(self, runner, r):
        r.lpush("stockev_list:task_requeue:retry", make_msg("retry-keep"))
        r.lpush("stockev_list:task_requeue:retry", make_msg("retry-move"))

        result = runner.invoke(
            app,
            [
                "task",
                "requeue",
                "retry-move",
                "--queue",
                "stockev_list:task_requeue",
                "--from",
                "retry",
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert message_ids(r, "stockev_list:task_requeue") == ["retry-move"]
        assert message_ids(r, "stockev_list:task_requeue:retry") == ["retry-keep"]


def message_ids(client, key: str) -> list[str]:
    return [json.loads(raw)["task_id"] for raw in client.lrange(key, 0, -1)]


class TestCLIWatch:
    def test_watch_command(self, runner, r, monkeypatch):
        r.lpush("stockev_list:watch_test", "test")

        def stop_after_first_sleep(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr("cli.__main__.time.sleep", stop_after_first_sleep)
        result = runner.invoke(
            app,
            ["watch", "stockev_list:watch_test", "-i", "1"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "Watching" in result.stdout


class TestCLIWorker:
    def test_worker_missing_qtask_list(self, runner, r, monkeypatch):
        import cli.__main__

        monkeypatch.setattr(cli.__main__, "QTASK_LIST_AVAILABLE", False)

        result = runner.invoke(app, ["worker", "-q", "test", "-n", "testns"])
        assert result.exit_code != 0
        assert "not installed" in result.stdout or "Error" in result.stdout

    def test_worker_without_module_does_not_start_generic_worker(self, runner, r):
        result = runner.invoke(app, ["worker", "-q", "test", "-n", "testns"])

        assert result.exit_code != 0
        assert "no registered handlers" in result.stdout


class TestCLICleanHistory:
    def test_clean_history(self, runner, r):
        r.set("qtask:task:clean1", '{"task_id":"clean1","action":"test"}')
        r.zadd("qtask:hist:stockev_list:clean_test", {"clean1": 1})

        result = runner.invoke(app, ["clean-history", "stockev_list:clean_test"])
        assert result.exit_code == 0
