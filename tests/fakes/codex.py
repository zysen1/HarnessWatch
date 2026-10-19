#!/usr/bin/env python3
# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Stand-in for codex(1). Writes a rollout JSONL and edits the repo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    probe = os.environ.get("HARNESSWATCH_PROBE")
    home = os.environ.get("CODEX_HOME", "")
    cwd = Path.cwd()
    git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    calc = cwd / "src" / "calc.py"
    if calc.exists():
        text = calc.read_text(encoding="utf-8")
        if "def multiply" not in text:
            calc.write_text(text + "\n\ndef multiply(a, b):\n    return a * b\n", encoding="utf-8")
    if probe:
        Path(probe).write_text(
            json.dumps(
                {
                    "argv": sys.argv[1:],
                    "cwd": str(cwd),
                    "codex_home": home,
                    "git": git.stdout.strip() == "true",
                    "agents": (cwd / "AGENTS.md").is_file(),
                    "key_present": bool(os.environ.get("CODEX_API_KEY")),
                }
            ),
            encoding="utf-8",
        )
    lines = [
        {
            "timestamp": "2026-10-19T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "fake-thread", "cwd": str(cwd), "cli_version": "fake"},
        },
        {
            "timestamp": "2026-10-19T00:00:00Z",
            "type": "turn_context",
            "payload": {"approval_policy": "on-request", "sandbox_policy": "workspace-write"},
        },
        {
            "timestamp": "2026-10-19T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call_read",
                "name": "read_file",
                "arguments": json.dumps({"path": "AGENTS.md"}),
            },
        },
        {
            "timestamp": "2026-10-19T00:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call_patch",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** Update File: src/calc.py\n*** End Patch",
            },
        },
        {
            "timestamp": "2026-10-19T00:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Added multiply to src/calc.py without a verbose comment."}],
            },
        },
    ]
    text = "".join(json.dumps(line) + "\n" for line in lines)
    if home:
        folder = Path(home) / "sessions" / "2026" / "10" / "19"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rollout-fake.jsonl").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
