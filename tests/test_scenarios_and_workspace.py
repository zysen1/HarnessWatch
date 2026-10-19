# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from harnesswatch.scenario import load_scenarios
from harnesswatch.workspace import capture_diff, prepare_workspace

ROOT = Path(__file__).resolve().parents[1]


def test_twelve_scenarios_cover_the_report_categories():
    scenarios = load_scenarios(ROOT / "scenarios")
    assert len(scenarios) == 12
    categories = {scenario.category for scenario in scenarios}
    assert categories == {"instruction", "tool", "language", "permission", "cost", "diff"}
    assert any("claude-code" in scenario.harnesses and "codex" in scenario.harnesses for scenario in scenarios)
    approval = next(scenario for scenario in scenarios if scenario.id == "approval-policy-untrusted")
    assert approval.harnesses == ("codex",)


def test_prepare_workspace_is_an_isolated_git_repo(tmp_path: Path):
    scenario = next(scenario for scenario in load_scenarios(ROOT / "scenarios") if scenario.id == "read-agents-md")
    repo = prepare_workspace(tmp_path, scenario, "claude-code")
    assert (repo / ".git").is_dir()
    assert (repo / "AGENTS.md").is_file()
    assert (repo / "src" / "calc.py").is_file()
    calc = repo / "src" / "calc.py"
    calc.write_text(calc.read_text(encoding="utf-8") + "\n\ndef multiply(a, b):\n    return a * b\n", encoding="utf-8")
    diff = capture_diff(repo)
    assert "def multiply" in diff
    assert "AGENTS.md" not in diff
