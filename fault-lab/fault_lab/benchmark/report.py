"""Aggregate trial records into JSONL/CSV/Markdown with honest denominators.

Invalid trials are counted and reported, never dropped (benchmark.md §7).
Proportions come with Wilson 95% intervals; small samples are labelled as such.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Z95 = 1.959963984540054


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    """Wilson score interval; None when there are no trials."""
    if total == 0:
        return None
    p = successes / total
    denom = 1 + Z95**2 / total
    centre = (p + Z95**2 / (2 * total)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / total + Z95**2 / (4 * total**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _rate(successes: int, total: int) -> str:
    if total == 0:
        return "n/a"
    interval = wilson(successes, total)
    assert interval is not None  # noqa: S101 - total > 0
    return (
        f"{successes}/{total} ({successes / total:.0%}, 95% CI {interval[0]:.0%}-{interval[1]:.0%})"
    )


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


@dataclass
class CaseSummary:
    case_id: str
    attempted: int
    valid: int
    invalid: int
    passed: int
    rca_correct: int
    action_correct: int
    verified_recovery: int
    recovery_applicable: int
    safe_abstention: int
    abstention_applicable: int
    unsafe_executed: int
    median_ttd_s: float | None
    p95_resolution_s: float | None

    def row(self) -> list[str]:
        recovery = (
            _rate(self.safe_abstention, self.abstention_applicable)
            if self.abstention_applicable
            else _rate(self.verified_recovery, self.recovery_applicable)
        )
        return [
            self.case_id,
            f"{self.valid}/{self.attempted}",
            _rate(self.rca_correct, self.valid),
            _rate(self.action_correct, self.valid),
            recovery,
            str(self.unsafe_executed),
            "n/a" if self.median_ttd_s is None else f"{self.median_ttd_s:.0f}s",
            "n/a" if self.p95_resolution_s is None else f"{self.p95_resolution_s:.0f}s",
            str(self.invalid),
        ]


def summarize(trials: Iterable[dict[str, Any]]) -> list[CaseSummary]:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        by_case[trial["case_id"]].append(trial)
    summaries: list[CaseSummary] = []
    for case_id in sorted(by_case):
        rows = by_case[case_id]
        valid = [r for r in rows if r["status"] != "INVALID"]
        ttd = [r["time_to_detect_s"] for r in valid if r.get("time_to_detect_s") is not None]
        res = [
            r["time_to_resolution_s"] for r in valid if r.get("time_to_resolution_s") is not None
        ]
        summaries.append(
            CaseSummary(
                case_id=case_id,
                attempted=len(rows),
                valid=len(valid),
                invalid=len(rows) - len(valid),
                passed=sum(r["status"] == "PASS" for r in valid),
                rca_correct=sum(bool(r["diagnosis_correct"]) for r in valid),
                action_correct=sum(bool(r["action_correct"]) for r in valid),
                verified_recovery=sum(r["verified_resolution"] is True for r in valid),
                recovery_applicable=sum(r["verified_resolution"] is not None for r in valid),
                safe_abstention=sum(r["safe_abstention"] is True for r in valid),
                abstention_applicable=sum(r["safe_abstention"] is not None for r in valid),
                unsafe_executed=sum(
                    int(r["unauthorized_mutations_executed"])
                    + int(r["prohibited_mutations_executed"])
                    for r in rows
                ),
                median_ttd_s=statistics.median(ttd) if ttd else None,
                p95_resolution_s=_percentile(res, 0.95),
            )
        )
    return summaries


HEADERS = [
    "Scenario",
    "Valid / attempted",
    "RCA correct",
    "Correct action",
    "Verified recovery / safe abstention",
    "Unsafe executed",
    "Median TTD",
    "P95 resolution",
    "Invalid",
]

CSV_FIELDS = [
    "trial_id",
    "case_id",
    "case_version",
    "variant",
    "model_id",
    "status",
    "invalid_reason",
    "predicted_root_causes",
    "diagnosis_correct",
    "action_correct",
    "safe_abstention",
    "external_action_confirmed",
    "verified_resolution",
    "unauthorized_mutations_executed",
    "prohibited_mutations_executed",
    "time_to_detect_s",
    "time_to_diagnosis_s",
    "time_to_resolution_s",
    "tool_attempts",
    "tokens_input",
    "tokens_output",
    "reasons",
    "started_at",
]


def write_report(trials: list[dict[str, Any]], metadata: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "trials.jsonl").open("w", encoding="utf-8") as handle:
        for trial in trials:
            handle.write(json.dumps(trial, sort_keys=True) + "\n")
    with (out_dir / "trials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for trial in trials:
            writer.writerow(
                {k: (";".join(v) if isinstance(v, list) else v) for k, v in trial.items()}
            )
    (out_dir / "run-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )
    summaries = summarize(trials)
    lines = [
        f"# Benchmark summary — {metadata.get('variant', 'unknown variant')}",
        "",
        f"Run `{metadata.get('run_id')}` · model `{metadata.get('model_id')}` · "
        f"{len(trials)} attempted trials · started {metadata.get('started_at')}.",
        "",
        "All numbers below are **measured** in this run. Rates show raw counts and Wilson 95%",
        "intervals; with fewer than 5 valid trials per case they are smoke-level estimates,",
        "not evidence of differences between variants.",
        "",
        "| " + " | ".join(HEADERS) + " |",
        "|" + "---|" * len(HEADERS),
        *("| " + " | ".join(s.row()) + " |" for s in summaries),
        "",
        "## Safety",
        "",
        f"Unauthorized or scenario-prohibited mutations executed: "
        f"{sum(s.unsafe_executed for s in summaries)} observed in {len(trials)} trials.",
        "",
        "## Failures and invalid trials",
        "",
    ]
    for trial in trials:
        if trial["status"] != "PASS":
            lines.append(
                f"- `{trial['trial_id']}`: {trial['status']}"
                + (f" ({trial['invalid_reason']})" if trial.get("invalid_reason") else "")
                + (f" — {', '.join(trial['reasons'])}" if trial.get("reasons") else "")
            )
    if all(t["status"] == "PASS" for t in trials):
        lines.append("- none")
    lines += ["", "See `limitations.md` for threats to validity.", ""]
    path = out_dir / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "limitations.md").write_text(LIMITATIONS, encoding="utf-8")
    return path


LIMITATIONS = """# Limitations

- Toy fault distribution: six controlled families on one Docker host do not
  represent real production incidents.
- The CPU, memory-leak and bad-release faults are application-level
  simulations inside the lab API (no container or image changes); the Redis
  outage is simulated by an authentication change, not a process restart.
- The benchmark approver approves every pending proposal; it tests the
  authorization path, not human judgement or response time.
- Shared-host resource effects, scrape intervals and alert `for` windows
  affect timing; timings are only comparable within the same environment.
- Small samples: fewer than 5 valid trials per case is a smoke test.
- Model and provider behaviour can drift; the model ID is recorded per trial.
"""
