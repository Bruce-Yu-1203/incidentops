"""Static contracts for the compose topologies (credential and isolation rules)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB = REPO_ROOT / "fault-lab" / "compose.yaml"
CONTROL = REPO_ROOT / "compose.yaml"


def _services(path: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(data["services"])


def _env(service: dict[str, Any]) -> dict[str, Any]:
    env = service.get("environment") or {}
    return dict(env) if isinstance(env, dict) else {}


def test_control_and_lab_databases_are_separate() -> None:
    assert "control-postgres" in _services(CONTROL)
    assert "control-postgres" not in _services(LAB)
    assert "lab-postgres" in _services(LAB)


@pytest.mark.parametrize("path", [LAB, CONTROL], ids=lambda p: p.parent.name)
def test_no_service_mounts_docker_socket(path: Path) -> None:
    for name, service in _services(path).items():
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume), f"{name} mounts the Docker socket"


@pytest.mark.parametrize("path", [LAB, CONTROL], ids=lambda p: p.parent.name)
def test_published_ports_bind_to_loopback(path: Path) -> None:
    for name, service in _services(path).items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} exposes {port} publicly"


def test_lab_third_party_images_are_digest_pinned() -> None:
    for name, service in _services(LAB).items():
        if "image" in service:
            assert "@sha256:" in service["image"], f"{name} image is not digest-pinned"


def test_only_consumer_receives_lab_control_tokens() -> None:
    holders = {
        name
        for name, service in _services(LAB).items()
        if any(key.startswith("FAULT_LAB_") for key in _env(service))
    }
    assert holders == {"lab-consumer", "fault-controller"}


def test_lab_dockerfile_installs_from_hashed_lock() -> None:
    dockerfile = (REPO_ROOT / "fault-lab" / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert "@sha256:" in dockerfile.splitlines()[2] or "@sha256:" in dockerfile
