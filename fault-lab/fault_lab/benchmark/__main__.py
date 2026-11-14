"""Benchmark CLI.

    python -m fault_lab.benchmark run --cases B-F01,B-F03 --trials 5 --variant B0_RULES
    python -m fault_lab.benchmark report --input results/<run_id>

Requires the lab, observability stack, API and workers to be running, the
benchmark token (FAULT_LAB_BENCHMARK_TOKEN) and the seeded approver password
(INCIDENTOPS_DEMO_APPROVER_PASSWORD).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from fault_lab.benchmark.clients import IncidentOpsApi, LabFaults, Prometheus
from fault_lab.benchmark.report import write_report
from fault_lab.benchmark.runner import TrialRunner
from fault_lab.scenarios import all_scenarios, load_scenario
from fault_lab.scripts.labctl import LabEndpoints

BENCHMARK_VERSION = "2.1"  # 2.1: stricter grading and secondary metrics


def _run(args: argparse.Namespace) -> int:
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN")
    password = os.environ.get("INCIDENTOPS_DEMO_APPROVER_PASSWORD")
    if not token or not password:
        print(
            "FAULT_LAB_BENCHMARK_TOKEN and INCIDENTOPS_DEMO_APPROVER_PASSWORD are required",
            file=sys.stderr,
        )
        return 2
    manifests = (
        [load_scenario(c.strip()) for c in args.cases.split(",")] if args.cases else all_scenarios()
    )
    schedule = [m for m in manifests for _ in range(args.trials)]
    # Counterbalance order effects (§11.1); not security-relevant randomness.
    random.Random(args.seed).shuffle(schedule)  # noqa: S311
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{args.variant}"
    out_dir = Path(args.out) / run_id
    trials: list[dict[str, object]] = []
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=15) as client, httpx.Client(timeout=15) as api_client:
        api = IncidentOpsApi(api_client, args.api)
        api.login("approver", password)
        runner = TrialRunner(
            LabFaults(client, token, LabEndpoints()),
            Prometheus(client, args.prometheus),
            api,
            args.alertmanager,
        )
        counts: dict[str, int] = {}
        for manifest in schedule:
            counts[manifest.case_id] = counts.get(manifest.case_id, 0) + 1
            trial_id = f"{manifest.case_id}-seed{manifest.seed}-run{counts[manifest.case_id]:02d}"
            record = runner.run(manifest, trial_id)
            record["variant"] = args.variant
            trials.append(record)
            detail = record.get("invalid_reason") or record.get("reasons") or ""
            print(f"[{trial_id}] {record['status']} {detail}", flush=True)
            with (out_dir.parent / f"{run_id}.partial.jsonl").open("a", encoding="utf-8") as h:
                h.write(json.dumps(record) + "\n")
    metadata = {
        "benchmark_version": BENCHMARK_VERSION,
        "run_id": run_id,
        "variant": args.variant,
        "model_id": ",".join(sorted({str(t.get("model_id")) for t in trials})),
        "started_at": trials[0]["started_at"] if trials else None,
        "cases": sorted({m.case_id for m in manifests}),
        "trials_per_case": args.trials,
        "order_seed": args.seed,
        "scenario_versions": {m.case_id: m.case_version for m in manifests},
    }
    path = write_report(trials, metadata, out_dir)
    print(f"report: {path}")
    return 0


def _report(args: argparse.Namespace) -> int:
    in_dir = Path(args.input)
    trials = [json.loads(line) for line in (in_dir / "trials.jsonl").read_text().splitlines()]
    metadata = json.loads((in_dir / "run-metadata.json").read_text())
    print(write_report(trials, metadata, in_dir))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fault_lab.benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--cases", default="")
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--variant", default="B0_RULES")
    run.add_argument("--seed", type=int, default=2026)
    run.add_argument("--out", default="results")
    run.add_argument(
        "--api", default=os.environ.get("INCIDENTOPS_API_URL", "http://127.0.0.1:8000")
    )
    run.add_argument("--prometheus", default="http://127.0.0.1:9090")
    run.add_argument("--alertmanager", default="http://127.0.0.1:9093")
    report = sub.add_parser("report")
    report.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    return _run(args) if args.command == "run" else _report(args)


if __name__ == "__main__":
    raise SystemExit(main())
