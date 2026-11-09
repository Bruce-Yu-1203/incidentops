"""Immutable action envelope and canonical action hash (architecture §9.2)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from incidentops.domain.schemas import (
    ArgumentValue,
    AwareDatetime,
    Environment,
    RecordId,
    ResourceId,
    TypeId,
)

ACTION_ENVELOPE_SCHEMA_VERSION: Final = 1

# Fields bound by the hash. Changing any of them invalidates an approval.
HASHED_FIELDS: Final = (
    "schema_version",
    "action_id",
    "action_type",
    "environment",
    "target_id",
    "target_revision",
    "arguments",
    "policy_version",
    "expires_at",
)


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, no NaN."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class ActionEnvelope(BaseModel):
    """The exact, immutable thing a human approves.

    The envelope is frozen; any change produces a different ``action_hash`` and
    therefore requires a new approval.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Annotated[int, Field(ge=1)] = ACTION_ENVELOPE_SCHEMA_VERSION
    action_id: RecordId
    action_type: TypeId
    environment: Environment
    target_id: ResourceId
    target_revision: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:._-]{1,128}$")]
    arguments: dict[TypeId, ArgumentValue]
    requested_by: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    evidence_ids: tuple[RecordId, ...]
    policy_version: TypeId
    expires_at: AwareDatetime

    def hash_payload(self) -> dict[str, Any]:
        """The subset of fields covered by the hash, in canonical form."""
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "environment": self.environment.value,
            "target_id": self.target_id,
            "target_revision": self.target_revision,
            "arguments": dict(self.arguments),
            "policy_version": self.policy_version,
            "expires_at": _canonical_timestamp(self.expires_at),
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action_hash(self) -> str:
        digest = hashlib.sha256(canonical_json(self.hash_payload()).encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def matches(self, approved_hash: str) -> bool:
        """Constant-time comparison against a previously approved hash."""
        return hmac.compare_digest(self.action_hash, approved_hash)

    def is_expired(self, now: datetime) -> bool:
        return now.astimezone(UTC) >= self.expires_at
