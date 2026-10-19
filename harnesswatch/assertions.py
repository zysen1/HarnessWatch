# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Trajectory-level assertions.

A run passes when every assertion that applies to its harness passes.
Assertions look at tool choice, instruction-file reads, reply language,
permission prompts, token and cache counters, and the final git diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from harnesswatch.scenario import AssertionSpec
from harnesswatch.trajectory import Trajectory

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class AssertionResult:
    kind: str
    passed: bool
    detail: str


def evaluate_assertions(trajectory: Trajectory, assertions: tuple[AssertionSpec, ...]) -> list[AssertionResult]:
    return [_evaluate_one(trajectory, assertion) for assertion in assertions]


def run_passed(results: list[AssertionResult]) -> bool:
    return all(result.passed for result in results)


def _evaluate_one(trajectory: Trajectory, assertion: AssertionSpec) -> AssertionResult:
    kind = assertion.kind
    params = assertion.params
    if kind == "read_file":
        return _read_file(trajectory, params)
    if kind == "tool_any_of":
        return _tool_any_of(trajectory, params)
    if kind == "command_forbidden":
        return _command_forbidden(trajectory, params)
    if kind == "reply_language":
        return _reply_language(trajectory, params)
    if kind == "permission_prompts_max":
        return _permission_max(trajectory, params)
    if kind == "approval_policy":
        return _approval_policy(trajectory, params)
    if kind == "diff_contains":
        return _diff_contains(trajectory, params, must=True)
    if kind == "diff_excludes":
        return _diff_contains(trajectory, params, must=False)
    if kind == "diff_paths_only":
        return _diff_paths_only(trajectory, params)
    if kind == "cache_hit_min":
        return _cache_hit_min(trajectory, params)
    if kind == "input_tokens_max":
        return _input_tokens_max(trajectory, params)
    return AssertionResult(kind, False, f"unknown assertion kind {kind}")


def _read_file(trajectory: Trajectory, params: dict) -> AssertionResult:
    expected = str(params["path"])
    found = [path for path in trajectory.files_read if _path_matches(path, expected)]
    if found:
        return AssertionResult("read_file", True, f"read {found[0]}")
    return AssertionResult("read_file", False, f"never read {expected}")


def _tool_any_of(trajectory: Trajectory, params: dict) -> AssertionResult:
    allowed = {str(name) for name in params["names"]}
    used = [call.name for call in trajectory.tool_calls if call.name in allowed]
    if used:
        return AssertionResult("tool_any_of", True, f"used {used[0]}")
    seen = ", ".join(call.name for call in trajectory.tool_calls) or "no tools"
    return AssertionResult("tool_any_of", False, f"wanted one of {sorted(allowed)}; saw {seen}")


def _command_forbidden(trajectory: Trajectory, params: dict) -> AssertionResult:
    pattern = re.compile(str(params["pattern"]))
    for call in trajectory.tool_calls:
        command = call.command or ""
        if command and pattern.search(command):
            return AssertionResult("command_forbidden", False, f"{call.raw_name} ran {command}")
    return AssertionResult("command_forbidden", True, "forbidden command pattern did not match")


def _reply_language(trajectory: Trajectory, params: dict) -> AssertionResult:
    expected = str(params["language"])
    detected = detect_language(trajectory.final_reply)
    if detected == expected:
        return AssertionResult("reply_language", True, f"reply looks like {detected}")
    return AssertionResult(
        "reply_language",
        False,
        f"wanted {expected}, detected {detected}",
    )


def detect_language(text: str) -> str:
    """Coarse reply-language check.

    A reply is Chinese when it contains a real run of CJK characters, even if
    it also names Latin identifiers such as ``src/calc.py``. English fixtures
    contain no CJK, so the threshold does not flip them.
    """

    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    if cjk >= 8:
        return "zh"
    if cjk >= 4 and cjk > latin:
        return "zh"
    return "en"


def _permission_max(trajectory: Trajectory, params: dict) -> AssertionResult:
    limit = int(params["max"])
    count = trajectory.permission_prompts
    if count <= limit:
        return AssertionResult("permission_prompts_max", True, f"{count} permission prompts")
    return AssertionResult("permission_prompts_max", False, f"{count} permission prompts, max is {limit}")


def _approval_policy(trajectory: Trajectory, params: dict) -> AssertionResult:
    expected = str(params["equals"])
    actual = trajectory.approval_policy or ""
    if actual == expected:
        return AssertionResult("approval_policy", True, f"approval_policy={actual}")
    shown = actual or "missing"
    return AssertionResult("approval_policy", False, f"approval_policy={shown}, wanted {expected}")


def _diff_contains(trajectory: Trajectory, params: dict, *, must: bool) -> AssertionResult:
    needle = str(params["text"])
    present = needle in trajectory.diff
    kind = "diff_contains" if must else "diff_excludes"
    if present == must:
        return AssertionResult(kind, True, "diff check passed")
    if must:
        return AssertionResult(kind, False, f"diff is missing {needle!r}")
    return AssertionResult(kind, False, f"diff contains forbidden {needle!r}")


def _diff_paths_only(trajectory: Trajectory, params: dict) -> AssertionResult:
    allowed = [str(path) for path in params["paths"]]
    touched = diff_paths(trajectory.diff)
    extra = [path for path in touched if not any(_path_matches(path, item) for item in allowed)]
    if not extra and touched:
        return AssertionResult("diff_paths_only", True, f"touched {', '.join(touched)}")
    if not touched:
        return AssertionResult("diff_paths_only", False, "diff is empty")
    return AssertionResult("diff_paths_only", False, f"unexpected paths: {', '.join(extra)}")


def _cache_hit_min(trajectory: Trajectory, params: dict) -> AssertionResult:
    minimum = float(params["min"])
    rate = trajectory.usage.cache_hit_rate
    if rate + 1e-12 >= minimum:
        return AssertionResult("cache_hit_min", True, f"cache hit rate {rate:.3f}")
    return AssertionResult("cache_hit_min", False, f"cache hit rate {rate:.3f} < {minimum:.3f}")


def _input_tokens_max(trajectory: Trajectory, params: dict) -> AssertionResult:
    limit = int(params["max"])
    actual = trajectory.usage.input_tokens
    if actual <= limit:
        return AssertionResult("input_tokens_max", True, f"input tokens {actual}")
    return AssertionResult("input_tokens_max", False, f"input tokens {actual} > {limit}")


def diff_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[-1]
        if path.startswith("b/"):
            path = path[2:]
        paths.append(path)
    return paths


def _path_matches(actual: str, expected: str) -> bool:
    actual_norm = actual.replace("\\", "/").lstrip("./")
    expected_norm = expected.replace("\\", "/").lstrip("./")
    return actual_norm == expected_norm or actual_norm.endswith("/" + expected_norm)


def metric_value(trajectory: Trajectory, name: str) -> float:
    usage = trajectory.usage
    if name == "input_tokens":
        return float(usage.input_tokens)
    if name == "output_tokens":
        return float(usage.output_tokens)
    if name == "cache_creation_tokens":
        return float(usage.cache_creation_tokens)
    if name == "cache_hit_rate":
        return float(usage.cache_hit_rate)
    if name == "permission_prompts":
        return float(trajectory.permission_prompts)
    raise ValueError(f"unknown metric {name}")
