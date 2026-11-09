"""The fault-lab container lockfile must match uv.lock."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_requirements_lock_is_up_to_date() -> None:
    exported = subprocess.run(
        [  # noqa: S607 - uv is resolved from PATH on developer machines and CI
            "uv",
            "export",
            "--package",
            "incidentops-fault-lab",
            "--no-dev",
            "--frozen",
            "--no-emit-workspace",
            "--no-header",
            "-q",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    committed = (REPO_ROOT / "fault-lab" / "requirements.lock").read_text(encoding="utf-8")
    assert exported == committed, "run `make lab-lock` to refresh fault-lab/requirements.lock"
