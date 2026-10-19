# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Small-sample tests for k repeated scenario runs.

Fisher's exact test is the hypergeometric probability of the 2x2 table of
passes and failures. The percentile bootstrap is used for continuous
metrics (tokens, cache hit rate, permission-prompt counts). Both are
implemented with the standard library so a report build stays offline.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


def fisher_exact_worse(base_pass: int, base_n: int, cand_pass: int, cand_n: int) -> float:
    """One-sided Fisher's exact p-value for "the candidate passes less often".

    The margins are fixed, so the candidate pass count is Hypergeometric
    with population ``base_n + cand_n``, ``base_pass + cand_pass`` successes,
    and ``cand_n`` draws. The p-value is P(X <= cand_pass). When the
    candidate pass rate is not lower, the p-value is 1.
    """

    if base_n <= 0 or cand_n <= 0:
        return 1.0
    if cand_pass / cand_n >= base_pass / base_n:
        return 1.0
    total_n = base_n + cand_n
    total_pass = base_pass + cand_pass
    if math.comb(total_n, cand_n) == 0:
        return 1.0
    lower = max(0, total_pass - base_n)
    upper = min(cand_pass, total_pass, cand_n)
    tail = 0.0
    for k in range(lower, upper + 1):
        tail += _hypergeom_pmf(k, total_n, total_pass, cand_n)
    return min(1.0, tail)


def _hypergeom_pmf(k: int, total_n: int, total_pass: int, draws: int) -> float:
    if k < 0 or draws < 0 or total_pass < 0 or total_n < 0:
        return 0.0
    failures = total_n - total_pass
    if k > total_pass or draws - k > failures or draws > total_n:
        return 0.0
    ways = math.comb(total_pass, k) * math.comb(failures, draws - k)
    return ways / math.comb(total_n, draws)


@dataclass(frozen=True)
class BootstrapResult:
    observed_diff: float
    ci_low: float
    ci_high: float
    n_resamples: int


def bootstrap_mean_diff(
    baseline: list[float],
    candidate: list[float],
    *,
    n_resamples: int = 4000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Percentile confidence interval for mean(candidate) - mean(baseline)."""

    if not baseline or not candidate:
        return BootstrapResult(0.0, 0.0, 0.0, 0)
    rng = random.Random(seed)
    nb = len(baseline)
    nc = len(candidate)
    base_mean = sum(baseline) / nb
    cand_mean = sum(candidate) / nc
    diffs = []
    for _ in range(n_resamples):
        sample_b = sum(baseline[rng.randrange(nb)] for _ in range(nb)) / nb
        sample_c = sum(candidate[rng.randrange(nc)] for _ in range(nc)) / nc
        diffs.append(sample_c - sample_b)
    diffs.sort()
    low_index = int((alpha / 2) * n_resamples)
    high_index = min(n_resamples - 1, int((1 - alpha / 2) * n_resamples) - 1)
    if high_index < low_index:
        high_index = low_index
    return BootstrapResult(cand_mean - base_mean, diffs[low_index], diffs[high_index], n_resamples)


# A CI that excludes zero is not enough when every run has the same token
# count: a 50-token shift would look "significant". These floors are the
# smallest change HarnessWatch will call a regression for that metric.
PRACTICAL_MIN_ABS = {
    "input_tokens": 2000.0,
    "output_tokens": 500.0,
    "cache_creation_tokens": 2000.0,
    "cache_hit_rate": 0.10,
    "permission_prompts": 1.0,
}


def metric_regresses(
    direction: str,
    result: BootstrapResult,
    *,
    metric: str,
) -> bool:
    """True when the bootstrap CI excludes zero in the worse direction and the shift is large enough to matter."""

    floor = PRACTICAL_MIN_ABS.get(metric, 0.0)
    if abs(result.observed_diff) < floor:
        return False
    if direction == "lower_is_better":
        return result.ci_low > 0
    if direction == "higher_is_better":
        return result.ci_high < 0
    raise ValueError(f"unknown metric direction: {direction}")
