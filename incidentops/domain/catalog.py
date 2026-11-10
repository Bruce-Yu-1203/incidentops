"""Versioned service catalog: the only source of registered targets."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incidentops.domain.schemas import Environment, ResourceId, TypeId

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "service-catalog.yaml"


class UnknownServiceError(LookupError):
    """Raised when a service ID is not registered in the catalog."""


class ServiceKind(StrEnum):
    HTTP_API = "http_api"
    KAFKA_CONSUMER = "kafka_consumer"
    KAFKA_PRODUCER = "kafka_producer"
    KAFKA_BROKER = "kafka_broker"
    POSTGRES = "postgres"
    REDIS = "redis"
    LOAD_GENERATOR = "load_generator"


class KafkaBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]
    consumer_group: ResourceId | None = None


class ServiceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_id: ResourceId
    kind: ServiceKind
    environment: Environment
    description: Annotated[str, StringConstraints(max_length=500)]
    management_url: AnyHttpUrl | None = None
    kafka: KafkaBinding | None = None
    allowed_actions: tuple[TypeId, ...] = ()

    @model_validator(mode="after")
    def _actions_need_management_endpoint(self) -> Self:
        if self.allowed_actions and self.management_url is None:
            raise ValueError(f"{self.service_id}: actions require a management_url")
        if self.allowed_actions and self.environment is not Environment.FAULT_LAB:
            # NFR-011: remediation targets are restricted to the disposable lab.
            raise ValueError(f"{self.service_id}: actions are only allowed in fault-lab")
        return self


class ServiceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Annotated[int, Field(ge=1, le=1)]
    catalog_version: TypeId
    services: tuple[ServiceEntry, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [s.service_id for s in self.services]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate service ids: {sorted(duplicates)}")
        return self

    def get(self, service_id: str) -> ServiceEntry:
        for entry in self.services:
            if entry.service_id == service_id:
                return entry
        raise UnknownServiceError(service_id)

    def is_registered(self, service_id: str) -> bool:
        return any(s.service_id == service_id for s in self.services)

    def allows_action(self, service_id: str, action_type: str) -> bool:
        """True only for a registered target that lists the action explicitly."""
        if not self.is_registered(service_id):
            return False
        return action_type in self.get(service_id).allowed_actions

    def digest(self) -> str:
        """Content digest used to pin the catalog revision in audit records."""
        canonical = self.model_dump_json(exclude_none=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> ServiceCatalog:
    """Load and validate the catalog from YAML."""
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return ServiceCatalog.model_validate(raw)
