"""Executor worker: the only process holding a remediation credential.

Run with ``python -m incidentops.workers.executor``. It has no model access
and no read-token for agent tools; it uses its executor token for exactly the
allowlisted adapter calls.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Final

import httpx

from incidentops.actions.approvals import EXECUTE_ACTION_JOB
from incidentops.config import Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.execution.adapters import (
    LAB_CONTROLLER_ACTIONS,
    ConsumerResumeAdapter,
    LabControllerAdapter,
    RemediationAdapter,
)
from incidentops.execution.executor import (
    RECONCILE_ACTION_JOB,
    ActionExecutor,
)
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.policy.engine import PolicyEngine, load_policy
from incidentops.security.identities import Role
from incidentops.workers.common import Handler, owner_id, run_forever
from incidentops.workers.credentials import assert_credential_scope

RECONCILE_MAX_ATTEMPTS: Final = 5


def handlers(executor: ActionExecutor) -> dict[str, Handler]:
    async def execute(payload: dict[str, Any], _: int) -> None:
        await executor.execute(str(payload["action_id"]))

    async def reconcile(payload: dict[str, Any], attempts: int) -> None:
        # On the last allowed attempt an unknown outcome escalates instead of retrying.
        await executor.reconcile(
            str(payload["action_id"]), give_up=attempts >= RECONCILE_MAX_ATTEMPTS
        )

    return {EXECUTE_ACTION_JOB: execute, RECONCILE_ACTION_JOB: reconcile}


async def serve(settings: Settings) -> None:  # pragma: no cover - process entry point
    assert_credential_scope(Role.EXECUTOR, settings)
    if settings.executor_lab_token is None:
        raise SystemExit("INCIDENTOPS_EXECUTOR_LAB_TOKEN is required")
    factory = make_session_factory(make_engine(settings.control_database_url.get_secret_value()))
    catalog = load_catalog()
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        adapter = ConsumerResumeAdapter(
            client,
            catalog,
            settings.executor_lab_token.get_secret_value(),
            settings.endpoint_overrides,
        )
        controller = LabControllerAdapter(
            client,
            catalog,
            settings.executor_lab_token.get_secret_value(),
            settings.endpoint_overrides,
        )
        adapters: dict[str, RemediationAdapter] = {"resume_test_consumer": adapter}
        adapters.update({action: controller for action in LAB_CONTROLLER_ACTIONS})
        executor = ActionExecutor(factory, PolicyEngine(load_policy(), catalog), adapters)
        await run_forever(
            factory,
            handlers(executor),
            owner_id("executor"),
            120,
            max_attempts=RECONCILE_MAX_ATTEMPTS,
        )


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":  # pragma: no cover
    main()
