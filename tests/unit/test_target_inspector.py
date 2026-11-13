"""Tests for the read-only target revision inspector."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from incidentops.actions.targets import ConsumerTargetInspector
from incidentops.domain.catalog import load_catalog


class _Backend:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def controller_get(self, service_id: str, management_url: str, path: str) -> Any:
        if self._error is not None:
            raise self._error
        return {"target_id": service_id, "revision": "load-gen-1"}


async def test_controller_target_revision_is_read() -> None:
    inspector = ConsumerTargetInspector(load_catalog(), _Backend())  # type: ignore[arg-type]
    assert await inspector.inspect("lab-loadgen") == {"revision": "load-gen-1"}


async def test_failed_revision_read_returns_none_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = _Backend(ConnectionError("name resolution failed"))
    inspector = ConsumerTargetInspector(load_catalog(), backend)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="incidentops.actions.targets"):
        assert await inspector.inspect("lab-loadgen") is None
    assert "target revision read failed for lab-loadgen: ConnectionError" in caplog.text


async def test_unknown_target_returns_none() -> None:
    inspector = ConsumerTargetInspector(load_catalog(), _Backend())  # type: ignore[arg-type]
    assert await inspector.inspect("not-a-service") is None
