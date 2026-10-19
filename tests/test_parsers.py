# Copyright 2026 HarnessWatch contributors
# SPDX-License-Identifier: Apache-2.0

import json

from harnesswatch.parsers import parse_jsonl


def test_claude_session_dedupes_streamed_tool_use_and_prefers_result_usage():
    usage = {
        "input_tokens": 8000,
        "output_tokens": 100,
        "cache_read_input_tokens": 24000,
        "cache_creation_input_tokens": 200,
    }
    lines = [
        {
            "type": "assistant",
            "sessionId": "s1",
            "version": "2.1.70",
            "message": {
                "id": "msg_1",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "AGENTS.md"}}],
                "usage": usage,
            },
        },
        {
            "type": "assistant",
            "sessionId": "s1",
            "message": {
                "id": "msg_1",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "AGENTS.md"}}],
                "usage": usage,
            },
        },
        {
            "type": "assistant",
            "sessionId": "s1",
            "message": {
                "id": "msg_1",
                "content": [
                    {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "sed -i 's/a/b/' src/calc.py"}},
                    {"type": "text", "text": "done"},
                ],
            },
        },
        {"type": "system", "subtype": "permission_request", "sessionId": "s1", "tool_name": "Bash"},
        {"type": "result", "session_id": "s1", "result": "done", "usage": usage},
        "not json",
    ]
    text = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
    trajectory = parse_jsonl(text, harness="claude-code")
    assert trajectory.session_id == "s1"
    assert [call.name for call in trajectory.tool_calls] == ["Read", "Bash"]
    assert trajectory.files_read == ["AGENTS.md"]
    assert trajectory.permission_prompts == 1
    assert trajectory.usage.input_tokens == 8000
    assert trajectory.usage.cache_hit_rate > 0.7
    assert trajectory.final_reply == "done"
    assert trajectory.parse_warnings


def test_codex_rollout_and_exec_stream():
    rollout = "\n".join(
        [
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "thread-1", "cli_version": "0.63.0-fixture", "cwd": "/work"},
                }
            ),
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:00Z",
                    "type": "turn_context",
                    "payload": {"approval_policy": "untrusted"},
                }
            ),
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "c1",
                        "arguments": json.dumps({"path": "pkg/AGENTS.md"}),
                    },
                }
            ),
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "call_id": "c2",
                        "input": "*** Begin Patch\n*** Update File: src/calc.py\n*** End Patch",
                    },
                }
            ),
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已在 src/calc.py 加入 multiply 函数，没有添加冗长注释。"}],
                    },
                }
            ),
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:04Z",
                    "type": "event_msg",
                    "payload": {"type": "exec_approval_request", "call_id": "a1"},
                }
            ),
            json.dumps(
                {
                    "timestamp": "2026-10-19T00:00:05Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 8000,
                                "cached_input_tokens": 24000,
                                "output_tokens": 120,
                            }
                        },
                    },
                }
            ),
        ]
    )
    trajectory = parse_jsonl(rollout, harness="codex")
    assert trajectory.session_id == "thread-1"
    assert trajectory.version == "0.63.0-fixture"
    assert trajectory.approval_policy == "untrusted"
    assert [call.name for call in trajectory.tool_calls] == ["Read", "ApplyPatch"]
    assert trajectory.files_read == ["pkg/AGENTS.md"]
    assert trajectory.permission_prompts == 1
    assert trajectory.usage.cache_read_tokens == 24000

    exec_stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "exec-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "command_execution", "command": "cat AGENTS.md", "status": "completed"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_2", "type": "agent_message", "text": "Added multiply to src/calc.py."},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10},
                }
            ),
        ]
    )
    streamed = parse_jsonl(exec_stream, harness="codex")
    assert streamed.session_id == "exec-1"
    assert streamed.tool_calls[0].name == "Bash"
    assert "AGENTS.md" in streamed.files_read
    assert streamed.usage.input_tokens == 100
    assert "Added multiply" in streamed.final_reply
