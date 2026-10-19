# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Isolated temporary git repositories for one scenario run."""

from __future__ import annotations

import subprocess
from pathlib import Path

from harnesswatch.scenario import Scenario, SeedFile


class WorkspaceError(RuntimeError):
    pass


def prepare_workspace(root: Path, scenario: Scenario, harness: str) -> Path:
    """Create ``root/repo`` as a fresh git repository containing the scenario files.

    ``root`` is owned by the caller (usually a TemporaryDirectory). Nothing is
    written outside it, and git identity is stored only in this repo's local
    config.
    """

    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=False)
    _write_files(repo, scenario.seed_files)
    _write_files(repo, scenario.instruction_files(harness))
    _git(repo, ["init", "-b", "main"])
    _git(repo, ["config", "user.email", "harnesswatch@example.com"])
    _git(repo, ["config", "user.name", "HarnessWatch"])
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-m", f"scenario {scenario.id}", "--allow-empty"])
    return repo


def capture_diff(repo: Path) -> str:
    """Unified diff of tracked edits plus the contents of untracked files."""

    tracked = _git(repo, ["diff", "HEAD"], check=False).stdout
    status = _git(repo, ["status", "--porcelain"], check=False).stdout
    extra: list[str] = []
    for line in status.splitlines():
        if len(line) < 4 or not line.startswith("??"):
            continue
        rel = line[3:].strip()
        path = repo / rel
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace")
            extra.append(f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n{body}")
    parts = [part for part in (tracked, "\n".join(extra)) if part.strip()]
    return "\n".join(parts)


def _write_files(repo: Path, files: tuple[SeedFile, ...]) -> None:
    for item in files:
        destination = repo / item.path
        if not _is_relative_to(destination, repo):
            raise WorkspaceError(f"seed path escapes the repo: {item.path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(item.content, encoding="utf-8")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise WorkspaceError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result
