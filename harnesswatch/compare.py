# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Compare a baseline version with a candidate version for one scenario."""

from __future__ import annotations

from dataclasses import dataclass, field

from harnesswatch.recording import BehaviorCatalog, RecordedRun
from harnesswatch.scenario import Scenario
from harnesswatch.stats import bootstrap_mean_diff, fisher_exact_worse, metric_regresses


@dataclass
class MetricComparison:
    name: str
    direction: str
    baseline_mean: float
    candidate_mean: float
    observed_diff: float
    ci_low: float
    ci_high: float
    alert: bool


@dataclass
class Comparison:
    harness: str
    scenario_id: str
    title: str
    category: str
    baseline_version: str
    candidate_version: str
    baseline_passes: int
    baseline_n: int
    candidate_passes: int
    candidate_n: int
    fisher_p: float
    rate_alert: bool
    metrics: list[MetricComparison] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    baseline_runs: list[RecordedRun] = field(default_factory=list)
    candidate_runs: list[RecordedRun] = field(default_factory=list)

    @property
    def alert(self) -> bool:
        return bool(self.reasons)

    @property
    def baseline_rate(self) -> float:
        return self.baseline_passes / self.baseline_n if self.baseline_n else 0.0

    @property
    def candidate_rate(self) -> float:
        return self.candidate_passes / self.candidate_n if self.candidate_n else 0.0

    def representative(self, role: str) -> RecordedRun:
        runs = self.baseline_runs if role == "baseline" else self.candidate_runs
        if role == "candidate" and self.alert:
            for run in runs:
                if not run.trajectory_passed:
                    return run
        else:
            for run in runs:
                if run.trajectory_passed:
                    return run
        return runs[0]


def compare_catalog(
    scenarios: dict[str, Scenario],
    catalog: BehaviorCatalog,
    grouped: dict[tuple[str, str, str], list[RecordedRun]],
) -> list[Comparison]:
    comparisons: list[Comparison] = []
    for case in catalog.cases:
        scenario = scenarios[case.scenario]
        baseline_version = catalog.version(case.harness, "baseline")
        candidate_version = catalog.version(case.harness, "candidate")
        baseline_runs = grouped[(case.harness, baseline_version, case.scenario)]
        candidate_runs = grouped[(case.harness, candidate_version, case.scenario)]
        comparisons.append(
            _compare_pair(
                scenario,
                case.harness,
                baseline_version,
                candidate_version,
                baseline_runs,
                candidate_runs,
                alpha=catalog.alpha,
                n_resamples=catalog.bootstrap_resamples,
            )
        )
    return comparisons


def _compare_pair(
    scenario: Scenario,
    harness: str,
    baseline_version: str,
    candidate_version: str,
    baseline_runs: list[RecordedRun],
    candidate_runs: list[RecordedRun],
    *,
    alpha: float,
    n_resamples: int,
) -> Comparison:
    baseline_passes = sum(run.trajectory_passed for run in baseline_runs)
    candidate_passes = sum(run.trajectory_passed for run in candidate_runs)
    fisher_p = fisher_exact_worse(baseline_passes, len(baseline_runs), candidate_passes, len(candidate_runs))
    rate_alert = fisher_p < alpha and candidate_passes < baseline_passes
    reasons: list[str] = []
    if rate_alert:
        reasons.append(
            f"pass rate {baseline_passes}/{len(baseline_runs)} → "
            f"{candidate_passes}/{len(candidate_runs)} "
            f"(Fisher one-sided p={fisher_p:.4g})"
        )
    metrics: list[MetricComparison] = []
    for watch in scenario.watch_metrics:
        base_values = [run.metrics[watch.name] for run in baseline_runs]
        cand_values = [run.metrics[watch.name] for run in candidate_runs]
        boot = bootstrap_mean_diff(base_values, cand_values, n_resamples=n_resamples, seed=0, alpha=alpha)
        alert = metric_regresses(watch.direction, boot, metric=watch.name)
        metrics.append(
            MetricComparison(
                name=watch.name,
                direction=watch.direction,
                baseline_mean=sum(base_values) / len(base_values),
                candidate_mean=sum(cand_values) / len(cand_values),
                observed_diff=boot.observed_diff,
                ci_low=boot.ci_low,
                ci_high=boot.ci_high,
                alert=alert,
            )
        )
        if alert:
            reasons.append(
                f"{watch.name} mean {sum(base_values) / len(base_values):.4g} → "
                f"{sum(cand_values) / len(cand_values):.4g} "
                f"(95% bootstrap CI {boot.ci_low:.4g} to {boot.ci_high:.4g})"
            )
    return Comparison(
        harness=harness,
        scenario_id=scenario.id,
        title=scenario.title,
        category=scenario.category,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        baseline_passes=baseline_passes,
        baseline_n=len(baseline_runs),
        candidate_passes=candidate_passes,
        candidate_n=len(candidate_runs),
        fisher_p=fisher_p,
        rate_alert=rate_alert,
        metrics=metrics,
        reasons=reasons,
        baseline_runs=baseline_runs,
        candidate_runs=candidate_runs,
    )
