"""The one-command demo script must stay valid and self-documenting."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "demo.sh"


def test_script_is_executable_bash() -> None:
    assert SCRIPT.stat().st_mode & 0o111
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_help_lists_commands() -> None:
    output = subprocess.run(
        [str(SCRIPT), "help"], check=True, capture_output=True, text=True
    ).stdout
    for command in ("up", "status", "down"):
        assert f"scripts/demo.sh {command}" in output


def test_every_process_is_started_through_a_scoped_make_target() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("api-run", "control-run", "executor-run", "verifier-run", "worker-run"):
        assert f'echo "{target}"' in text
        assert f"\n{target}:" in makefile


def test_worker_defaults_to_the_no_key_baseline() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "INCIDENTOPS_MODEL_ID:-rules-baseline-v1" in makefile
