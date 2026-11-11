"""Published benchmark runs, read from ``docs/benchmark-results``.

Only committed, measured result folders are served; nothing is computed from
the live database. Each folder needs ``run-metadata.json`` and
``trials.jsonl`` written by the benchmark runner.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

DEFAULT_RESULTS_DIR: Final = Path(__file__).resolve().parents[2] / "docs" / "benchmark-results"


class TrialView(BaseModel):
    trial_id: str
    case_id: str
    status: str
    predicted_root_causes: list[str]
    diagnosis_correct: bool | None
    action_correct: bool | None
    verified_resolution: bool | None
    safe_abstention: bool | None
    unsafe_mutations: int
    time_to_detect_s: float | None
    time_to_diagnosis_s: float | None
    time_to_resolution_s: float | None
    reasons: list[str]


class CaseView(BaseModel):
    case_id: str
    trials: list[TrialView]
    passed: int
    median_time_to_detect_s: float | None


class RunView(BaseModel):
    run_id: str
    folder: str
    variant: str
    model_id: str
    started_at: str
    trials_per_case: int
    attempted: int
    passed: int
    unsafe_mutations: int
    median_time_to_detect_s: float | None
    cases: list[CaseView]


def _median(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 1) if present else None


def _trial(raw: dict[str, Any]) -> TrialView:
    return TrialView(
        trial_id=str(raw["trial_id"]),
        case_id=str(raw["case_id"]),
        status=str(raw["status"]),
        predicted_root_causes=[str(r) for r in raw.get("predicted_root_causes") or []],
        diagnosis_correct=raw.get("diagnosis_correct"),
        action_correct=raw.get("action_correct"),
        verified_resolution=raw.get("verified_resolution"),
        safe_abstention=raw.get("safe_abstention"),
        unsafe_mutations=int(raw.get("unauthorized_mutations_executed") or 0)
        + int(raw.get("prohibited_mutations_executed") or 0),
        time_to_detect_s=raw.get("time_to_detect_s"),
        time_to_diagnosis_s=raw.get("time_to_diagnosis_s"),
        time_to_resolution_s=raw.get("time_to_resolution_s"),
        reasons=[str(r) for r in raw.get("reasons") or []],
    )


def load_run(folder: Path) -> RunView | None:
    meta_path, trials_path = folder / "run-metadata.json", folder / "trials.jsonl"
    if not meta_path.is_file() or not trials_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    trials = [
        _trial(json.loads(line))
        for line in trials_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    order = [str(c) for c in meta.get("cases", [])] or sorted({t.case_id for t in trials})
    cases = [
        CaseView(
            case_id=case_id,
            trials=[t for t in trials if t.case_id == case_id],
            passed=sum(1 for t in trials if t.case_id == case_id and t.status == "PASS"),
            median_time_to_detect_s=_median(
                [t.time_to_detect_s for t in trials if t.case_id == case_id]
            ),
        )
        for case_id in order
    ]
    return RunView(
        run_id=str(meta.get("run_id", folder.name)),
        folder=folder.name,
        variant=str(meta.get("variant", "")),
        model_id=str(meta.get("model_id", "")),
        started_at=str(meta.get("started_at", "")),
        trials_per_case=int(meta.get("trials_per_case", 0)),
        attempted=len(trials),
        passed=sum(1 for t in trials if t.status == "PASS"),
        unsafe_mutations=sum(t.unsafe_mutations for t in trials),
        median_time_to_detect_s=_median([t.time_to_detect_s for t in trials]),
        cases=cases,
    )


def list_runs(results_dir: Path = DEFAULT_RESULTS_DIR) -> list[RunView]:
    if not results_dir.is_dir():
        return []
    runs = [load_run(folder) for folder in sorted(results_dir.iterdir()) if folder.is_dir()]
    return sorted((r for r in runs if r is not None), key=lambda r: r.started_at, reverse=True)
