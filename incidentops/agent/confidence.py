"""Deterministic confidence rules checked before a diagnosis is accepted (ADR-025).

The rubric in the prompt is judgement; these rules are the part of it that code
can verify from the evidence records a report cites. A report that breaks one
is sent back to the model once with the reason, like any invalid report.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from incidentops.domain.schemas import ConfidenceLevel, DiagnosisReport, Evidence

INSUFFICIENT: Final = "insufficient_evidence"

# Readings that only describe the symptom on the failing service itself. A
# high-confidence diagnosis needs at least one reading outside this set: a
# dependency, change, status or resource check.
SYMPTOM_TEMPLATES: Final = frozenset(
    {
        "api_error_ratio",
        "api_latency_p95",
        "api_request_rate",
        "kafka_group_lag",
        "kafka_group_lag_by_partition",
        "service_logs",
        "service_traces",
    }
)
MIN_HIGH_SOURCES: Final = 2


class ConfidenceRuleError(ValueError):
    """The stated confidence does not match the cited evidence."""


def check_confidence(report: DiagnosisReport, evidence: Iterable[Evidence]) -> None:
    """Raise ConfidenceRuleError with a repair hint when a rule is broken."""
    diagnosis = report.diagnosis
    level = diagnosis.confidence_level
    if diagnosis.root_cause_id == INSUFFICIENT and level is not ConfidenceLevel.LOW:
        raise ConfidenceRuleError("insufficient_evidence must use confidence_level low")
    if level is ConfidenceLevel.LOW and report.proposed_action is not None:
        raise ConfidenceRuleError(
            "a low-confidence diagnosis may not propose an action; remove proposed_action "
            "and set needs_human_review=true, or gather stronger evidence"
        )
    if level is not ConfidenceLevel.HIGH:
        return
    cited = set(diagnosis.supporting_evidence_ids) | set(diagnosis.contradicting_evidence_ids)
    templates = {e.query.template_id for e in evidence if e.id in cited}
    if len(templates) < MIN_HIGH_SOURCES:
        raise ConfidenceRuleError(
            "high confidence needs cited readings from at least two different tools or "
            f"metric templates (cited: {sorted(templates)}); cite more evidence or use medium"
        )
    if templates <= SYMPTOM_TEMPLATES:
        raise ConfidenceRuleError(
            "high confidence needs at least one cited dependency, change, status or "
            "resource check, not only symptom readings; cite one or use medium"
        )
