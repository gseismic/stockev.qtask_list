"""时钟抽象。

核心对象只依赖这个小接口，便于测试 deadline、保留期和退避而不真实 sleep。
Redis 原子状态转换仍优先使用 Redis TIME，避免不同进程的系统时钟偏差。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """队列内部时钟契约，提供 epoch 秒和可替换的等待能力。"""

    def now(self) -> float:
        """返回当前 UTC epoch 秒。"""

    def sleep(self, seconds: float) -> None:
        """等待指定秒数。"""


class SystemClock:
    """生产环境使用的系统时钟。"""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FrozenClock:
    """可推进的测试时钟，避免依赖真实等待。"""

    def __init__(self, initial: float):
        self._value = float(initial)
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._value

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> float:
        """推进测试时钟并返回新值。"""
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        with self._lock:
            self._value += seconds
            return self._value


def datetime_to_epoch(value: datetime | None) -> float | None:
    """把带时区 datetime 转为 UTC epoch；None 原样返回。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.timestamp()


def epoch_to_datetime(value: float | int | str | None) -> datetime | None:
    """把 epoch 转为 UTC datetime；空值返回 None。"""
    if value is None or value == "":
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)
