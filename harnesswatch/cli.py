# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Command line: demo, run, report, evaluate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harnesswatch import __version__
from harnesswatch.compare import compare_catalog
from harnesswatch.evaluate import measure
from harnesswatch.recording import load_catalog, load_recorded_runs
from harnesswatch.report import write_site
from harnesswatch.runner import LiveRunError, run_scenario
from harnesswatch.scenario import load_scenario, load_scenarios

DATASET = "fixtures/recordings synthetic JSONL (fixtures/behaviors.yaml labels)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnesswatch")
    parser.add_argument("--version", action="version", version=f"harnesswatch {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="build the weather report from recorded fixtures, offline")
    demo.add_argument("--out", default="docs", help="output directory (default: docs)")
    demo.add_argument("--root", default=".", help="repository root")

    report = sub.add_parser("report", help="alias of demo")
    report.add_argument("--out", default="docs")
    report.add_argument("--root", default=".")

    evaluate = sub.add_parser("evaluate", help="print detection and false-positive rates")
    evaluate.add_argument("--root", default=".")
    evaluate.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="optional live CLI run (API key required)")
    run.add_argument("--scenario", required=True)
    run.add_argument("--harness", required=True, choices=["claude-code", "codex"])
    run.add_argument("--claude-bin", default="claude")
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--timeout", type=float, default=600)
    run.add_argument("--max-turns", type=int, default=12)

    args = parser.parse_args(argv)
    if args.cmd in {"demo", "report"}:
        measurement = build_demo(Path(args.root).resolve(), Path(args.out).resolve())
        _print_measurement(measurement)
        print(f"wrote {Path(args.out).resolve() / 'index.html'}")
        return 0
    if args.cmd == "evaluate":
        measurement = build_demo(Path(args.root).resolve(), None)
        if args.json:
            print(json.dumps(_public_measurement(measurement), indent=2))
        else:
            _print_measurement(measurement)
        return 0
    if args.cmd == "run":
        return _run(args)
    return 2


def build_demo(root: Path, out: Path | None):
    scenarios = load_scenarios(root / "scenarios")
    catalog = load_catalog(root / "fixtures" / "behaviors.yaml")
    scenario_map = {scenario.id: scenario for scenario in scenarios}
    grouped = load_recorded_runs(root / "fixtures" / "recordings", scenario_map, catalog)
    comparisons = compare_catalog(scenario_map, catalog, grouped)
    measurement = measure(catalog, comparisons, dataset=DATASET)
    if out is not None:
        write_site(
            out,
            scenarios=scenarios,
            catalog=catalog,
            comparisons=comparisons,
            measurement=measurement,
            scenario_dir=root / "scenarios",
        )
    return measurement


def _run(args: argparse.Namespace) -> int:
    scenario = load_scenario(Path(args.scenario))
    try:
        result = run_scenario(
            scenario,
            args.harness,
            claude_bin=args.claude_bin,
            codex_bin=args.codex_bin,
            timeout_s=args.timeout,
            max_turns=args.max_turns,
        )
    except LiveRunError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    trajectory = result.trajectory
    print(f"exit={result.returncode} tools={len(trajectory.tool_calls)} permission_prompts={trajectory.permission_prompts}")
    print(f"cache_hit_rate={trajectory.usage.cache_hit_rate:.3f} input_tokens={trajectory.usage.input_tokens}")
    if trajectory.diff.strip():
        print(trajectory.diff)
    else:
        print("(empty diff)")
    return 0 if result.returncode == 0 else result.returncode


def _print_measurement(measurement) -> None:
    print(
        f"detection {measurement.regressions_detected}/{measurement.regressions_total}"
        f" ({measurement.detection_rate:.0%})"
    )
    print(
        f"false positives {measurement.false_positives}/{measurement.stable_total}"
        f" ({measurement.false_positive_rate:.0%})"
    )
    print(f"dataset: {measurement.dataset}")
    if measurement.misses:
        print("misses: " + ", ".join(measurement.misses))
    if measurement.false_positive_ids:
        print("false positives: " + ", ".join(measurement.false_positive_ids))


def _public_measurement(measurement) -> dict:
    return {
        "dataset": measurement.dataset,
        "k": measurement.k,
        "alpha": measurement.alpha,
        "bootstrap_resamples": measurement.bootstrap_resamples,
        "regressions_total": measurement.regressions_total,
        "regressions_detected": measurement.regressions_detected,
        "stable_total": measurement.stable_total,
        "false_positives": measurement.false_positives,
        "detection_rate": measurement.detection_rate,
        "false_positive_rate": measurement.false_positive_rate,
        "misses": list(measurement.misses),
        "false_positive_ids": list(measurement.false_positive_ids),
    }
