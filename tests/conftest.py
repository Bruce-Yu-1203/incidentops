"""Repository-wide test fixtures."""

from __future__ import annotations

import pytest

from incidentops.config import Settings


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep developer shell variables (e.g. an exported .env) out of Settings."""
    for name in Settings.model_fields:
        monkeypatch.delenv(f"INCIDENTOPS_{name.upper()}", raising=False)
    # Tests drive one incident straight into investigation; the grouping
    # settle wait (ADR-030) is exercised explicitly in the grouping tests.
    monkeypatch.setenv("INCIDENTOPS_GROUP_SETTLE_SECONDS", "0")
