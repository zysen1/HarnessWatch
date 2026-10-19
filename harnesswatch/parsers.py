# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0
"""Parsers for Claude Code session JSONL and Codex rollout / exec JSONL.

Claude Code writes one JSON object per line under
``$CLAUDE_CONFIG_DIR/projects/<project>/<session-id>.jsonl``. ``claude -p
--output-format stream-json`` emits the same family of records on stdout,
plus a final ``result`` record.

Codex writes rollout files under ``$CODEX_HOME/sessions/**/rollout-*.jsonl``
as ``{timestamp, type, payload}`` lines. ``codex exec --json`` emits a
flatter event stream (``thread.started``, ``item.*``, ``turn.completed``).
Both shapes normalize to the same Trajectory.
"""

from __future__ import annotations

import json
import re
from typing import Any

from harnesswatch.trajectory import ToolCall, Trajectory, Usage

_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")
_READ_SHELL = re.compile(r"(?:^|[;&|]\s*)(?:cat|head|less|more|nl|bat)\b")
_PATH_TOKEN = re.compile(r"(?:^|[\s'\"=])((?:[\w.-]+/)*[\w.-]+\.(?:md|py|txt|json|toml|yml|yaml))")


def parse_jsonl(text: str, *, harness: str, version: str = "") -> Trajectory:
    """Parse a session transcript. ``harness`` is ``claude-code`` or ``codex``."""

    if harness == "claude-code":
        return _parse_claude(text, version)
    if harness == "codex":
        return _parse_codex(text, version)
    raise ValueError(f"unsupported harness: {harness}")


def _loads_lines(text: str, trajectory: Trajectory) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            trajectory.parse_warnings.append(f"line {lineno}: {exc.msg}")
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            trajectory.parse_warnings.append(f"line {lineno}: expected an object")
    return rows


def _parse_claude(text: str, version: str) -> Trajectory:
    trajectory = Trajectory(harness="claude-code", version=version, session_id="")
    rows = _loads_lines(text, trajectory)
    seen_calls: set[str] = set()
    message_usage: dict[str, Usage] = {}
    result_usage: Usage | None = None
    for row in rows:
        kind = str(row.get("type") or "")
        session_id = str(row.get("sessionId") or row.get("session_id") or "")
        if session_id and not trajectory.session_id:
            trajectory.session_id = session_id
        if not trajectory.version:
            trajectory.version = str(row.get("version") or "")
        if kind == "assistant":
            _consume_claude_assistant(row, trajectory, seen_calls, message_usage)
        elif kind == "user":
            _consume_claude_user_tools(row, trajectory, seen_calls)
        elif kind == "system":
            _consume_claude_permission(row, trajectory)
        elif kind in {"permission_request", "permission_prompt"}:
            trajectory.permission_prompts += 1
        elif kind == "result":
            result_usage = _usage_from_mapping(row.get("usage"))
            reply = row.get("result")
            if isinstance(reply, str) and reply.strip() and not trajectory.assistant_texts:
                trajectory.assistant_texts.append(reply)
    if result_usage is not None:
        trajectory.usage = result_usage
    elif message_usage:
        trajectory.usage = _sum_usage(message_usage.values())
    _finish_files_read(trajectory)
    return trajectory


def _consume_claude_assistant(
    row: dict[str, Any],
    trajectory: Trajectory,
    seen_calls: set[str],
    message_usage: dict[str, Usage],
) -> None:
    message = row.get("message")
    if not isinstance(message, dict):
        return
    message_id = str(message.get("id") or row.get("uuid") or f"anon-{len(message_usage)}")
    usage = _usage_from_mapping(message.get("usage"))
    if usage is not None:
        # Streaming writes one line per content block. Usage on the last
        # chunk for a message id is the message total, so last-write wins.
        message_usage[message_id] = usage
    content = message.get("content")
    if isinstance(content, str):
        if content.strip():
            trajectory.assistant_texts.append(content)
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "")
            if text.strip():
                trajectory.assistant_texts.append(text)
        elif block_type == "tool_use":
            call = _claude_tool_call(block)
            if call.call_id and call.call_id in seen_calls:
                continue
            if call.call_id:
                seen_calls.add(call.call_id)
            trajectory.tool_calls.append(call)


def _consume_claude_user_tools(
    row: dict[str, Any],
    trajectory: Trajectory,
    seen_calls: set[str],
) -> None:
    """Some transcripts place tool_use blocks on the following user turn."""

    message = row.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            call = _claude_tool_call(block)
            if call.call_id and call.call_id in seen_calls:
                continue
            if call.call_id:
                seen_calls.add(call.call_id)
            trajectory.tool_calls.append(call)


def _claude_tool_call(block: dict[str, Any]) -> ToolCall:
    raw_name = str(block.get("name") or "unknown")
    arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
    command = arguments.get("command") if isinstance(arguments.get("command"), str) else None
    paths = _paths_from_arguments(arguments)
    if command and raw_name in {"Bash", "bash", "Shell"}:
        paths.extend(_paths_from_shell(command))
    return ToolCall(
        name=_normalize_claude_tool(raw_name),
        raw_name=raw_name,
        arguments=arguments,
        command=command,
        paths=_unique(paths),
        call_id=str(block.get("id") or "") or None,
    )


def _consume_claude_permission(row: dict[str, Any], trajectory: Trajectory) -> None:
    subtype = str(row.get("subtype") or "")
    if subtype in {"permission_request", "permission_prompt"} or row.get("permission_request"):
        trajectory.permission_prompts += 1


def _parse_codex(text: str, version: str) -> Trajectory:
    trajectory = Trajectory(harness="codex", version=version, session_id="")
    rows = _loads_lines(text, trajectory)
    if rows and all(_is_rollout_line(row) for row in rows if row.get("type") != "turn.completed"):
        # Mixed files are rare. Prefer the rollout parser when the envelope is present.
        if any(row.get("type") in {"session_meta", "response_item", "event_msg", "turn_context"} for row in rows):
            _parse_codex_rollout(rows, trajectory)
            _finish_files_read(trajectory)
            return trajectory
    _parse_codex_exec(rows, trajectory)
    _finish_files_read(trajectory)
    return trajectory


def _is_rollout_line(row: dict[str, Any]) -> bool:
    return "payload" in row and row.get("type") in {
        "session_meta",
        "response_item",
        "event_msg",
        "turn_context",
        "compacted",
        "token_usage_record",
    }


def _parse_codex_rollout(rows: list[dict[str, Any]], trajectory: Trajectory) -> None:
    latest_total: Usage | None = None
    for row in rows:
        kind = row.get("type")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if kind == "session_meta":
            trajectory.session_id = str(payload.get("id") or trajectory.session_id)
            cli_version = str(payload.get("cli_version") or "")
            if cli_version and not trajectory.version:
                trajectory.version = cli_version
        elif kind == "turn_context":
            policy = payload.get("approval_policy")
            if isinstance(policy, str) and policy:
                trajectory.approval_policy = policy
        elif kind == "response_item":
            _consume_codex_item(payload, trajectory)
        elif kind == "event_msg":
            latest = _consume_codex_event(payload, trajectory)
            if latest is not None:
                latest_total = latest
        elif kind == "token_usage_record":
            usage = _codex_usage(payload.get("usage") or payload.get("thread_token_usage"))
            if usage is not None:
                latest_total = usage
    if latest_total is not None:
        trajectory.usage = latest_total


def _parse_codex_exec(rows: list[dict[str, Any]], trajectory: Trajectory) -> None:
    """``codex exec --json`` event stream."""

    totals: Usage | None = None
    for row in rows:
        kind = str(row.get("type") or "")
        if kind == "thread.started":
            trajectory.session_id = str(row.get("thread_id") or trajectory.session_id)
        elif kind in {"item.completed", "item.started"} and kind == "item.completed":
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            _consume_codex_exec_item(item, trajectory)
        elif kind == "turn.completed":
            usage = _codex_usage(row.get("usage"))
            if usage is not None:
                totals = _add_usage(totals, usage)
        elif kind == "event_msg":
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
            latest = _consume_codex_event(payload, trajectory)
            if latest is not None:
                totals = latest
    if totals is not None:
        trajectory.usage = totals


def _consume_codex_exec_item(item: dict[str, Any], trajectory: Trajectory) -> None:
    item_type = str(item.get("type") or "")
    if item_type == "agent_message":
        text = str(item.get("text") or "")
        if text.strip():
            trajectory.assistant_texts.append(text)
    elif item_type in {"command_execution", "function_call", "mcp_tool_call", "file_change"}:
        _consume_codex_item(
            {
                "type": "local_shell_call" if item_type == "command_execution" else item_type,
                "name": item.get("name") or item_type,
                "call_id": item.get("id"),
                "command": item.get("command"),
                "arguments": item.get("arguments") or item.get("input"),
                "action": {"type": "exec", "command": _command_list(item.get("command"))},
            },
            trajectory,
        )
    elif item_type == "approval_request":
        trajectory.permission_prompts += 1


def _command_list(command: Any) -> list[str]:
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str):
        return [command]
    return []


def _consume_codex_item(payload: dict[str, Any], trajectory: Trajectory) -> None:
    item_type = str(payload.get("type") or "")
    if item_type == "message":
        if payload.get("role") == "assistant":
            text = _codex_message_text(payload.get("content"))
            if text.strip():
                trajectory.assistant_texts.append(text)
        return
    if item_type == "function_call":
        arguments = _parse_arguments(payload.get("arguments"))
        raw_name = str(payload.get("name") or "function_call")
        command = arguments.get("command") if isinstance(arguments.get("command"), str) else None
        paths = _paths_from_arguments(arguments)
        if command:
            paths.extend(_paths_from_shell(command))
        trajectory.tool_calls.append(
            ToolCall(
                name=_normalize_codex_tool(raw_name),
                raw_name=raw_name,
                arguments=arguments,
                command=command,
                paths=_unique(paths),
                call_id=str(payload.get("call_id") or "") or None,
            )
        )
        return
    if item_type == "custom_tool_call":
        raw_name = str(payload.get("name") or "custom_tool")
        raw_input = payload.get("input")
        arguments = _parse_arguments(raw_input)
        command = raw_input if isinstance(raw_input, str) else None
        paths = _paths_from_arguments(arguments)
        paths.extend(_paths_from_patch(command or ""))
        trajectory.tool_calls.append(
            ToolCall(
                name=_normalize_codex_tool(raw_name),
                raw_name=raw_name,
                arguments=arguments,
                command=command,
                paths=_unique(paths),
                call_id=str(payload.get("call_id") or "") or None,
            )
        )
        return
    if item_type == "local_shell_call":
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        command_parts = action.get("command")
        command = " ".join(command_parts) if isinstance(command_parts, list) else str(command_parts or "")
        paths = _paths_from_shell(command)
        trajectory.tool_calls.append(
            ToolCall(
                name="Bash",
                raw_name="local_shell_call",
                arguments={"command": command},
                command=command or None,
                paths=_unique(paths),
                call_id=str(payload.get("call_id") or "") or None,
            )
        )


def _consume_codex_event(payload: dict[str, Any], trajectory: Trajectory) -> Usage | None:
    event_type = str(payload.get("type") or "")
    if "approval_request" in event_type or event_type in {"approval_request"}:
        trajectory.permission_prompts += 1
        return None
    if event_type == "token_count":
        info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
        total = info.get("total_token_usage") if isinstance(info, dict) else None
        return _codex_usage(total)
    return None


def _codex_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts)


def _parse_arguments(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return parsed if isinstance(parsed, dict) else {"raw": value}
    if isinstance(value, str):
        return {"raw": value}
    return {}


def _normalize_claude_tool(name: str) -> str:
    mapping = {
        "read": "Read",
        "edit": "Edit",
        "write": "Write",
        "bash": "Bash",
        "shell": "Bash",
        "glob": "Glob",
        "grep": "Grep",
        "notebookedit": "Edit",
    }
    return mapping.get(name.lower(), name)


def _normalize_codex_tool(name: str) -> str:
    mapping = {
        "read_file": "Read",
        "read": "Read",
        "apply_patch": "ApplyPatch",
        "shell": "Bash",
        "shell_command": "Bash",
        "local_shell": "Bash",
        "exec_command": "Bash",
        "write_file": "Write",
        "edit_file": "Edit",
        "command_execution": "Bash",
    }
    return mapping.get(name.lower(), name)


def _paths_from_arguments(arguments: dict) -> list[str]:
    paths: list[str] = []
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    files = arguments.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, str):
                paths.append(item)
    return paths


def _paths_from_shell(command: str) -> list[str]:
    if not command or not _READ_SHELL.search(command):
        return []
    return _PATH_TOKEN.findall(command)


def _paths_from_patch(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("*** Update File:") or line.startswith("*** Add File:"):
            paths.append(line.split(":", 1)[1].strip())
    return paths


def _finish_files_read(trajectory: Trajectory) -> None:
    files: list[str] = []
    for call in trajectory.tool_calls:
        if call.name == "Read":
            files.extend(call.paths)
        elif call.name == "Bash" and call.command and _READ_SHELL.search(call.command):
            files.extend(call.paths)
    trajectory.files_read = _unique(files)


def _usage_from_mapping(value: Any) -> Usage | None:
    if not isinstance(value, dict):
        return None
    if not any(key in value for key in ("input_tokens", "output_tokens", "cache_read_input_tokens")):
        return None
    return Usage(
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=int(value.get("output_tokens") or 0),
        cache_read_tokens=int(value.get("cache_read_input_tokens") or value.get("cached_input_tokens") or 0),
        cache_creation_tokens=int(value.get("cache_creation_input_tokens") or value.get("cache_creation_tokens") or 0),
        reasoning_tokens=int(value.get("reasoning_tokens") or value.get("reasoning_output_tokens") or 0),
    )


def _codex_usage(value: Any) -> Usage | None:
    if not isinstance(value, dict):
        return None
    if not any(key in value for key in ("input_tokens", "cached_input_tokens", "output_tokens")):
        return None
    return Usage(
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=int(value.get("output_tokens") or 0),
        cache_read_tokens=int(value.get("cached_input_tokens") or value.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(value.get("cache_creation_input_tokens") or 0),
        reasoning_tokens=int(value.get("reasoning_output_tokens") or 0),
    )


def _sum_usage(usages) -> Usage:
    total = Usage()
    for usage in usages:
        total = _add_usage(total, usage)
    return total


def _add_usage(left: Usage | None, right: Usage) -> Usage:
    base = left or Usage()
    return Usage(
        input_tokens=base.input_tokens + right.input_tokens,
        output_tokens=base.output_tokens + right.output_tokens,
        cache_read_tokens=base.cache_read_tokens + right.cache_read_tokens,
        cache_creation_tokens=base.cache_creation_tokens + right.cache_creation_tokens,
        reasoning_tokens=base.reasoning_tokens + right.reasoning_tokens,
    )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
