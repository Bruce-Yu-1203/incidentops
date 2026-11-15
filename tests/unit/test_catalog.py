"""Tests for the versioned service catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from incidentops.domain.catalog import (
    ServiceCatalog,
    ServiceKind,
    UnknownServiceError,
    load_catalog,
)


def _raw(**service_overrides: Any) -> dict[str, Any]:
    service: dict[str, Any] = {
        "service_id": "persist-demo-consumer",
        "kind": "kafka_consumer",
        "environment": "fault-lab",
        "description": "test consumer",
        "management_url": "http://lab-consumer:8081",
        "allowed_actions": ["resume_test_consumer"],
    }
    service.update(service_overrides)
    return {"schema_version": 1, "catalog_version": "c1", "services": [service]}


def test_repository_catalog_loads() -> None:
    catalog = load_catalog()
    consumer = catalog.get("persist-demo-consumer")
    assert consumer.kind is ServiceKind.KAFKA_CONSUMER
    assert consumer.kafka is not None
    assert consumer.kafka.consumer_group == "persist-demo-consumer"


def test_only_consumer_can_be_resumed() -> None:
    catalog = load_catalog()
    assert catalog.allows_action("persist-demo-consumer", "resume_test_consumer")
    assert not catalog.allows_action("lab-api", "resume_test_consumer")
    assert not catalog.allows_action("persist-demo-consumer", "run_shell")
    assert not catalog.allows_action("unregistered", "resume_test_consumer")


def test_unknown_service_raises() -> None:
    with pytest.raises(UnknownServiceError):
        load_catalog().get("prod-db")


def test_actions_require_management_url() -> None:
    with pytest.raises(ValidationError, match="management_url"):
        ServiceCatalog.model_validate(_raw(management_url=None))


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_actions_are_lab_only(environment: str) -> None:
    with pytest.raises(ValidationError, match="fault-lab"):
        ServiceCatalog.model_validate(_raw(environment=environment))


def test_duplicate_service_ids_rejected() -> None:
    raw = _raw()
    raw["services"].append(dict(raw["services"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        ServiceCatalog.model_validate(raw)


def test_unknown_schema_version_rejected() -> None:
    raw = _raw()
    raw["schema_version"] = 2
    with pytest.raises(ValidationError):
        ServiceCatalog.model_validate(raw)


def test_digest_changes_with_content(tmp_path: Path) -> None:
    a = ServiceCatalog.model_validate(_raw())
    b = ServiceCatalog.model_validate(_raw(description="changed"))
    assert a.digest() != b.digest()
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(_raw()), encoding="utf-8")
    assert load_catalog(path).digest() == a.digest()


def _example_endpoint_overrides() -> dict[str, str]:
    """Parse INCIDENTOPS_ENDPOINT_OVERRIDES from the repository's .env.example."""
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    for line in env_example.read_text(encoding="utf-8").splitlines():
        if line.startswith("INCIDENTOPS_ENDPOINT_OVERRIDES="):
            raw = line.split("=", 1)[1].strip().strip("'")
            overrides: dict[str, str] = json.loads(raw)
            return overrides
    raise AssertionError("INCIDENTOPS_ENDPOINT_OVERRIDES missing from .env.example")


def test_every_compose_management_url_has_a_host_override() -> None:
    # Host-run workers cannot resolve compose service names, so a catalog
    # target without an override gets an unknown revision and is denied.
    overrides = _example_endpoint_overrides()
    missing = [
        service.service_id
        for service in load_catalog().services
        if service.management_url is not None
        and service.management_url.host not in {"localhost", "127.0.0.1", "::1"}
        and service.service_id not in overrides
    ]
    assert missing == []


# -- dependency relations (ADR-030) ---------------------------------------------


def _entry(service_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "kind": "http_api",
        "environment": "fault-lab",
        "description": service_id,
        **extra,
    }


def _catalog(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "catalog_version": "c1", "services": list(entries)}


def test_repository_catalog_dependency_edges() -> None:
    catalog = load_catalog()
    assert catalog.catalog_version == "c5"
    assert catalog.get("lab-api").depends_on == ("lab-postgres", "lab-redis", "lab-api-config")
    assert catalog.get("persist-demo-consumer").depends_on == ("lab-kafka", "lab-postgres")
    assert catalog.get("demo-producer").depends_on == ("lab-kafka",)
    assert catalog.get("lab-loadgen").depends_on == ("lab-api",)
    assert catalog.get("lab-cpu-load").co_located_with == ("lab-api",)
    for upstream in ("lab-postgres", "lab-redis", "lab-kafka", "lab-api-config"):
        assert catalog.get(upstream).depends_on == ()


def test_related_is_direct_and_undirected() -> None:
    catalog = load_catalog()
    assert catalog.related("lab-api", "lab-postgres")
    assert catalog.related("lab-postgres", "lab-api")
    assert catalog.related("lab-loadgen", "lab-api")
    assert catalog.related("lab-cpu-load", "lab-api")
    assert catalog.related("lab-api", "lab-cpu-load")
    # Only direct edges count: sharing a dependency is not a relation.
    assert not catalog.related("lab-api", "persist-demo-consumer")
    assert not catalog.related("lab-loadgen", "lab-postgres")
    assert not catalog.related("lab-api", "lab-api")
    assert not catalog.related("lab-api", "unregistered")


def test_upstream_rank_and_most_upstream() -> None:
    catalog = load_catalog()
    assert catalog.upstream_rank("lab-postgres") == 0
    assert catalog.upstream_rank("lab-api") == 1
    assert catalog.upstream_rank("lab-loadgen") == 2
    assert catalog.upstream_rank("lab-cpu-load") == 0
    assert catalog.most_upstream(["lab-api", "lab-postgres"]) == "lab-postgres"
    assert catalog.most_upstream(["persist-demo-consumer", "lab-kafka"]) == "lab-kafka"
    # Ties keep the first (the current primary).
    assert catalog.most_upstream(["lab-redis", "lab-postgres"]) == "lab-redis"
    with pytest.raises(ValueError, match="at least one"):
        catalog.most_upstream([])


def test_dependency_on_unknown_service_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown related service"):
        ServiceCatalog.model_validate(_catalog(_entry("a", depends_on=["ghost"])))


def test_co_location_with_unknown_service_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown related service"):
        ServiceCatalog.model_validate(_catalog(_entry("a", co_located_with=["ghost"])))


def test_self_reference_rejected() -> None:
    with pytest.raises(ValidationError, match="itself"):
        ServiceCatalog.model_validate(_catalog(_entry("a", depends_on=["a"])))


def test_dependency_across_environments_rejected() -> None:
    raw = _catalog(_entry("a", depends_on=["b"]), _entry("b", environment="staging"))
    with pytest.raises(ValidationError, match="another environment"):
        ServiceCatalog.model_validate(raw)


def test_dependency_cycle_rejected() -> None:
    raw = _catalog(
        _entry("a", depends_on=["b"]), _entry("b", depends_on=["c"]), _entry("c", depends_on=["a"])
    )
    with pytest.raises(ValidationError, match="dependency cycle"):
        ServiceCatalog.model_validate(raw)
