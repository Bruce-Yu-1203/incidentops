"""Versioned service catalog: the only source of registered targets."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
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
    CONFIG_STORE = "config_store"


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
    # Services this one calls or uses and fails with ("A depends on B": B is
    # upstream of A). Used to group incidents of one fault (ADR-030).
    depends_on: tuple[ResourceId, ...] = ()
    # Services sharing this one's runtime (e.g. a load generator inside a
    # process): related for grouping, with no upstream/downstream direction.
    co_located_with: tuple[ResourceId, ...] = ()

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

    @model_validator(mode="after")
    def _valid_relations(self) -> Self:
        by_id = {s.service_id: s for s in self.services}
        for entry in self.services:
            for other in (*entry.depends_on, *entry.co_located_with):
                if other == entry.service_id:
                    raise ValueError(f"{entry.service_id}: a service cannot relate to itself")
                if other not in by_id:
                    raise ValueError(f"{entry.service_id}: unknown related service {other}")
                if by_id[other].environment is not entry.environment:
                    raise ValueError(
                        f"{entry.service_id}: related service {other} is in another environment"
                    )
        # depends_on must be acyclic, or "most upstream" has no meaning.
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(service_id: str, path: tuple[str, ...]) -> None:
            if service_id in done:
                return
            if service_id in visiting:
                raise ValueError(f"dependency cycle: {' -> '.join((*path, service_id))}")
            visiting.add(service_id)
            for dependency in by_id[service_id].depends_on:
                visit(dependency, (*path, service_id))
            visiting.discard(service_id)
            done.add(service_id)

        for entry in self.services:
            visit(entry.service_id, ())
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

    def related(self, a: str, b: str) -> bool:
        """True when ``a`` and ``b`` share a direct edge (either direction or co-located).

        Grouping treats the graph as undirected: a client can overload its
        dependency (lab-loadgen -> lab-api), so direction never decides whether
        two incidents belong together, only which one is the primary.
        """
        if a == b or not (self.is_registered(a) and self.is_registered(b)):
            return False
        first, second = self.get(a), self.get(b)
        return (
            b in first.depends_on
            or a in second.depends_on
            or b in first.co_located_with
            or a in second.co_located_with
        )

    def upstream_rank(self, service_id: str) -> int:
        """0 for a service without dependencies, else 1 + the deepest dependency's rank.

        A lower rank is more upstream; the root cause of a group is usually its
        most upstream member.
        """
        entry = self.get(service_id)
        if not entry.depends_on:
            return 0
        return 1 + max(self.upstream_rank(d) for d in entry.depends_on)

    def most_upstream(self, service_ids: Iterable[str]) -> str:
        """The lowest-ranked service; ties keep the first given (the current primary)."""
        ordered = list(service_ids)
        if not ordered:
            raise ValueError("most_upstream needs at least one service")
        return min(ordered, key=self.upstream_rank)

    def digest(self) -> str:
        """Content digest used to pin the catalog revision in audit records."""
        canonical = self.model_dump_json(exclude_none=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> ServiceCatalog:
    """Load and validate the catalog from YAML."""
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return ServiceCatalog.model_validate(raw)
