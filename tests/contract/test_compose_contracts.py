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


def test_only_controllers_receive_lab_control_tokens() -> None:
    holders = {
        name: {key for key in _env(service) if key.startswith("FAULT_LAB_")}
        for name, service in _services(LAB).items()
    }
    holders = {name: keys for name, keys in holders.items() if keys}
    assert set(holders) == {
        "lab-consumer",
        "fault-controller",
        "lab-api",
        "lab-kafka-proxy",
        "lab-loadgen",
    }
    # The API, the Kafka proxy and the load generator only verify the internal
    # control token; they hold no role token.
    for name in ("lab-api", "lab-kafka-proxy", "lab-loadgen"):
        assert holders[name] == {"FAULT_LAB_INTERNAL_TOKEN"}


def test_lab_dockerfile_installs_from_hashed_lock() -> None:
    dockerfile = (REPO_ROOT / "fault-lab" / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert "@sha256:" in dockerfile.splitlines()[2] or "@sha256:" in dockerfile


def test_lab_api_returns_leaked_memory_between_trials() -> None:
    env = _env(_services(LAB)["lab-api"])
    assert env["MALLOC_MMAP_THRESHOLD_"] == "131072"


def test_kafka_clients_export_logs_for_search_logs() -> None:
    # search_logs covers kafka_consumer and kafka_producer services (ADR-024).
    services = _services(LAB)
    for name in ("lab-api", "lab-consumer", "demo-producer"):
        assert _env(services[name])["OTEL_EXPORTER_OTLP_ENDPOINT"] == ("http://otel-collector:4318")


def test_kafka_clients_reach_the_broker_only_through_the_proxy() -> None:
    services = _services(LAB)
    broker = _env(services["lab-kafka"])
    assert broker["KAFKA_ADVERTISED_LISTENERS"].startswith("CLIENT://lab-kafka-proxy:9092,")
    for name in ("demo-producer", "lab-consumer"):
        assert _env(services[name])["LAB_KAFKA_BOOTSTRAP"] == "lab-kafka-proxy:9092"
    exporter = _services(REPO_ROOT / "infra" / "compose.yaml")["kafka-exporter"]
    assert exporter["command"] == ["--kafka.server=lab-kafka-proxy:9092"]
    # The proxy's state is ground truth: it is never scraped.
    prometheus = (REPO_ROOT / "infra" / "prometheus" / "prometheus.yml").read_text("utf-8")
    assert "lab-kafka-proxy" not in prometheus


def test_api_uses_an_ordinary_role_and_monitoring_keeps_reserved_slots() -> None:
    # B-F12: idle connections can exhaust the ordinary slots the API needs,
    # while the exporter and superusers keep theirs.
    services = _services(LAB)
    assert _env(services["lab-api"])["LAB_DATABASE_DSN"].startswith("postgresql://lab_app:")
    command = " ".join(services["lab-postgres"]["command"])
    assert "reserved_connections=3" in command
    init = (REPO_ROOT / "fault-lab" / "postgres" / "init.sql").read_text(encoding="utf-8")
    assert "pg_use_reserved_connections" in init
    assert "CREATE ROLE lab_app LOGIN" in init
    assert "SUPERUSER" not in init.split("CREATE ROLE lab_app", 1)[1].split(";", 1)[0]


def test_lab_roles_are_bootstrapped_on_every_start() -> None:
    # Compose keeps lab-postgres' data volume across recreation, so initdb
    # scripts alone would never create roles added later (lab_app).
    services = _services(LAB)
    bootstrap = services["lab-db-bootstrap"]
    assert bootstrap["image"] == services["lab-postgres"]["image"]
    assert "./postgres/init.sql:/bootstrap/init.sql:ro" in bootstrap["volumes"]
    assert services["lab-api"]["depends_on"]["lab-db-bootstrap"] == {
        "condition": "service_completed_successfully"
    }
    init = (REPO_ROOT / "fault-lab" / "postgres" / "init.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE lab_app" in init
    # Idempotent: every CREATE ROLE is guarded.
    assert init.count("CREATE ROLE") == init.count("IF NOT EXISTS (SELECT FROM pg_roles")
