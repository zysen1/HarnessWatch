# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Load recorded session JSONL fixtures from disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from harnesswatch.assertions import AssertionResult, evaluate_assertions, metric_value, run_passed
from harnesswatch.trajectory import Trajectory
from harnesswatch.parsers import parse_jsonl
from harnesswatch.scenario import Scenario


@dataclass
class RecordedRun:
    harness: str
    version: str
    scenario_id: str
    index: int
    transcript_path: Path
    trajectory_passed: bool
    assertions: list[AssertionResult]
    metrics: dict[str, float]
    trajectory: Trajectory


@dataclass(frozen=True)
class BehaviorCase:
    harness: str
    scenario: str
    label: str
    candidate_pattern: str
    note: str


@dataclass(frozen=True)
class BehaviorCatalog:
    k: int
    alpha: float
    bootstrap_resamples: int
    versions: dict[str, dict[str, str]]
    cases: tuple[BehaviorCase, ...]

    def version(self, harness: str, role: str) -> str:
        return self.versions[harness][role]


def load_catalog(path: Path) -> BehaviorCatalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = tuple(
        BehaviorCase(
            harness=str(item["harness"]),
            scenario=str(item["scenario"]),
            label=str(item["label"]),
            candidate_pattern=str(item["candidate_pattern"]),
            note=str(item.get("note") or ""),
        )
        for item in data["comparisons"]
    )
    labels = {case.label for case in cases}
    if not labels <= {"regression", "stable"}:
        raise ValueError(f"unknown labels: {labels - {'regression', 'stable'}}")
    return BehaviorCatalog(
        k=int(data["k"]),
        alpha=float(data["alpha"]),
        bootstrap_resamples=int(data.get("bootstrap_resamples") or 4000),
        versions={str(harness): {str(role): str(version) for role, version in pair.items()} for harness, pair in data["versions"].items()},
        cases=cases,
    )


def recordings_root(repo_root: Path) -> Path:
    return repo_root / "fixtures" / "recordings"


def load_recorded_runs(
    root: Path,
    scenarios: dict[str, Scenario],
    catalog: BehaviorCatalog,
) -> dict[tuple[str, str, str], list[RecordedRun]]:
    """Map (harness, version, scenario id) to parsed runs with assertion results."""

    grouped: dict[tuple[str, str, str], list[RecordedRun]] = {}
    for case in catalog.cases:
        scenario = scenarios[case.scenario]
        for role in ("baseline", "candidate"):
            version = catalog.version(case.harness, role)
            directory = root / case.harness / version / case.scenario
            runs = _load_directory(directory, case.harness, version, scenario)
            if len(runs) != catalog.k:
                raise ValueError(f"{directory} has {len(runs)} runs, catalog k is {catalog.k}")
            grouped[(case.harness, version, case.scenario)] = runs
    return grouped


def _load_directory(directory: Path, harness: str, version: str, scenario: Scenario) -> list[RecordedRun]:
    paths = sorted(directory.glob("run-*.jsonl"))
    assertions = scenario.assertions_for(harness)
    runs: list[RecordedRun] = []
    for path in paths:
        index = int(path.stem.split("-")[1])
        diff_path = path.with_suffix(".diff")
        transcript = path.read_text(encoding="utf-8")
        trajectory = parse_jsonl(transcript, harness=harness, version=version)
        trajectory.diff = diff_path.read_text(encoding="utf-8") if diff_path.exists() else ""
        results = evaluate_assertions(trajectory, assertions)
        runs.append(
            RecordedRun(
                harness=harness,
                version=version,
                scenario_id=scenario.id,
                index=index,
                transcript_path=path,
                trajectory_passed=run_passed(results),
                assertions=results,
                metrics={watch.name: metric_value(trajectory, watch.name) for watch in scenario.watch_metrics},
                trajectory=trajectory,
            )
        )
    runs.sort(key=lambda run: run.index)
    return runs
