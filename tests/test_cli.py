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
    yield client
    keys = client.keys("testns:*")
    for k in keys:
        client.delete(k)
    keys = client.keys("stockev_list:*")
    for k in keys:
        client.delete(k)


class TestCLI:

    def test_status_empty(self, runner, r):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_with_queue(self, runner, r):
        # 创建测试队列
        r.lpush("stockev_list:test", "test")
        
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "stockev_list:test" in result.stdout

    def test_status_specific_queue(self, runner, r):
        r.lpush("stockev_list:specific", "test")
        
        result = runner.invoke(app, ["status", "stockev_list:specific"])
        assert result.exit_code == 0
        assert "Queue: stockev_list:specific" in result.stdout

    def test_clear_queue(self, runner, r):
        r.lpush("stockev_list:clear_test", "test1")
        r.lpush("stockev_list:clear_test", "test2")
        
        result = runner.invoke(app, ["clear", "stockev_list:clear_test", "--force"])
        assert result.exit_code == 0
        assert "Cleared" in result.stdout

    def test_requeue(self, runner, r):
        # 添加 DLQ 任务
        r.lpush("stockev_list:dlq_test", '{"task_id":"test","payload":"{}"}')
        
        result = runner.invoke(app, ["requeue", "stockev_list:dlq_test", "--force"])
        assert result.exit_code == 0

    def test_retry(self, runner, r):
        # 添加 retry 任务
        r.lpush("stockev_list:retry_test", '{"task_id":"test","payload":"{}"}')
        
        result = runner.invoke(app, ["retry", "stockev_list:retry_test"])
        assert result.exit_code == 0

    def test_recover(self, runner, r):
        # 添加 processing 任务
        r.lpush("stockev_list:proc_test", "test")
        
        result = runner.invoke(app, ["recover", "stockev_list:proc_test"])
        assert result.exit_code == 0

    def test_history(self, runner, r):
        # 添加历史记录
        r.set("qtask:task:test1", '{"task_id":"test1","action":"test","status":"completed"}')
        r.zadd("qtask:hist:stockev_list:history_test", {"test1": 1000})
        
        result = runner.invoke(app, ["history", "stockev_list:history_test"])
        assert result.exit_code == 0

    def test_history_with_task_id(self, runner, r):
        r.set("qtask:task:abc123", '{"task_id":"abc123","action":"test"}')
        r.zadd("qtask:hist:stockev_list:task_test", {"abc123": 1000})
        
        result = runner.invoke(app, ["history", "stockev_list:task_test", "-t", "abc123"])
        assert result.exit_code == 0


class TestCLIWatch:

    def test_watch_command(self, runner, r):
        r.lpush("stockev_list:watch_test", "test")
        
        # watch 命令会阻塞，使用 timeout
        result = runner.invoke(app, ["watch", "stockev_list:watch_test", "-i", "1"], catch_exceptions=False)
        # 超时会返回非0
        assert "Watching" in result.stdout or result.exit_code != 0


class TestCLIWorker:

    def test_worker_missing_qtask_list(self, runner, r, monkeypatch):
        # 模拟 qtask_list 未安装
        import cli.__main__
        monkeypatch.setattr(cli.__main__, "QTASK_LIST_AVAILABLE", False)
        
        result = runner.invoke(app, ["worker", "-q", "test", "-n", "testns"])
        assert result.exit_code != 0
        assert "not installed" in result.stdout or "Error" in result.stdout


class TestCLICleanHistory:

    def test_clean_history(self, runner, r):
        # 添加历史记录
        r.set("qtask:task:clean1", '{"task_id":"clean1","action":"test"}')
        r.zadd("qtask:hist:stockev_list:clean_test", {"clean1": 1})  # 旧时间戳
        
        result = runner.invoke(app, ["clean-history", "stockev_list:clean_test"])
        assert result.exit_code == 0
