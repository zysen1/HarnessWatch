# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""YAML scenario DSL.

A scenario is a tiny repository, the instruction files planted in it, a
prompt, and trajectory-level assertions. One file can target Claude Code,
Codex, or both. Assertions may be limited to a harness when the instruction
filename differs (CLAUDE.md versus AGENTS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SeedFile:
    path: str
    content: str


@dataclass(frozen=True)
class AssertionSpec:
    kind: str
    params: dict
    harnesses: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricWatch:
    name: str
    direction: str


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    category: str
    summary: str
    harnesses: tuple[str, ...]
    prompt: str
    seed_files: tuple[SeedFile, ...]
    instructions: dict[str, tuple[SeedFile, ...]]
    assertions: tuple[AssertionSpec, ...]
    watch_metrics: tuple[MetricWatch, ...] = ()
    references: tuple[str, ...] = ()

    def instruction_files(self, harness: str) -> tuple[SeedFile, ...]:
        if harness in self.instructions:
            return self.instructions[harness]
        return self.instructions.get("default", ())

    def assertions_for(self, harness: str) -> tuple[AssertionSpec, ...]:
        selected = []
        for assertion in self.assertions:
            if not assertion.harnesses or harness in assertion.harnesses:
                selected.append(assertion)
        return tuple(selected)


def load_scenario(path: Path) -> Scenario:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a mapping")
    return scenario_from_mapping(data, source=str(path))


def load_scenarios(directory: Path) -> list[Scenario]:
    paths = sorted(directory.glob("*.yaml"))
    scenarios = [load_scenario(path) for path in paths]
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate scenario ids in {directory}")
    return scenarios


def scenario_from_mapping(data: dict, *, source: str = "<memory>") -> Scenario:
    required = ("id", "title", "category", "summary", "harnesses", "prompt", "assertions")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"{source} is missing {', '.join(missing)}")
    harnesses = tuple(str(item) for item in data["harnesses"])
    for harness in harnesses:
        if harness not in {"claude-code", "codex"}:
            raise ValueError(f"{source} has unsupported harness {harness}")
    return Scenario(
        id=str(data["id"]),
        title=str(data["title"]),
        category=str(data["category"]),
        summary=str(data["summary"]).strip(),
        harnesses=harnesses,
        prompt=str(data["prompt"]).strip(),
        seed_files=tuple(_seed(item) for item in data.get("seed") or []),
        instructions={
            str(harness): tuple(_seed(item) for item in files)
            for harness, files in (data.get("instructions") or {}).items()
        },
        assertions=tuple(_assertion(item) for item in data["assertions"]),
        watch_metrics=tuple(_metric(item) for item in data.get("watch_metrics") or []),
        references=tuple(str(item) for item in data.get("references") or []),
    )


def _seed(item: dict) -> SeedFile:
    return SeedFile(path=str(item["path"]), content=str(item.get("content", "")))


def _assertion(item: dict) -> AssertionSpec:
    if "kind" not in item:
        raise ValueError("assertion is missing kind")
    harnesses = tuple(str(harness) for harness in item.get("harnesses") or [])
    params = {key: value for key, value in item.items() if key not in {"kind", "harnesses"}}
    return AssertionSpec(kind=str(item["kind"]), params=params, harnesses=harnesses)


def _metric(item: dict) -> MetricWatch:
    direction = str(item["direction"])
    if direction not in {"higher_is_better", "lower_is_better"}:
        raise ValueError(f"bad metric direction: {direction}")
    return MetricWatch(name=str(item["name"]), direction=direction)
