"""Mandatory, fail-closed gateway for every agent tool call (architecture §7).

Pipeline: authorize principal -> look up tool -> validate input -> check scope
-> enforce budgets -> execute with timeout -> redact/bound -> persist evidence
-> record step -> return a structured result. Every attempt, including
denials, invalid input and timeouts, produces a trajectory step (NFR-002).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.schemas import Environment
from incidentops.evidence.redaction import redact, truncate_utf8
from incidentops.evidence.store import EvidenceDraft, EvidenceSink, build_evidence
from incidentops.security.identities import Capability, Role, has_capability
from incidentops.time import utcnow
from incidentops.tools.backends import TelemetryBackend
from incidentops.tools.executors import EXECUTORS, Executor, ScopeError
from incidentops.tools.specs import TOOL_SPECS, ToolSpec
from incidentops.trajectory.recorder import StepKind, StepRecord, TrajectorySink

DEFAULT_MAX_TOOL_CALLS = 40  # NFR-005


class ToolStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    INVALID = "INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    status: ToolStatus
    summary: str
    evidence_id: str | None = None
    data: str | None = None
    truncated: bool = False
    error_code: str | None = None

    def for_model(self) -> dict[str, Any]:
        """What the model sees: bounded, redacted, and explicitly marked untrusted."""
        payload: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "status": self.status.value,
            "summary": self.summary,
        }
        if self.evidence_id:
            payload["evidence_id"] = self.evidence_id
        if self.data is not None:
            payload["untrusted_data"] = self.data
            payload["truncated"] = self.truncated
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass
class RunContext:
    """Per-run identity and budget accounting."""

    incident_id: str
    run_id: str
    principal: Role = Role.AGENT
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    calls_by_tool: Counter[str] = field(default_factory=Counter)
    total_calls: int = 0


class ToolGateway:
    def __init__(
        self,
        catalog: ServiceCatalog,
        backend: TelemetryBackend,
        evidence: EvidenceSink,
        trajectory: TrajectorySink,
        specs: Mapping[str, ToolSpec] = TOOL_SPECS,
        executors: Mapping[str, Executor] = EXECUTORS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._catalog = catalog
        self._backend = backend
        self._evidence = evidence
        self._trajectory = trajectory
        self._specs = specs
        self._executors = executors
        self._clock = clock

    def manifest(self) -> list[dict[str, Any]]:
        """Tool definitions for the model: names, descriptions and input schemas only."""
        return [
            {"name": s.name, "description": s.description, "input_schema": s.model_schema()}
            for s in self._specs.values()
        ]

    async def invoke(
        self, ctx: RunContext, tool_call_id: str, tool_name: str, raw_args: Any
    ) -> ToolResult:
        started = self._clock()
        result = await self._invoke(ctx, tool_call_id, tool_name, raw_args, started)
        self._trajectory.record(
            StepRecord(
                run_id=ctx.run_id,
                kind=StepKind.TOOL_CALL,
                status=result.status.value,
                started_at=started,
                finished_at=self._clock(),
                metadata={
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "arguments": raw_args if isinstance(raw_args, dict) else repr(raw_args),
                    "summary": result.summary,
                    "evidence_id": result.evidence_id,
                    "error_code": result.error_code,
                },
            )
        )
        return result

    async def _invoke(
        self,
        ctx: RunContext,
        tool_call_id: str,
        tool_name: str,
        raw_args: Any,
        started: datetime,
    ) -> ToolResult:
        def fail(status: ToolStatus, code: str, summary: str) -> ToolResult:
            return ToolResult(tool_call_id, tool_name, status, redact(summary), error_code=code)

        if not has_capability(ctx.principal, Capability.READ_TELEMETRY):
            return fail(ToolStatus.DENIED, "principal_not_authorized", "principal may not read")
        spec = self._specs.get(tool_name)
        executor = self._executors.get(tool_name)
        if spec is None or executor is None:
            return fail(ToolStatus.DENIED, "tool_not_registered", f"no tool named {tool_name!r}")

        try:
            args = spec.input_model.model_validate(raw_args)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
            return fail(ToolStatus.INVALID, "invalid_arguments", f"invalid fields: {fields}")

        target = getattr(args, "service_id", None) or getattr(args, "worker_id", None)
        if target is not None:
            if not self._catalog.is_registered(target):
                return fail(ToolStatus.DENIED, "unregistered_target", f"{target} not registered")
            service = self._catalog.get(target)
            if service.kind not in spec.service_kinds:
                return fail(
                    ToolStatus.DENIED,
                    "tool_not_applicable",
                    f"{tool_name} does not apply to {service.kind.value}",
                )
            if service.environment.value not in spec.allowed_environments or (
                service.environment is Environment.PRODUCTION
            ):
                return fail(ToolStatus.DENIED, "environment_denied", "environment not allowed")

        if ctx.total_calls >= ctx.max_tool_calls:
            return fail(ToolStatus.BUDGET_EXCEEDED, "run_budget_exceeded", "run tool budget used")
        if ctx.calls_by_tool[tool_name] >= spec.max_calls_per_run:
            return fail(ToolStatus.BUDGET_EXCEEDED, "tool_budget_exceeded", "tool budget used")
        ctx.total_calls += 1
        ctx.calls_by_tool[tool_name] += 1

        try:
            output = await asyncio.wait_for(
                executor(args, self._catalog, self._backend, started),
                timeout=spec.timeout_ms / 1000,
            )
        except ScopeError as exc:
            return fail(ToolStatus.DENIED, "out_of_scope", str(exc))
        except TimeoutError:
            return fail(ToolStatus.TIMEOUT, "timeout", f"{tool_name} exceeded {spec.timeout_ms}ms")
        except Exception as exc:  # backend failure; never leak details verbatim
            return fail(ToolStatus.ERROR, "backend_error", type(exc).__name__)

        evidence = build_evidence(
            EvidenceDraft(
                incident_id=ctx.incident_id,
                run_id=ctx.run_id,
                source=output.source,
                template_id=output.template_id,
                parameters=output.parameters,
                window_start=output.window_start,
                window_end=output.window_end,
                observed_at=self._clock(),
                summary=output.summary,
                data=output.data,
            )
        )
        self._evidence.save(evidence)
        data, truncated = truncate_utf8(
            redact(json.dumps(output.data, sort_keys=True, default=str)), spec.max_response_bytes
        )
        return ToolResult(
            tool_call_id,
            tool_name,
            ToolStatus.SUCCESS,
            evidence.summary,
            evidence_id=evidence.id,
            data=data,
            truncated=truncated or evidence.truncated,
        )
