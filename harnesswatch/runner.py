# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Optional live runs of Claude Code and Codex.

Live runs are off by default. ``harnesswatch demo`` never calls a vendor CLI.
When a live run is requested, authentication is an API key:

* Claude Code: ``ANTHROPIC_API_KEY``. ``--bare`` is not used, because bare
  mode skips CLAUDE.md and would make instruction-file scenarios meaningless.
  Isolation comes from a temporary git repo and a temporary ``CLAUDE_CONFIG_DIR``.
* Codex: ``CODEX_API_KEY`` plus a temporary ``CODEX_HOME``. The retired
  ``approval_policy = "untrusted"`` flag is not passed. ``codex exec`` gets
  ``--sandbox workspace-write`` so the agent can edit inside the temp repo.

Consumer-subscription login (``claude setup-token``, ChatGPT account auth)
is not implemented. Read the vendor terms before automating either CLI.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from harnesswatch.parsers import parse_jsonl
from harnesswatch.scenario import Scenario
from harnesswatch.trajectory import Trajectory
from harnesswatch.workspace import capture_diff, prepare_workspace

CLAUDE_DOCS = "https://code.claude.com/docs/en/headless"
CODEX_DOCS = "https://developers.openai.com/codex/noninteractive"


class LiveRunError(RuntimeError):
    pass


@dataclass
class LiveRun:
    trajectory: Trajectory
    command: list[str]
    returncode: int


def run_scenario(
    scenario: Scenario,
    harness: str,
    *,
    claude_bin: str = "claude",
    codex_bin: str = "codex",
    timeout_s: float = 600,
    env: dict[str, str] | None = None,
    max_turns: int = 12,
) -> LiveRun:
    """Run one scenario once in a throwaway git repo. Requires an API key."""

    if harness not in scenario.harnesses:
        raise LiveRunError(f"{scenario.id} does not target {harness}")
    base_env = os.environ.copy()
    if env:
        base_env.update(env)
    with tempfile.TemporaryDirectory(prefix="harnesswatch-") as tmp:
        root = Path(tmp)
        repo = prepare_workspace(root, scenario, harness)
        if harness == "claude-code":
            command, run_env, stdout_text, returncode = _run_claude(
                scenario, repo, root, claude_bin, base_env, timeout_s, max_turns
            )
            transcript = _prefer_session_transcript(root / "claude-config", stdout_text)
            version = base_env.get("HARNESSWATCH_VERSION", "")
            trajectory = parse_jsonl(transcript, harness="claude-code", version=version)
        elif harness == "codex":
            command, run_env, stdout_text, returncode = _run_codex(
                scenario, repo, root, codex_bin, base_env, timeout_s
            )
            transcript = _prefer_session_transcript(root / "codex-home", stdout_text)
            version = base_env.get("HARNESSWATCH_VERSION", "")
            trajectory = parse_jsonl(transcript, harness="codex", version=version)
        else:
            raise LiveRunError(f"unsupported harness {harness}")
        del run_env
        trajectory.diff = capture_diff(repo)
        if not trajectory.version:
            trajectory.version = version or "live"
        # The temporary repo is deleted when this function returns. The diff
        # and the parsed transcript are the durable result.
        return LiveRun(trajectory=trajectory, command=command, returncode=returncode)


def _run_claude(
    scenario: Scenario,
    repo: Path,
    root: Path,
    binary: str,
    env: dict[str, str],
    timeout_s: float,
    max_turns: int,
) -> tuple[list[str], dict[str, str], str, int]:
    if not env.get("ANTHROPIC_API_KEY"):
        raise LiveRunError(
            "Claude Code live runs require ANTHROPIC_API_KEY. "
            "HarnessWatch does not use a Claude subscription (claude setup-token / claude.ai login). "
            f"See {CLAUDE_DOCS}. Offline fixture report: harnesswatch demo."
        )
    config_dir = root / "claude-config"
    config_dir.mkdir()
    session_id = str(uuid.uuid4())
    # Flags follow the Claude Code headless docs: -p / --print, stream-json
    # transcript, explicit permission mode, and project setting sources so a
    # fresh CLAUDE_CONFIG_DIR does not pick up a user's global hooks.
    command = [
        binary,
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
        session_id,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read,Edit,Write,Bash,Glob,Grep",
        "--max-turns",
        str(max_turns),
        "--setting-sources",
        "project",
        "-p",
        scenario.prompt,
    ]
    run_env = _isolated_env(env)
    run_env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    run_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    completed = _spawn(command, repo, run_env, timeout_s)
    return command, run_env, completed.stdout, completed.returncode


def _run_codex(
    scenario: Scenario,
    repo: Path,
    root: Path,
    binary: str,
    env: dict[str, str],
    timeout_s: float,
) -> tuple[list[str], dict[str, str], str, int]:
    if not env.get("CODEX_API_KEY"):
        raise LiveRunError(
            "Codex live runs require CODEX_API_KEY. "
            "HarnessWatch does not copy ChatGPT account auth (~/.codex/auth.json). "
            f"See {CODEX_DOCS}. Offline fixture report: harnesswatch demo."
        )
    codex_home = root / "codex-home"
    codex_home.mkdir()
    last_message = root / "last-message.txt"
    # codex exec is the documented non-interactive entrypoint. --json streams
    # JSONL events. workspace-write is the documented sandbox for edits.
    # --full-auto and approval_policy=untrusted are not passed.
    command = [
        binary,
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-last-message",
        str(last_message),
        scenario.prompt,
    ]
    run_env = _isolated_env(env)
    run_env["CODEX_HOME"] = str(codex_home)
    completed = _spawn(command, repo, run_env, timeout_s)
    return command, run_env, completed.stdout, completed.returncode


def _isolated_env(env: dict[str, str]) -> dict[str, str]:
    cleaned = dict(env)
    # Keep the API key. Drop variables that would point the CLI at a real home
    # directory of session logs. HOME itself stays so the OS can resolve a
    # shell; the vendor config dirs are overridden explicitly.
    cleaned.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return cleaned


def _spawn(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise LiveRunError(
            f"Could not execute {command[0]!r}. Live CLI runs are optional; use harnesswatch demo."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LiveRunError(f"{command[0]} exceeded {timeout_s:.0f}s") from exc


def _prefer_session_transcript(config_root: Path, stdout_text: str) -> str:
    """Prefer an on-disk session JSONL when the CLI wrote one, else stdout."""

    sessions = sorted(config_root.rglob("*.jsonl"))
    if not sessions:
        return stdout_text
    # The newest session file is the run we just started inside an empty config dir.
    newest = max(sessions, key=lambda path: path.stat().st_mtime)
    disk = newest.read_text(encoding="utf-8", errors="replace")
    if disk.strip():
        return disk
    return stdout_text
