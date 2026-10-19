#!/usr/bin/env python3
# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Stand-in for claude(1). Speaks a tiny stream-json transcript and edits the repo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    probe = os.environ.get("HARNESSWATCH_PROBE")
    config = os.environ.get("CLAUDE_CONFIG_DIR", "")
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
                    "config": config,
                    "git": git.stdout.strip() == "true",
                    "agents": (cwd / "AGENTS.md").is_file(),
                    "key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
                    "oauth_present": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ,
                }
            ),
            encoding="utf-8",
        )
    session = "00000000-0000-4000-8000-000000000001"
    for arg, value in zip(sys.argv, sys.argv[1:]):
        if arg == "--session-id":
            session = value
    record = {
        "type": "assistant",
        "sessionId": session,
        "version": "fake",
        "message": {
            "id": "msg_fake",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_fake_read", "name": "Read", "input": {"file_path": "AGENTS.md"}},
                {
                    "type": "tool_use",
                    "id": "toolu_fake_edit",
                    "name": "Edit",
                    "input": {"file_path": "src/calc.py", "old_string": "x", "new_string": "y"},
                },
                {"type": "text", "text": "Added multiply to src/calc.py without a verbose comment."},
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    if config:
        folder = Path(config) / "projects" / "work"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{session}.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "result": "ok", "session_id": session}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
