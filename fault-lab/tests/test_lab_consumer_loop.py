"""Unit tests for the supervised Kafka consume loop (no broker needed)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from fault_lab.consumer.app import Metrics, kafka_consume_loop
from fault_lab.consumer.controller import ConsumerController


@dataclass
class Message:
    value: bytes


def _batch(*event_ids: str) -> dict[Any, list[Any]]:
    return {"tp0": [Message(json.dumps({"event_id": e}).encode()) for e in event_ids]}


class Done(BaseException):
    """Ends a test run; a BaseException, so the supervisor does not catch it."""


@dataclass
class FakeConsumer:
    """Plays a script of ``getmany`` results; an exception entry is raised."""

    script: list[dict[Any, list[Any]] | BaseException]
    fail_commit: bool = False
    fail_start: int = 0
    started: int = 0
    stopped: int = 0
    commits: int = 0

    async def start(self) -> None:
        if self.fail_start:
            self.fail_start -= 1
            raise ConnectionError("broker unreachable")
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    def assignment(self) -> set[Any]:
        return {"tp0"}

    def pause(self, *partitions: Any) -> None:
        pass

    def resume(self, *partitions: Any) -> None:
        pass

    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]:
        if not self.script:
            raise Done
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("CommitFailedError: IllegalGenerationError")

    async def committed(self, partition: Any) -> int | None:
        return 0

    def seek(self, partition: Any, offset: int) -> None:
        pass


@dataclass
class Sink:
    stored: list[str] = field(default_factory=list)

    async def persist(self, records: list[dict[str, Any]]) -> None:
        self.stored.extend(r["event_id"] for r in records)


async def _no_sleep(_: float) -> None:
    await asyncio.sleep(0)


def _factory(consumers: list[FakeConsumer]) -> Any:
    queue = list(consumers)
    return lambda: queue.pop(0)


async def test_loop_restarts_with_a_new_consumer_after_a_failure(
    controller: ConsumerController,
) -> None:
    broken = FakeConsumer([_batch("e1"), RuntimeError("coordinator dead")])
    fresh = FakeConsumer([_batch("e2", "e3")])
    metrics, sink = Metrics(), Sink()

    with pytest.raises(Done):
        await kafka_consume_loop(
            controller, metrics, sink, factory=_factory([broken, fresh]), sleep=_no_sleep
        )

    assert sink.stored == ["e1", "e2", "e3"]
    assert broken.stopped == 1
    assert fresh.started == 1
    assert metrics.restarts._value.get() == 1
    assert metrics.processed._value.get() == 3


async def test_failed_commit_is_logged_and_consumption_continues(
    controller: ConsumerController,
) -> None:
    consumer = FakeConsumer([_batch("e1"), _batch("e2")], fail_commit=True)
    metrics, sink = Metrics(), Sink()

    with pytest.raises(Done):
        await kafka_consume_loop(
            controller, metrics, sink, factory=_factory([consumer]), sleep=_no_sleep
        )

    assert sink.stored == ["e1", "e2"]
    assert consumer.commits == 2
    assert metrics.restarts._value.get() == 0


async def test_start_is_retried_while_the_broker_is_unreachable(
    controller: ConsumerController,
) -> None:
    consumer = FakeConsumer([_batch("e1")], fail_start=3)
    sink = Sink()

    with pytest.raises(Done):
        await kafka_consume_loop(
            controller, Metrics(), sink, factory=_factory([consumer]), sleep=_no_sleep
        )

    assert consumer.started == 1
    assert sink.stored == ["e1"]


async def test_cancellation_propagates_without_restart(controller: ConsumerController) -> None:
    consumer = FakeConsumer([asyncio.CancelledError()])
    metrics = Metrics()

    with pytest.raises(asyncio.CancelledError):
        await kafka_consume_loop(
            controller, metrics, Sink(), factory=_factory([consumer]), sleep=_no_sleep
        )

    assert consumer.stopped == 1
    assert metrics.restarts._value.get() == 0


async def test_max_restarts_bounds_a_permanently_failing_loop(
    controller: ConsumerController,
) -> None:
    consumers = [FakeConsumer([RuntimeError("down")]) for _ in range(3)]

    with pytest.raises(RuntimeError, match="down"):
        await kafka_consume_loop(
            controller,
            Metrics(),
            Sink(),
            factory=_factory(consumers),
            sleep=_no_sleep,
            max_restarts=2,
        )

    assert all(c.stopped == 1 for c in consumers)
