"""Tests for the local .env generator."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_env", REPO_ROOT / "scripts" / "generate_env.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_replaces_every_placeholder_with_unique_tokens() -> None:
    rendered = _load().render((REPO_ROOT / ".env.example").read_text(encoding="utf-8"))
    assert "change-me" not in rendered
    env = dict(re.findall(r"^(\w+_TOKEN)=(\S+)$", rendered, re.M))
    assert len(env) >= 8
    # The agent's lab read token must match the token the lab verifies.
    assert env["INCIDENTOPS_LAB_READ_TOKEN"] == env["FAULT_LAB_READ_TOKEN"]
    others = [v for k, v in env.items() if k != "INCIDENTOPS_LAB_READ_TOKEN"]
    assert len(set(others)) == len(others)
    assert "incidentops:incidentops@localhost:5433" in rendered


def test_main_never_overwrites(tmp_path: Path) -> None:
    module = _load()
    (tmp_path / ".env.example").write_text("A_TOKEN=change-me-a\n", encoding="utf-8")
    (tmp_path / ".env").write_text("keep\n", encoding="utf-8")
    assert module.main(tmp_path) == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "keep\n"


def test_main_writes_private_file(tmp_path: Path) -> None:
    module = _load()
    (tmp_path / ".env.example").write_text(
        "DB=postgresql://incidentops:change-me@x\nA_TOKEN=change-me-a\n", encoding="utf-8"
    )
    assert module.main(tmp_path) == 0
    env = tmp_path / ".env"
    assert env.stat().st_mode & 0o777 == 0o600
    assert "change-me" not in env.read_text(encoding="utf-8")
