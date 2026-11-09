"""Structural checks for architecture decision records."""

from pathlib import Path

import pytest

ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "adr"
ADR_FILES = sorted(ADR_DIR.glob("ADR-*.md"))


def test_adrs_exist() -> None:
    assert ADR_FILES, "expected at least one ADR"


@pytest.mark.parametrize("path", ADR_FILES, ids=lambda p: p.name)
def test_adr_has_status_and_decision(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith(f"# {path.name[:7]}:"), "title must start with the ADR id"
    assert "**Status:**" in text
    assert "## Decision" in text


@pytest.mark.parametrize("path", ADR_FILES, ids=lambda p: p.name)
def test_adr_is_indexed(path: Path) -> None:
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    assert f"({path.name})" in index
