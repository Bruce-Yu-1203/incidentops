"""The operator guide must explain every escalation reason the code can emit."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = (ROOT / "docs" / "operations" / "failure-modes.md").read_text(encoding="utf-8")

# Escalation reasons written by trusted code paths (grep for escalation_reason).
EMITTED = {
    "insufficient_evidence",
    "approval_rejected",
    "approval_expired",
    "action_cancelled",
    "action_failed",
    "outcome_unknown",
    "verification_failed",
    "unknown",
    "model_error",
}


def test_every_escalation_reason_is_documented() -> None:
    missing = sorted(r for r in EMITTED if f"`{r}`" not in GUIDE)
    assert not missing


def test_emitted_set_matches_source_literals() -> None:
    # Catch new literal reasons added in code without updating the guide.
    literals: set[str] = set()
    for path in (ROOT / "incidentops").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        literals.update(re.findall(r'"escalation_reason":\s*"([a-z_]+)"', text))
        literals.update(re.findall(r'_escalate(?:_incident)?\([^)]*?"([a-z_]+)"', text, re.S))
    assert literals <= EMITTED | {"insufficient_evidence"}, literals - EMITTED


def test_lifecycle_diagrams_present() -> None:
    assert GUIDE.count("stateDiagram-v2") == 2
