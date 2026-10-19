# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Normalized trajectory produced by every session parser."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """One tool invocation, with the harness-specific name kept beside a common name."""

    name: str
    raw_name: str
    arguments: dict
    command: str | None = None
    paths: list[str] = field(default_factory=list)
    call_id: str | None = None


@dataclass
class Usage:
    """Token accounting. Cache fields follow each vendor's own counters."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from cache.

        Claude reports uncached input, cache reads, and cache writes as three
        counters. Codex reports input_tokens plus cached_input_tokens. The
        denominator is the sum of those prompt-side counters so a rate near 1
        means almost every prompt token was cached.
        """

        denom = self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens
        if denom <= 0:
            return 0.0
        return self.cache_read_tokens / denom


@dataclass
class Trajectory:
    """What a single isolated run did, independent of which CLI produced it."""

    harness: str
    version: str
    session_id: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_texts: list[str] = field(default_factory=list)
    permission_prompts: int = 0
    approval_policy: str | None = None
    usage: Usage = field(default_factory=Usage)
    diff: str = ""
    files_read: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def final_reply(self) -> str:
        parts = [text.strip() for text in self.assistant_texts if text and text.strip()]
        return "\n".join(parts)
