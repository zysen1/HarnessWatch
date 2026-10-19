# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from harnesswatch.cli import build_demo

ROOT = Path(__file__).resolve().parents[1]


def test_demo_detects_injected_regressions_without_false_positives(tmp_path: Path):
    out = tmp_path / "site"
    measurement = build_demo(ROOT, out)
    assert measurement.regressions_total == 10
    assert measurement.regressions_detected == 10
    assert measurement.false_positives == 0
    assert measurement.stable_total >= 10
    assert measurement.misses == ()
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "read-agents-md" in index
    assert "storm" in index
    detail = (out / "scenarios" / "read-agents-md.html").read_text(encoding="utf-8")
    assert "Copy issue draft" in detail
    assert "AGENTS.md" in detail
    issue = (out / "repro" / "claude-code" / "read-agents-md" / "ISSUE.md").read_text(encoding="utf-8")
    assert "2.1.70-fixture" in issue
    assert "2.1.88-fixture" in issue
    assert "Read AGENTS.md" in issue
    bundle = out / "repro" / "claude-code" / "read-agents-md"
    assert (bundle / "baseline.jsonl").is_file()
    assert (bundle / "candidate.jsonl").is_file()
    assert (bundle / "scenario.yaml").is_file()
    assert (bundle / "manifest.json").is_file()
    # Stable controls do not get a repro bundle.
    assert not (out / "repro" / "claude-code" / "reply-in-english").exists()


def test_committed_docs_match_a_fresh_demo(tmp_path: Path):
    out = tmp_path / "site"
    build_demo(ROOT, out)
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        committed = ROOT / "docs" / path.relative_to(out)
        assert committed.is_file(), committed
        assert committed.read_bytes() == path.read_bytes()
