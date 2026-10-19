# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Write synthetic session JSONL fixtures from fixtures/behaviors.yaml.

The files follow the Claude Code session shape and the Codex rollout shape.
They are not recordings of a live vendor CLI. Re-run this script from the
repository root after editing a scenario or a behavior label:

    python scripts/build_fixtures.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnesswatch.recording import load_catalog
from harnesswatch.scenario import load_scenarios

ZH = "已在 src/calc.py 加入 multiply 函数，没有添加冗长注释。"
EN = "Added multiply to src/calc.py without a verbose comment."
NESTED_EN = "Added slugify to pkg/util.py with a one-line docstring."


def main() -> None:
    catalog = load_catalog(ROOT / "fixtures" / "behaviors.yaml")
    scenarios = {scenario.id: scenario for scenario in load_scenarios(ROOT / "scenarios")}
    destination = ROOT / "fixtures" / "recordings"
    if destination.exists():
        shutil.rmtree(destination)
    for case in catalog.cases:
        scenario = scenarios[case.scenario]
        if case.harness not in scenario.harnesses:
            raise SystemExit(f"{case.harness} is not a harness of {case.scenario}")
        if len(case.candidate_pattern) != catalog.k or set(case.candidate_pattern) - {"P", "F"}:
            raise SystemExit(f"bad pattern for {case.harness}/{case.scenario}")
        for role, pattern in (("baseline", "P" * catalog.k), ("candidate", case.candidate_pattern)):
            version = catalog.version(case.harness, role)
            folder = destination / case.harness / version / case.scenario
            folder.mkdir(parents=True)
            for index, mark in enumerate(pattern):
                passed = mark == "P"
                usage_index = index if role == "baseline" else catalog.k - 1 - index
                transcript, diff = render_run(
                    harness=case.harness,
                    scenario_id=case.scenario,
                    version=version,
                    index=index,
                    passed=passed,
                    usage_index=usage_index,
                )
                stem = f"run-{index:02d}"
                (folder / f"{stem}.jsonl").write_text(transcript, encoding="utf-8")
                (folder / f"{stem}.diff").write_text(diff, encoding="utf-8")
    print(f"wrote {destination}")


def render_run(*, harness: str, scenario_id: str, version: str, index: int, passed: bool, usage_index: int) -> tuple[str, str]:
    usage = _usage(scenario_id, passed, usage_index)
    reply = _reply(scenario_id, passed)
    prompts = _permission_count(scenario_id, passed)
    policy = _approval_policy(scenario_id, harness, passed)
    calls = _calls(scenario_id, harness, passed)
    diff = _diff(scenario_id, passed)
    session = f"{harness[:2]}-{scenario_id}-{index:02d}"
    if harness == "claude-code":
        lines = _claude_lines(session, version, reply, usage, prompts, calls)
    else:
        lines = _codex_lines(session, version, reply, usage, prompts, policy, calls)
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    return text, diff


def _usage(scenario_id: str, passed: bool, index: int) -> dict[str, int]:
    if scenario_id == "cache-hit-rate" and not passed:
        return {"input": 26000 + index * 10, "output": 300, "cache_read": 200, "cache_creation": 18000, "reasoning": 30}
    if scenario_id == "token-budget" and not passed:
        return {"input": 28000 + index * 20, "output": 420, "cache_read": 500, "cache_creation": 20000 + index * 15, "reasoning": 40}
    return {
        "input": 8000 + ((index * 37) % 250),
        "output": 160 + index * 3,
        "cache_read": 24000 + ((index * 13) % 100),
        "cache_creation": 180 + (index % 3) * 10,
        "reasoning": 20 + index,
    }


def _reply(scenario_id: str, passed: bool) -> str:
    if scenario_id == "nested-instruction":
        return NESTED_EN
    if scenario_id == "reply-in-chinese":
        return ZH if passed else EN
    if scenario_id == "reply-in-english":
        return EN if passed else ZH
    return EN


def _permission_count(scenario_id: str, passed: bool) -> int:
    if scenario_id == "permission-prompt-budget" and not passed:
        return 4
    if scenario_id == "approval-policy-untrusted" and not passed:
        return 3
    return 0


def _approval_policy(scenario_id: str, harness: str, passed: bool) -> str | None:
    if harness != "codex":
        return None
    if scenario_id == "approval-policy-untrusted" and not passed:
        return "on-request"
    if scenario_id == "approval-policy-untrusted":
        return "untrusted"
    return "on-request"


def _calls(scenario_id: str, harness: str, passed: bool) -> list[dict]:
    edit_path = "pkg/util.py" if scenario_id == "nested-instruction" else "src/calc.py"
    if scenario_id == "read-agents-md":
        calls = []
        if passed:
            calls.append(_read(harness, "AGENTS.md"))
        calls.append(_edit(harness, edit_path))
        return calls
    if scenario_id == "nested-instruction":
        calls = []
        if passed:
            nested = "pkg/CLAUDE.md" if harness == "claude-code" else "pkg/AGENTS.md"
            calls.append(_read(harness, nested))
        calls.append(_edit(harness, edit_path))
        return calls
    if scenario_id == "no-sed-edits" and not passed:
        return [_bash(harness, "sed -i 's/return a + b/return a * b/' src/calc.py")]
    if scenario_id == "edit-not-bash-rewrite" and not passed:
        return [_bash(harness, "cat > src/calc.py <<'EOF'\ndef multiply(a, b):\n    return a * b\nEOF")]
    if scenario_id == "diff-scope" and not passed:
        return [_edit(harness, "src/calc.py"), _edit(harness, "README.md")]
    return [_edit(harness, edit_path)]


def _read(harness: str, path: str) -> dict:
    if harness == "claude-code":
        return {"tool": "Read", "input": {"file_path": path}}
    return {"tool": "read_file", "arguments": {"path": path}}


def _edit(harness: str, path: str) -> dict:
    if harness == "claude-code":
        return {
            "tool": "Edit",
            "input": {"file_path": path, "old_string": "return a + b", "new_string": "return a * b"},
        }
    patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-return a + b\n+return a * b\n*** End Patch"
    return {"tool": "apply_patch", "input": patch}


def _bash(harness: str, command: str) -> dict:
    if harness == "claude-code":
        return {"tool": "Bash", "input": {"command": command}}
    return {"tool": "shell_command", "arguments": {"command": command}}


def _diff(scenario_id: str, passed: bool) -> str:
    if scenario_id == "nested-instruction":
        return _nested_diff()
    comment = scenario_id == "no-verbose-comments" and not passed
    extra = scenario_id == "diff-scope" and not passed
    return _multiply_diff(comment=comment, extra=extra)


def _multiply_diff(*, comment: bool, extra: bool) -> str:
    comment_line = "+    # This function multiplies two numbers together and returns the product.\n" if comment else ""
    body = (
        "diff --git a/src/calc.py b/src/calc.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,2 +1,6 @@\n"
        " def add(a, b):\n"
        "     return a + b\n"
        "+\n"
        "+def multiply(a, b):\n"
        f"{comment_line}"
        "+    return a * b\n"
    )
    if extra:
        body += (
            "diff --git a/README.md b/README.md\n"
            "index 3333333..4444444 100644\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1,2 @@\n"
            " # Tiny calc\n"
            "+Edited an unrelated file.\n"
        )
    return body


def _nested_diff() -> str:
    return (
        "diff --git a/pkg/util.py b/pkg/util.py\n"
        "index 5555555..6666666 100644\n"
        "--- a/pkg/util.py\n"
        "+++ b/pkg/util.py\n"
        "@@ -1,2 +1,5 @@\n"
        " def ping():\n"
        "     return \"pong\"\n"
        "+\n"
        "+def slugify(text):\n"
        "+    \"\"\"Return a lowercase slug.\"\"\"\n"
        "+    return text.strip().lower().replace(\" \", \"-\")\n"
    )


def _claude_lines(session: str, version: str, reply: str, usage: dict, prompts: int, calls: list[dict]) -> list[dict]:
    blocks = []
    for index, call in enumerate(calls):
        blocks.append(
            {
                "type": "tool_use",
                "id": f"toolu_{session}_{index}",
                "name": call["tool"],
                "input": call["input"],
            }
        )
    blocks.append({"type": "text", "text": reply})
    usage_block = {
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "cache_read_input_tokens": usage["cache_read"],
        "cache_creation_input_tokens": usage["cache_creation"],
    }
    lines = [
        {
            "type": "user",
            "uuid": f"{session}-u",
            "parentUuid": None,
            "timestamp": "2026-10-19T00:00:00.000Z",
            "sessionId": session,
            "version": version,
            "cwd": "/tmp/harnesswatch/repo",
            "gitBranch": "main",
            "message": {"role": "user", "content": "scenario prompt"},
        },
        {
            "type": "assistant",
            "uuid": f"{session}-a",
            "parentUuid": f"{session}-u",
            "timestamp": "2026-10-19T00:00:01.000Z",
            "sessionId": session,
            "version": version,
            "cwd": "/tmp/harnesswatch/repo",
            "message": {
                "id": f"msg_{session}",
                "role": "assistant",
                "model": "claude-fixture",
                "content": blocks,
                "usage": usage_block,
                "stop_reason": "end_turn",
            },
        },
    ]
    for prompt_index in range(prompts):
        lines.append(
            {
                "type": "system",
                "subtype": "permission_request",
                "uuid": f"{session}-p{prompt_index}",
                "sessionId": session,
                "timestamp": f"2026-10-19T00:00:02.{prompt_index:03d}Z",
                "tool_name": "Bash",
            }
        )
    lines.append(
        {
            "type": "result",
            "subtype": "success",
            "session_id": session,
            "result": reply,
            "usage": usage_block,
        }
    )
    return lines


def _codex_lines(session: str, version: str, reply: str, usage: dict, prompts: int, policy: str | None, calls: list[dict]) -> list[dict]:
    stamp = "2026-10-19T00:00:00Z"
    lines = [
        {
            "timestamp": stamp,
            "type": "session_meta",
            "payload": {
                "id": session,
                "cwd": "/tmp/harnesswatch/repo",
                "cli_version": version,
                "originator": "codex",
                "model_provider": "openai",
                "source": "exec",
            },
        },
        {
            "timestamp": stamp,
            "type": "turn_context",
            "payload": {
                "model": "gpt-5-codex-fixture",
                "approval_policy": policy,
                "sandbox_policy": "workspace-write",
                "cwd": "/tmp/harnesswatch/repo",
            },
        },
        {
            "timestamp": stamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "scenario prompt"}],
            },
        },
    ]
    for index, call in enumerate(calls):
        call_id = f"call_{session}_{index}"
        if call["tool"] == "apply_patch":
            lines.append(
                {
                    "timestamp": stamp,
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "call_id": call_id,
                        "name": "apply_patch",
                        "input": call["input"],
                    },
                }
            )
        else:
            lines.append(
                {
                    "timestamp": stamp,
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": call["tool"],
                        "arguments": json.dumps(call["arguments"]),
                    },
                }
            )
    lines.append(
        {
            "timestamp": stamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": reply}],
            },
        }
    )
    for prompt_index in range(prompts):
        lines.append(
            {
                "timestamp": stamp,
                "type": "event_msg",
                "payload": {
                    "type": "exec_approval_request",
                    "call_id": f"approval_{session}_{prompt_index}",
                    "command": ["bash", "-lc", "true"],
                },
            }
        )
    lines.append(
        {
            "timestamp": stamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": usage["input"],
                        "cached_input_tokens": usage["cache_read"],
                        "output_tokens": usage["output"],
                        "reasoning_output_tokens": usage["reasoning"],
                    }
                },
            },
        }
    )
    return lines


if __name__ == "__main__":
    main()
