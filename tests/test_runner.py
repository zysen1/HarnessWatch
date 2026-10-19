# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from harnesswatch.runner import LiveRunError, run_scenario
from harnesswatch.scenario import load_scenarios

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / "tests" / "fakes" / "claude.py"
CODEX = ROOT / "tests" / "fakes" / "codex.py"


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


def _scenario():
    return next(scenario for scenario in load_scenarios(ROOT / "scenarios") if scenario.id == "read-agents-md")


def test_live_run_refuses_without_an_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    probe = tmp_path / "probe.json"
    monkeypatch.setenv("HARNESSWATCH_PROBE", str(probe))
    with pytest.raises(LiveRunError, match="ANTHROPIC_API_KEY"):
        run_scenario(_scenario(), "claude-code", claude_bin=str(CLAUDE))
    assert not probe.exists()
    with pytest.raises(LiveRunError, match="CODEX_API_KEY"):
        run_scenario(_scenario(), "codex", codex_bin=str(CODEX))
    assert not probe.exists()


def test_fake_claude_runs_inside_a_temp_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    probe = tmp_path / "probe.json"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("HARNESSWATCH_PROBE", str(probe))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "should-be-stripped")
    result = run_scenario(_scenario(), "claude-code", claude_bin=str(CLAUDE))
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload["git"] is True
    assert payload["agents"] is True
    assert payload["key_present"] is True
    assert payload["oauth_present"] is False
    assert "harnesswatch-" in payload["config"]
    assert "--bare" not in payload["argv"]
    assert payload["argv"].count("-p") == 1
    assert "stream-json" in payload["argv"]
    assert "acceptEdits" in payload["argv"]
    assert "project" in payload["argv"]
    assert "def multiply" in result.trajectory.diff
    assert [call.name for call in result.trajectory.tool_calls] == ["Read", "Edit"]
    joined = " ".join(result.command)
    assert "setup-token" not in joined
    assert "untrusted" not in joined


def test_fake_codex_uses_exec_json_and_workspace_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    probe = tmp_path / "probe.json"
    monkeypatch.setenv("CODEX_API_KEY", "test-key")
    monkeypatch.setenv("HARNESSWATCH_PROBE", str(probe))
    result = run_scenario(_scenario(), "codex", codex_bin=str(CODEX))
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload["git"] is True
    assert payload["agents"] is True
    assert "harnesswatch-" in payload["codex_home"]
    argv = payload["argv"]
    assert argv[0] == "exec"
    assert "--json" in argv
    assert "workspace-write" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert "untrusted" not in argv
    assert "--yolo" not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert result.trajectory.approval_policy == "on-request"
    assert "def multiply" in result.trajectory.diff
