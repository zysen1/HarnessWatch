# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Score detection and false positives against labeled fixture regressions."""

from __future__ import annotations

from dataclasses import dataclass

from harnesswatch.compare import Comparison
from harnesswatch.recording import BehaviorCatalog


@dataclass(frozen=True)
class Measurement:
    dataset: str
    k: int
    alpha: float
    bootstrap_resamples: int
    regressions_total: int
    regressions_detected: int
    stable_total: int
    false_positives: int
    misses: tuple[str, ...]
    false_positive_ids: tuple[str, ...]

    @property
    def detection_rate(self) -> float:
        if self.regressions_total == 0:
            return 0.0
        return self.regressions_detected / self.regressions_total

    @property
    def false_positive_rate(self) -> float:
        if self.stable_total == 0:
            return 0.0
        return self.false_positives / self.stable_total


def measure(catalog: BehaviorCatalog, comparisons: list[Comparison], *, dataset: str) -> Measurement:
    by_key = {(item.harness, item.scenario_id): item for item in comparisons}
    misses: list[str] = []
    false_positive_ids: list[str] = []
    regressions_total = 0
    detected = 0
    stable_total = 0
    for case in catalog.cases:
        key = f"{case.harness}/{case.scenario}"
        comparison = by_key[(case.harness, case.scenario)]
        if case.label == "regression":
            regressions_total += 1
            if comparison.alert:
                detected += 1
            else:
                misses.append(key)
        elif case.label == "stable":
            stable_total += 1
            if comparison.alert:
                false_positive_ids.append(key)
        else:
            raise ValueError(case.label)
    return Measurement(
        dataset=dataset,
        k=catalog.k,
        alpha=catalog.alpha,
        bootstrap_resamples=catalog.bootstrap_resamples,
        regressions_total=regressions_total,
        regressions_detected=detected,
        stable_total=stable_total,
        false_positives=len(false_positive_ids),
        misses=tuple(misses),
        false_positive_ids=tuple(false_positive_ids),
    )
