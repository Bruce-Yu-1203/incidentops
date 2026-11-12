"""Hierarchical root-cause taxonomy for partial diagnosis credit (benchmark side).

The agent still emits one flat ``root_cause_id``. The grader maps it onto
``category`` and ``mechanism`` and compares each level separately, so a
near miss (right component and family, wrong mechanism) is told apart from a
completely wrong diagnosis. This is a secondary metric: PASS stays binary.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

# Level weights of the diagnosis score; they sum to 1.
COMPONENT_WEIGHT: Final = 0.4
CATEGORY_WEIGHT: Final = 0.3
MECHANISM_WEIGHT: Final = 0.3

ABSTAIN: Final = "insufficient_evidence"


@dataclass(frozen=True)
class RootCauseLevels:
    category: str
    mechanism: str


# Keep in sync with ``ROOT_CAUSE_TAXONOMY`` in ``incidentops/agent/prompt.py``;
# a test fails when a root cause is missing here.
ROOT_CAUSES: Final = MappingProxyType(
    {
        "consumer_paused": RootCauseLevels("consumer_stall", "consumer_paused"),
        "consumer_slow": RootCauseLevels("consumer_stall", "consumer_slow"),
        "kafka_broker_unavailable": RootCauseLevels(
            "dependency_unavailable", "kafka_broker_unavailable"
        ),
        "postgres_unavailable": RootCauseLevels("dependency_unavailable", "postgres_unavailable"),
        "redis_unavailable": RootCauseLevels("dependency_unavailable", "redis_unavailable"),
        "postgres_lock_contention": RootCauseLevels(
            "database_contention", "postgres_lock_contention"
        ),
        "connection_pool_exhausted": RootCauseLevels(
            "database_contention", "connection_pool_exhausted"
        ),
        "bad_deployment": RootCauseLevels("change_induced", "bad_deployment"),
        "config_error": RootCauseLevels("change_induced", "config_error"),
        "application_error": RootCauseLevels("application_defect", "application_error"),
        "application_memory_leak": RootCauseLevels("application_defect", "application_memory_leak"),
        "cpu_saturation": RootCauseLevels("resource_saturation", "cpu_saturation"),
        "traffic_surge": RootCauseLevels("resource_saturation", "traffic_surge"),
        ABSTAIN: RootCauseLevels(ABSTAIN, ABSTAIN),
    }
)


def levels(root_cause_id: str) -> RootCauseLevels | None:
    return ROOT_CAUSES.get(root_cause_id)


def diagnosis_score(
    predicted_root_cause: str,
    predicted_component: str,
    accepted_root_causes: list[str],
    expected_component: str,
) -> float:
    """Weighted level match in [0, 1]; the best accepted root cause counts.

    An unknown predicted root cause earns no category or mechanism credit.
    """
    component = COMPONENT_WEIGHT if predicted_component == expected_component else 0.0
    predicted = levels(predicted_root_cause)
    best = 0.0
    for accepted in accepted_root_causes:
        expected = levels(accepted)
        if predicted is None or expected is None:
            continue
        score = 0.0
        if predicted.category == expected.category:
            score += CATEGORY_WEIGHT
        if predicted.mechanism == expected.mechanism:
            score += MECHANISM_WEIGHT
        best = max(best, score)
    return round(component + best, 3)
