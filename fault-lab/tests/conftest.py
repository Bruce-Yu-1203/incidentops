"""Shared fixtures for Fault Lab unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fault_lab.auth import LabAuthenticator, LabRole
from fault_lab.consumer.controller import ConsumerController

TOKENS = {
    LabRole.BENCHMARK: "benchmark-token-0123456789",
    LabRole.EXECUTOR: "executor-token-0123456789",
    LabRole.READER: "reader-token-0123456789",
}


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.sqlite3"


@pytest.fixture
def controller(ledger_path: Path) -> ConsumerController:
    return ConsumerController("persist-demo-consumer", ledger_path)


@pytest.fixture
def authenticator() -> LabAuthenticator:
    return LabAuthenticator(TOKENS)
