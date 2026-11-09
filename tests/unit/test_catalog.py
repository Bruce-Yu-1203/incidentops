"""Tests for the versioned service catalog."""

from __future__ import annotations

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
