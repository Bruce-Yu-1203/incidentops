"""ADR-014 import boundaries: the control plane cannot reach fault-lab code."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "incidentops"
SOURCES = sorted(PACKAGE.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_control_plane_never_imports_fault_lab(path: Path) -> None:
    offending = {name for name in _imports(path) if name.split(".")[0] == "fault_lab"}
    assert not offending, f"{path.name} imports {offending}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_control_plane_never_references_benchmark_token(path: Path) -> None:
    # Fault-injection credentials belong to the benchmark controller only.
    text = path.read_text(encoding="utf-8")
    assert "FAULT_LAB_BENCHMARK_TOKEN" not in text
    assert "FAULT_LAB_INTERNAL_TOKEN" not in text
