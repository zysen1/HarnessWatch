# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0

from harnesswatch.stats import bootstrap_mean_diff, fisher_exact_worse, metric_regresses


def test_fisher_separates_a_real_drop_from_one_flake():
    sharp = fisher_exact_worse(8, 8, 3, 8)
    flake = fisher_exact_worse(8, 8, 7, 8)
    assert sharp < 0.05
    assert flake > 0.2
    assert fisher_exact_worse(8, 8, 8, 8) == 1.0
    assert fisher_exact_worse(7, 8, 8, 8) == 1.0


def test_bootstrap_flags_a_token_spike_and_ignores_a_matched_sample():
    baseline = [8000 + (i * 37) % 250 for i in range(8)]
    matched = list(reversed(baseline))
    spike = [28000 + i * 20 for i in range(8)]
    quiet = bootstrap_mean_diff(baseline, matched, n_resamples=500, seed=0)
    loud = bootstrap_mean_diff(baseline, spike, n_resamples=500, seed=0)
    assert not metric_regresses("lower_is_better", quiet, metric="input_tokens")
    assert metric_regresses("lower_is_better", loud, metric="input_tokens")
    assert loud.ci_low > 0
