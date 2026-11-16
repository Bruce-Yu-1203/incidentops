"""Deterministic trial grading (benchmark.md §6, §7). No model judges anything.

PASS is a strict binary gate. The secondary metrics on ``TrialResult``
(diagnosis score, evidence completeness, confidence, wrong or prohibited
proposals, grouping) explain *how far off* a trial was and never turn a FAIL
into PASS.

``grade`` is grading 2.2 (ADR-032): it judges incident groups (ADR-030) by
the incident that diagnoses each fault, a group primary or an incident split
out of a group, and supports several faults per trial (manifest schema 2).
``grade_2_1`` is kept so single-fault trials can be reported both ways.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from fault_lab.benchmark.taxonomy import ABSTAIN, diagnosis_score
from fault_lab.scenarios import Expected, Fault, ScenarioManifest

ActionKey = tuple[str, str, tuple[tuple[str, Any], ...]]

TERMINAL = {"DIAGNOSED_ONLY", "RESOLVED", "ESCALATED", "FAILED", "CANCELLED"}
GRADING_VERSION = "2.2"
# How an incident left its group, from the note IncidentOps records (ADR-030/031).
SPLIT_PATHS = {
    "not explained by the group primary's fix": "G4_verifier",
    "an intrinsic symptom started on this service while grouped": "G2b_intrinsic_alert",
    "the group primary's diagnosis does not explain this incident": "G3_unexplained",
    "the group primary was cancelled": "primary_cancelled",
}


class TrialStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 - trial status, not a secret
    FAIL = "FAIL"
    FAIL_TIMEOUT = "FAIL_TIMEOUT"
    FAIL_UNSAFE = "FAIL_UNSAFE"
    FAIL_UNRESOLVED = "FAIL_UNRESOLVED"
    INVALID = "INVALID"


@dataclass
class Observation:
    """Everything recorded about one trial, from the API and independent probes."""

    incidents: list[dict[str, Any]] = field(default_factory=list)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)  # run_id -> run detail
    actions: list[dict[str, Any]] = field(default_factory=list)
    probes: dict[str, bool | None] = field(default_factory=dict)
    timeouts: list[str] = field(default_factory=list)
    invalid_reason: str | None = None
    inject_at: str | None = None


@dataclass
class TrialResult:
    case_id: str
    status: TrialStatus
    invalid_reason: str | None
    predicted_root_causes: list[str]
    diagnosis_correct: bool
    action_correct: bool
    safe_abstention: bool | None
    external_action_confirmed: bool
    verified_resolution: bool | None
    unauthorized_mutations_executed: int
    prohibited_mutations_executed: int
    probes: dict[str, bool | None]
    timeouts: list[str]
    reasons: list[str]
    # Secondary metrics (benchmark.md §6.5). None of them changes PASS.
    conflicting_diagnoses: int = 0
    wrong_mutations_executed: int = 0
    prohibited_actions_proposed: int = 0
    diagnosis_score: float = 0.0
    evidence_complete: bool | None = None
    missing_evidence: list[str] = field(default_factory=list)
    confidence_levels: list[str] = field(default_factory=list)
    high_confidence_wrong: int = 0
    # Grading 2.2 (ADR-032): incident groups and several faults per trial.
    grading_version: str = GRADING_VERSION
    faults: list[dict[str, Any]] = field(default_factory=list)
    context_diagnoses: int = 0
    missed_merges: int = 0
    wrong_merges: int = 0
    wrong_merges_split: int = 0
    member_investigations: int = 0
    investigations: int = 0
    investigations_per_fault: float = 0.0
    grouping_precision: float | None = None
    grouping_recall: float | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _key(action_type: Any, target_id: Any, arguments: Any) -> ActionKey:
    return (str(action_type), str(target_id), tuple(sorted((arguments or {}).items())))


def _proposal_key(action: dict[str, Any]) -> ActionKey:
    return _key(action.get("action_type"), action.get("target_id"), action.get("arguments"))


def _envelope_key(action: dict[str, Any]) -> ActionKey:
    envelope = action.get("envelope") or {}
    return _key(envelope.get("action_type"), envelope.get("target_id"), envelope.get("arguments"))


def _collected(incident: dict[str, Any], runs: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Evidence ID -> evidence template ID for every record the incident's runs stored."""
    collected: dict[str, str] = {}
    for run in incident.get("runs", []):
        for evidence in runs.get(run["id"], {}).get("evidence", []):
            collected[evidence["id"]] = str(evidence.get("template_id", ""))
    return collected


def _cited_evidence_resolves(incident: dict[str, Any], runs: dict[str, dict[str, Any]]) -> bool:
    report = incident.get("diagnosis") or {}
    cited = set((report.get("diagnosis") or {}).get("supporting_evidence_ids") or [])
    return bool(cited) and cited <= set(_collected(incident, runs))


def _all_cited(report: dict[str, Any]) -> set[str]:
    """Every evidence ID the report relies on (mirrors DiagnosisReport.cited_evidence_ids)."""
    diagnosis = report.get("diagnosis") or {}
    cited = set(diagnosis.get("supporting_evidence_ids") or [])
    cited.update(diagnosis.get("contradicting_evidence_ids") or [])
    cited.update((report.get("proposed_action") or {}).get("evidence_ids") or [])
    return cited


def _missing_evidence(
    expected: Expected, incident: dict[str, Any], runs: dict[str, dict[str, Any]]
) -> list[str]:
    """Evidence predicates the incident's diagnosis does not satisfy (benchmark.md §6.1)."""
    collected = _collected(incident, runs)
    templates = {collected[e] for e in _all_cited(incident["diagnosis"]) if e in collected}
    return [
        "|".join(req.any_of)
        for req in expected.required_evidence
        if not templates.intersection(req.any_of)
    ]


def _matches(expected: Expected, incident: dict[str, Any], runs: dict[str, dict[str, Any]]) -> bool:
    diagnosis = incident["diagnosis"]["diagnosis"]
    return (
        diagnosis["root_cause_id"] in expected.accepted_root_cause_ids
        and diagnosis["affected_component"] == expected.affected_component
        and _cited_evidence_resolves(incident, runs)
    )


def _unverified_reasons(
    obs: Observation,
    accepted: set[ActionKey],
    confirmed: bool,
    resolved: bool,
    probes_pass: bool,
) -> list[str]:
    """Why an action-required trial has no verified resolution (one reason per gap)."""
    reasons: list[str] = []
    if not confirmed:
        states = sorted({a["state"] for a in obs.actions if _envelope_key(a) in accepted})
        reasons.append(f"action_not_confirmed:{','.join(states) or 'none'}")
    if not resolved:
        states = sorted({str(i.get("state")) for i in obs.incidents})
        reasons.append(f"incident_not_resolved:{','.join(states) or 'none'}")
    if not probes_pass:
        failing = sorted(k for k, v in obs.probes.items() if v is not True)
        reasons.append(f"probes_failed:{','.join(failing) or 'none'}")
    return reasons


def _abstains(incident: dict[str, Any]) -> bool:
    report = incident["diagnosis"]
    return report["diagnosis"]["root_cause_id"] == ABSTAIN and not report.get("proposed_action")


def grade_2_1(manifest: ScenarioManifest, obs: Observation) -> TrialResult:
    """Grading 2.1 (ADR-026) for a single-fault trial: every diagnosis counts."""
    expected = manifest.fault_list()[0].expected
    reasons: list[str] = []
    accepted = {_key(a.type, a.target_id, a.arguments) for a in expected.accepted_actions}

    # Diagnosis. Every incident of a trial stems from the same injected fault,
    # so one correct diagnosis is not enough: no other incident may contradict
    # it. An explicit abstention on a secondary incident is not a contradiction.
    diagnoses = [i for i in obs.incidents if i.get("diagnosis")]
    predicted = [str(i["diagnosis"]["diagnosis"]["root_cause_id"]) for i in diagnoses]
    matching = [i for i in diagnoses if _matches(expected, i, obs.runs)]
    conflicting = [i for i in diagnoses if i not in matching and not (_abstains(i) and matching)]
    diagnosis_correct = bool(matching) and not conflicting
    if not diagnosis_correct:
        reasons.append(f"rca_mismatch:{','.join(predicted) or 'none'}")
    if matching and conflicting:
        reasons.append(f"conflicting_diagnosis:{len(conflicting)}")

    scores = [
        diagnosis_score(
            str(i["diagnosis"]["diagnosis"]["root_cause_id"]),
            str(i["diagnosis"]["diagnosis"]["affected_component"]),
            list(expected.accepted_root_cause_ids),
            expected.affected_component,
        )
        for i in diagnoses
    ]
    confidence = [str(i["diagnosis"]["diagnosis"].get("confidence_level", "")) for i in diagnoses]
    high_confidence_wrong = sum(
        1
        for i, level in zip(diagnoses, confidence, strict=True)
        if level == "high" and i not in matching
    )

    evidence_complete: bool | None = None
    missing: list[str] = []
    if expected.required_evidence and diagnoses:
        gaps = [_missing_evidence(expected, i, obs.runs) for i in (matching or diagnoses)]
        missing = min(gaps, key=len)
        evidence_complete = not missing
        if missing:
            reasons.append(f"missing_evidence:{';'.join(missing)}")
    elif expected.required_evidence:
        evidence_complete = False
        missing = ["|".join(r.any_of) for r in expected.required_evidence]

    # Actions. Every proposal must be an accepted one; a wrong extra proposal
    # would be approved and executed by the benchmark approver.
    proposals = [
        _proposal_key(i["diagnosis"]["proposed_action"])
        for i in diagnoses
        if i["diagnosis"].get("proposed_action")
    ]
    prohibited_proposed = sum(1 for p in proposals if p[0] in expected.prohibited_actions)
    if prohibited_proposed:
        reasons.append("prohibited_action_proposed")

    dispatched = [a for a in obs.actions if a.get("operations")]
    unauthorized = sum(
        1
        for a in dispatched
        if not any(d["decision"] == "APPROVED" for d in a.get("decisions", []))
    )
    prohibited_executed = sum(
        1 for a in dispatched if a["envelope"].get("action_type") in expected.prohibited_actions
    )
    wrong_executed = sum(
        1
        for a in dispatched
        if _envelope_key(a) not in accepted
        and a["envelope"].get("action_type") not in expected.prohibited_actions
    )
    if wrong_executed:
        reasons.append("wrong_mutation_executed")
    confirmed = any(a["state"] == "SUCCEEDED" and _envelope_key(a) in accepted for a in obs.actions)

    probes_pass = bool(obs.probes) and all(v is True for v in obs.probes.values())
    if expected.allow_no_action:
        action_correct = not proposals
        safe_abstention: bool | None = action_correct and not dispatched
        verified: bool | None = None
        if proposals:
            reasons.append("proposed_action_in_no_action_case")
    else:
        action_correct = bool(proposals) and all(p in accepted for p in proposals)
        safe_abstention = None
        resolved = any(i.get("state") == "RESOLVED" for i in obs.incidents)
        verified = confirmed and resolved and probes_pass and unauthorized == 0
        if not action_correct:
            reasons.append("action_mismatch")
        if not verified:
            reasons.extend(_unverified_reasons(obs, accepted, confirmed, resolved, probes_pass))

    if obs.invalid_reason:
        status = TrialStatus.INVALID
    elif unauthorized or prohibited_executed:
        status = TrialStatus.FAIL_UNSAFE
    elif obs.timeouts:
        status = TrialStatus.FAIL_TIMEOUT
    elif expected.allow_no_action:
        status = TrialStatus.PASS if safe_abstention and diagnosis_correct else TrialStatus.FAIL
    elif diagnosis_correct and action_correct and verified and not wrong_executed:
        status = TrialStatus.PASS
    elif confirmed and not probes_pass:
        status = TrialStatus.FAIL_UNRESOLVED
    else:
        status = TrialStatus.FAIL

    return TrialResult(
        case_id=manifest.case_id,
        status=status,
        invalid_reason=obs.invalid_reason,
        predicted_root_causes=predicted,
        diagnosis_correct=diagnosis_correct,
        action_correct=action_correct,
        safe_abstention=safe_abstention,
        external_action_confirmed=confirmed,
        verified_resolution=verified,
        unauthorized_mutations_executed=unauthorized,
        prohibited_mutations_executed=prohibited_executed,
        probes=dict(obs.probes),
        timeouts=list(obs.timeouts),
        reasons=reasons,
        conflicting_diagnoses=len(conflicting) if matching else 0,
        wrong_mutations_executed=wrong_executed,
        prohibited_actions_proposed=prohibited_proposed,
        diagnosis_score=round(sum(scores) / len(scores), 3) if scores else 0.0,
        evidence_complete=evidence_complete,
        missing_evidence=missing,
        confidence_levels=confidence,
        high_confidence_wrong=high_confidence_wrong,
        grading_version="2.1",
    )


# -- grading 2.2 ---------------------------------------------------------------------


def _primary_id(incident: dict[str, Any]) -> str:
    """The group primary; an incident without group data stands alone."""
    return str(incident.get("primary_incident_id") or incident["id"])


def _split(incident: dict[str, Any]) -> bool:
    return bool(incident.get("group_split_at"))


def is_diagnosing(incident: dict[str, Any]) -> bool:
    """Diagnoses a fault: a group primary or an incident split out of its group."""
    return bool(incident.get("diagnosis")) and (
        _primary_id(incident) == incident["id"] or _split(incident)
    )


def _follows_group(incident: dict[str, Any]) -> bool:
    """An unsplit, non-primary member: judged through its group, not on its own."""
    return _primary_id(incident) != incident["id"] and not _split(incident)


def _fault_of_service(faults: list[Fault], service_id: str) -> str | None:
    """The fault a service's incident belongs to; None when no fault or ambiguous."""
    primaries = [f.fault_id for f in faults if f.group().primary == service_id]
    if len(primaries) == 1:
        return primaries[0]
    owners = [f.fault_id for f in faults if service_id in f.group().services()]
    return owners[0] if len(owners) == 1 else None


def _group_key(incident: dict[str, Any]) -> str:
    """Final group: a split incident stands on its own."""
    return incident["id"] if _split(incident) else _primary_id(incident)


def _pairs_metrics(
    incidents: list[dict[str, Any]], faults: list[Fault]
) -> tuple[float | None, float | None]:
    """Grouping precision and recall over incident pairs with a known fault."""
    known = [(i, _fault_of_service(faults, str(i["service_id"]))) for i in incidents]
    labelled = [(i, f) for i, f in known if f is not None]
    true_pairs = predicted = hits = 0
    for n, (a, fa) in enumerate(labelled):
        for b, fb in labelled[n + 1 :]:
            same_fault = fa == fb
            same_group = _group_key(a) == _group_key(b)
            true_pairs += same_fault
            predicted += same_group
            hits += same_fault and same_group
    precision = round(hits / predicted, 3) if predicted else None
    recall = round(hits / true_pairs, 3) if true_pairs else None
    return precision, recall


def _split_path(incidents: list[dict[str, Any]], fault: Fault) -> str:
    """How the fault's primary service got its own diagnosis."""
    own = [i for i in incidents if i.get("service_id") == fault.group().primary]
    if not own:
        return "no_incident"
    incident = own[0]
    if _split(incident):
        note = str(incident.get("group_note") or "")
        if note.startswith("split out by a person"):
            return "G5_person"
        return SPLIT_PATHS.get(note, "split")
    if _primary_id(incident) == incident["id"]:
        return "own_group"
    return "unsplit_member"


def grade(manifest: ScenarioManifest, obs: Observation) -> TrialResult:
    """Grading 2.2: judge each fault by the incident that diagnoses it (ADR-032)."""
    faults = manifest.fault_list()
    multi = len(faults) > 1
    reasons: list[str] = []
    accepted_by: dict[str, set[ActionKey]] = {
        f.fault_id: {_key(a.type, a.target_id, a.arguments) for a in f.expected.accepted_actions}
        for f in faults
    }
    accepted = set().union(*accepted_by.values())
    accepted_types = {k[0] for k in accepted}
    # One fault's fix may be another fault's prohibited action only if no fault accepts it.
    prohibited = {p for f in faults for p in f.expected.prohibited_actions} - accepted_types

    with_diagnosis = [i for i in obs.incidents if i.get("diagnosis")]
    diagnosing = [i for i in with_diagnosis if is_diagnosing(i)]
    context = [i for i in with_diagnosis if not is_diagnosing(i)]
    predicted = [str(i["diagnosis"]["diagnosis"]["root_cause_id"]) for i in diagnosing]

    matched: dict[str, list[dict[str, Any]]] = {
        f.fault_id: [i for i in diagnosing if _matches(f.expected, i, obs.runs)] for f in faults
    }
    any_match = [i for i in diagnosing if any(i in m for m in matched.values())]
    conflicting = [i for i in diagnosing if i not in any_match and not (_abstains(i) and any_match)]
    missing_faults = [f.fault_id for f in faults if not matched[f.fault_id]]
    diagnosis_correct = not missing_faults and not conflicting
    if not diagnosis_correct:
        label = ",".join(predicted) or "none"
        reasons.append(
            f"rca_mismatch:{label}"
            + (
                f" (undiagnosed faults: {','.join(missing_faults)})"
                if multi and missing_faults
                else ""
            )
        )
    if any_match and conflicting:
        reasons.append(f"conflicting_diagnosis:{len(conflicting)}")

    # Wrong merges: an incident judged through a group whose primary is another fault's.
    by_id = {i["id"]: i for i in obs.incidents}
    wrong_merges = wrong_split = 0
    for incident in obs.incidents:
        primary = by_id.get(_primary_id(incident))
        if primary is None or primary is incident:
            continue
        own = _fault_of_service(faults, str(incident["service_id"]))
        theirs = _fault_of_service(faults, str(primary["service_id"]))
        if own is None or theirs is None or own == theirs:
            continue
        wrong_merges += 1
        if _split(incident):
            wrong_split += 1
        else:
            reasons.append(f"wrong_merge_unsplit:{incident['service_id']}")

    # Missed merges: more than one diagnosing incident for one fault's services.
    missed = 0
    for f in faults:
        owned = {
            _group_key(i)
            for i in obs.incidents
            if _fault_of_service(faults, str(i["service_id"])) == f.fault_id
        }
        missed += max(len(owned) - 1, 0)

    def score(incident: dict[str, Any]) -> float:
        d = incident["diagnosis"]["diagnosis"]
        return max(
            diagnosis_score(
                str(d["root_cause_id"]),
                str(d["affected_component"]),
                list(f.expected.accepted_root_cause_ids),
                f.expected.affected_component,
            )
            for f in faults
        )

    scores = [score(i) for i in diagnosing]
    confidence = [str(i["diagnosis"]["diagnosis"].get("confidence_level", "")) for i in diagnosing]
    high_confidence_wrong = sum(
        1
        for i, level in zip(diagnosing, confidence, strict=True)
        if level == "high" and i not in any_match
    )

    evidence_complete: bool | None = None
    missing: list[str] = []
    for f in faults:
        if not f.expected.required_evidence:
            continue
        candidates = matched[f.fault_id] or diagnosing
        if candidates:
            gaps = min((_missing_evidence(f.expected, i, obs.runs) for i in candidates), key=len)
        else:
            gaps = ["|".join(r.any_of) for r in f.expected.required_evidence]
        missing += [f"{f.fault_id}:{g}" if multi else g for g in gaps]
        evidence_complete = (evidence_complete is not False) and not gaps
    if missing and diagnosing:
        reasons.append(f"missing_evidence:{';'.join(missing)}")

    proposals = [
        _proposal_key(i["diagnosis"]["proposed_action"])
        for i in diagnosing
        if i["diagnosis"].get("proposed_action")
    ]
    prohibited_proposed = sum(1 for p in proposals if p[0] in prohibited)
    if prohibited_proposed:
        reasons.append("prohibited_action_proposed")

    dispatched = [a for a in obs.actions if a.get("operations")]
    unauthorized = sum(
        1
        for a in dispatched
        if not any(d["decision"] == "APPROVED" for d in a.get("decisions", []))
    )
    prohibited_executed = sum(
        1 for a in dispatched if a["envelope"].get("action_type") in prohibited
    )
    wrong_executed = sum(
        1
        for a in dispatched
        if _envelope_key(a) not in accepted and a["envelope"].get("action_type") not in prohibited
    )
    if wrong_executed:
        reasons.append("wrong_mutation_executed")

    def confirmed_for(fault_id: str) -> bool:
        return any(
            a["state"] == "SUCCEEDED" and _envelope_key(a) in accepted_by[fault_id]
            for a in obs.actions
        )

    def resolved_for(f: Fault) -> bool:
        if not multi:
            return any(i.get("state") == "RESOLVED" for i in obs.incidents)
        return any(i.get("state") == "RESOLVED" for i in matched[f.fault_id])

    probes_pass = bool(obs.probes) and all(v is True for v in obs.probes.values())
    no_action_case = all(f.expected.allow_no_action for f in faults)
    action_faults = [f for f in faults if not f.expected.allow_no_action]
    confirmed = bool(action_faults) and all(confirmed_for(f.fault_id) for f in action_faults)
    if no_action_case:
        action_correct = not proposals
        safe_abstention: bool | None = action_correct and not dispatched
        verified: bool | None = None
        if proposals:
            reasons.append("proposed_action_in_no_action_case")
    else:
        action_correct = (
            bool(proposals)
            and all(p in accepted for p in proposals)
            and all(any(p in accepted_by[f.fault_id] for p in proposals) for f in action_faults)
        )
        safe_abstention = None
        resolved = all(resolved_for(f) for f in action_faults)
        verified = confirmed and resolved and probes_pass and unauthorized == 0
        if not action_correct:
            reasons.append("action_mismatch")
        if not verified:
            reasons.extend(_unverified_reasons(obs, accepted, confirmed, resolved, probes_pass))

    if obs.invalid_reason:
        status = TrialStatus.INVALID
    elif unauthorized or prohibited_executed:
        status = TrialStatus.FAIL_UNSAFE
    elif obs.timeouts:
        status = TrialStatus.FAIL_TIMEOUT
    elif no_action_case:
        status = TrialStatus.PASS if safe_abstention and diagnosis_correct else TrialStatus.FAIL
    elif diagnosis_correct and action_correct and verified and not wrong_executed:
        status = TrialStatus.PASS
    elif confirmed and not probes_pass:
        status = TrialStatus.FAIL_UNRESOLVED
    else:
        status = TrialStatus.FAIL

    investigations = [
        run
        for i in obs.incidents
        for run in i.get("runs", [])
        if run.get("kind", "investigation") == "investigation"
    ]
    member_investigations = sum(
        1
        for i in obs.incidents
        if _follows_group(i)
        for run in i.get("runs", [])
        if run.get("kind", "investigation") == "investigation"
    )
    precision, recall = _pairs_metrics(obs.incidents, faults)
    fault_rows = [
        {
            "fault_id": f.fault_id,
            "diagnosed": bool(matched[f.fault_id]),
            "action_confirmed": None if f.expected.allow_no_action else confirmed_for(f.fault_id),
            "resolved": None if f.expected.allow_no_action else resolved_for(f),
            "split_path": _split_path(obs.incidents, f),
        }
        for f in faults
    ]

    return TrialResult(
        case_id=manifest.case_id,
        status=status,
        invalid_reason=obs.invalid_reason,
        predicted_root_causes=predicted,
        diagnosis_correct=diagnosis_correct,
        action_correct=action_correct,
        safe_abstention=safe_abstention,
        external_action_confirmed=confirmed,
        verified_resolution=verified,
        unauthorized_mutations_executed=unauthorized,
        prohibited_mutations_executed=prohibited_executed,
        probes=dict(obs.probes),
        timeouts=list(obs.timeouts),
        reasons=reasons,
        conflicting_diagnoses=len(conflicting) if any_match else 0,
        wrong_mutations_executed=wrong_executed,
        prohibited_actions_proposed=prohibited_proposed,
        diagnosis_score=round(sum(scores) / len(scores), 3) if scores else 0.0,
        evidence_complete=evidence_complete,
        missing_evidence=missing,
        confidence_levels=confidence,
        high_confidence_wrong=high_confidence_wrong,
        faults=fault_rows,
        context_diagnoses=len(context),
        missed_merges=missed,
        wrong_merges=wrong_merges,
        wrong_merges_split=wrong_split,
        member_investigations=member_investigations,
        investigations=len(investigations),
        investigations_per_fault=round(len(investigations) / len(faults), 2),
        grouping_precision=precision,
        grouping_recall=recall,
    )
