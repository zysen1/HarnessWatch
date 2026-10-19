# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Static weather-report site rendered with Jinja2."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from harnesswatch.compare import Comparison
from harnesswatch.evaluate import Measurement
from harnesswatch.recording import BehaviorCatalog
from harnesswatch.repro import render_issue
from harnesswatch.scenario import Scenario
from harnesswatch.trajectory import Trajectory

HARNESS_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex CLI",
}


def write_site(
    output: Path,
    *,
    scenarios: list[Scenario],
    catalog: BehaviorCatalog,
    comparisons: list[Comparison],
    measurement: Measurement,
    scenario_dir: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    template_dir = Path(__file__).parent / "templates"
    shutil.copyfile(template_dir / "style.css", assets / "style.css")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    scenario_map = {scenario.id: scenario for scenario in scenarios}
    by_scenario: dict[str, list[Comparison]] = defaultdict(list)
    for comparison in comparisons:
        by_scenario[comparison.scenario_id].append(comparison)
    columns = [
        {"id": scenario.id, "title": scenario.title, "category": scenario.category}
        for scenario in scenarios
    ]
    rows = _rows(scenarios, catalog, comparisons)
    measurement_payload = _measurement_payload(measurement)
    (output / "measurement.json").write_text(
        json.dumps(measurement_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    index = env.get_template("index.html.j2")
    (output / "index.html").write_text(
        index.render(
            columns=columns,
            rows=rows,
            measurement=measurement_payload,
            bulletin="Fixture bulletin 001",
        ),
        encoding="utf-8",
    )
    detail = env.get_template("scenario.html.j2")
    scenario_out = output / "scenarios"
    scenario_out.mkdir(exist_ok=True)
    for scenario in scenarios:
        pairs = []
        for comparison in by_scenario.get(scenario.id, []):
            baseline = comparison.representative("baseline")
            candidate = comparison.representative("candidate")
            pairs.append(
                {
                    "comparison": comparison,
                    "harness_label": HARNESS_LABELS.get(comparison.harness, comparison.harness),
                    "baseline_steps": _steps(baseline.trajectory),
                    "candidate_steps": _steps(candidate.trajectory),
                    "baseline_reply": baseline.trajectory.final_reply,
                    "candidate_reply": candidate.trajectory.final_reply,
                    "baseline_index": baseline.index,
                    "candidate_index": candidate.index,
                    "baseline_passed": baseline.trajectory_passed,
                    "candidate_passed": candidate.trajectory_passed,
                    "issue": render_issue(comparison, scenario) if comparison.alert else "",
                    "repro_href": f"../repro/{comparison.harness}/{scenario.id}/ISSUE.md" if comparison.alert else "",
                }
            )
        (scenario_out / f"{scenario.id}.html").write_text(
            detail.render(
                scenario=scenario,
                pairs=pairs,
                bulletin="Fixture bulletin 001",
            ),
            encoding="utf-8",
        )
    from harnesswatch.repro import write_bundles

    write_bundles(comparisons, scenario_map, scenario_dir, output / "repro")


def _rows(scenarios: list[Scenario], catalog: BehaviorCatalog, comparisons: list[Comparison]) -> list[dict]:
    lookup = {(item.harness, item.scenario_id): item for item in comparisons}
    rows = []
    for harness, label in HARNESS_LABELS.items():
        if harness not in catalog.versions:
            continue
        for role in ("baseline", "candidate"):
            version = catalog.version(harness, role)
            cells = []
            for scenario in scenarios:
                comparison = lookup.get((harness, scenario.id))
                if comparison is None or harness not in scenario.harnesses:
                    cells.append({"status": "na", "label": "—", "sub": "n/a", "href": None, "title": "not in this harness"})
                    continue
                rate = comparison.baseline_rate if role == "baseline" else comparison.candidate_rate
                passes = comparison.baseline_passes if role == "baseline" else comparison.candidate_passes
                total = comparison.baseline_n if role == "baseline" else comparison.candidate_n
                status = _status(comparison, role, rate)
                sub = f"{passes}/{total}"
                if role == "candidate":
                    delta = (comparison.candidate_rate - comparison.baseline_rate) * 100
                    sub = f"{delta:+.0f} pp"
                cells.append(
                    {
                        "status": status,
                        "label": f"{rate * 100:.0f}%",
                        "sub": sub,
                        "href": f"scenarios/{scenario.id}.html#{harness}",
                        "title": f"{scenario.title}: {passes}/{total}",
                    }
                )
            rows.append({"harness": label, "version": version, "role": role, "cells": cells})
    return rows


def _status(comparison: Comparison, role: str, rate: float) -> str:
    if role == "candidate" and comparison.alert:
        return "storm"
    if role == "candidate" and comparison.candidate_rate + 1e-9 < comparison.baseline_rate:
        return "watch"
    if rate >= 0.875:
        return "clear"
    if rate >= 0.5:
        return "watch"
    return "storm"


def _steps(trajectory: Trajectory) -> list[dict[str, str]]:
    steps = []
    for call in trajectory.tool_calls:
        if call.command:
            detail = call.command.replace("\n", " ")
            if len(detail) > 220:
                detail = detail[:217] + "..."
        elif call.paths:
            detail = call.paths[0]
        else:
            detail = ""
        steps.append({"name": call.name, "raw": call.raw_name, "detail": detail})
    return steps


def _measurement_payload(measurement: Measurement) -> dict:
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
