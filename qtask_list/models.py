"""qtask_list V2 的公共数据模型。"""

from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence, TypeAlias, Union

from .clock import datetime_to_epoch

JsonScalar: TypeAlias = Union[None, bool, int, float, str]
JsonValue: TypeAlias = Union[JsonScalar, list["JsonValue"], dict[str, "JsonValue"]]
EnqueueReason: TypeAlias = Literal[
    "enqueued",
    "duplicate_active",
    "duplicate_retained",
    "superseded",
]


class DuplicateAction(str, Enum):
    """重复 logical key 的显式处理策略。"""

    REJECT = "reject"
    ALLOW_NEW = "allow_new"


class IdentityPolicy(str, Enum):
    """清理 operational message 时对 logical identity 的处理策略。"""

    KEEP = "keep"
    RELEASE = "release"


class HistoryMode(str, Enum):
    """历史记录级别；minimal 仍保留状态机正确性所需字段。"""

    FULL = "full"
    MINIMAL = "minimal"


class TaskOutcome(str, Enum):
    """不可变生命周期结果；空字符串表示尚未进入终态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskSpec:
    """一次任务实例的完整用户意图。

    action 位于信封头，payload 只包含业务数据。身份、调度和执行控制字段彼此独立，
    避免调用方通过 payload 中的私有键间接影响队列状态机。
    """

    action: str
    payload: Mapping[str, JsonValue]
    logical_key: str | None = None
    scheduled_for: datetime | None = None
    not_before_at: datetime | None = None
    start_deadline_at: datetime | None = None
    dedup_until: datetime | None = None
    trace_id: str | None = None
    parent_task_id: str | None = None
    concurrency_key: str | None = None
    supersede_key: str | None = None
    supersede_version: int | str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action.strip():
            raise ValueError("action must be a non-empty string")
        if len(self.action.encode("utf-8")) > 256:
            raise ValueError("action must not exceed 256 UTF-8 bytes")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")

        if self.logical_key is not None:
            if not isinstance(self.logical_key, str) or not self.logical_key:
                raise ValueError("logical_key must be None or a non-empty string")
            if len(self.logical_key.encode("utf-8")) > 512:
                raise ValueError("logical_key must not exceed 512 UTF-8 bytes")
        if self.dedup_until is not None and self.logical_key is None:
            raise ValueError("dedup_until requires logical_key")

        for name in (
            "scheduled_for",
            "not_before_at",
            "start_deadline_at",
            "dedup_until",
        ):
            value = getattr(self, name)
            if value is not None:
                try:
                    datetime_to_epoch(value)
                except ValueError as exc:
                    raise ValueError(f"{name} must be timezone-aware") from exc

        not_before = datetime_to_epoch(self.not_before_at)
        deadline = datetime_to_epoch(self.start_deadline_at)
        dedup_until = datetime_to_epoch(self.dedup_until)
        if not_before is not None and deadline is not None and not_before > deadline:
            raise ValueError("not_before_at must not be later than start_deadline_at")
        if dedup_until is not None and deadline is not None and dedup_until < deadline:
            warnings.warn(
                "dedup_until is earlier than start_deadline_at; live identity is still retained",
                UserWarning,
                stacklevel=2,
            )

        has_supersede_key = self.supersede_key is not None
        has_supersede_version = self.supersede_version is not None
        if has_supersede_key != has_supersede_version:
            raise ValueError("supersede_key and supersede_version must be provided together")
        if self.supersede_key == "":
            raise ValueError("supersede_key must not be empty")
        if isinstance(self.supersede_version, bool):
            raise TypeError("supersede_version must be int or str, not bool")
        if self.concurrency_key == "":
            raise ValueError("concurrency_key must not be empty")


@dataclass(frozen=True)
class EnqueueResult:
    """结构化投递结果，明确区分接受、活跃重复和保留期重复。"""

    accepted: bool
    task_id: str | None
    logical_key: str | None
    duplicate_of: str | None
    reason: EnqueueReason

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "task_id": self.task_id,
            "logical_key": self.logical_key,
            "duplicate_of": self.duplicate_of,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TaskContext:
    """传给双参数 handler 的只读执行元数据。"""

    task_id: str
    action: str
    logical_key: str | None
    attempt: int
    max_attempts: int
    scheduled_for: datetime | None
    start_deadline_at: datetime | None
    trace_id: str | None
    parent_task_id: str | None
    replay_of: str | None
    worker_id: str | None
    _stop_event: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
        compare=False,
    )

    @property
    def stop_requested(self) -> bool:
        """Worker 是否请求 handler 协作式停止。"""
        return self._stop_event.is_set()

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        """等待停止请求，适合长循环 handler 定期检查。"""
        return self._stop_event.wait(timeout)


@dataclass(frozen=True)
class TaskResult:
    """handler 结果及需要投递到下游的带身份任务。"""

    value: JsonValue | Mapping[str, JsonValue] | None = None
    emissions: Sequence[TaskSpec] = ()

    def __post_init__(self) -> None:
        # tuple 防止调用方在 handler 返回后继续改动 emission 集合。
        object.__setattr__(self, "emissions", tuple(self.emissions))


def readonly_payload(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """提供只读浅层视图，供需要强调不可变输入的集成使用。"""
    return MappingProxyType(dict(payload))
