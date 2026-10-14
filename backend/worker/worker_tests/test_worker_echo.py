"""The live echo: an agent stage's output on the worker's stderr, as readable `[stage] ...` lines.

Echo turns Claude stream-json and Copilot JSONL events into text, reasoning, tool calls and
results, and prints anything else as plain text, cut short. Whole runs check that it is printed
during the stage (HTTP and mailbox mode), that log events still carry every raw line, that shell
stages aren't echoed and that AGENTOS_ECHO=0 turns it off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import worker
from worker_tests.worker_support import (
    FakeServer,
    agent_stage,
    make_manifest,
    python_shell,
    run_worker,
    shell_stage,
)

CLAUDE: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-haiku-5-5"},
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Look at the tests first."},
                {"type": "text", "text": "I'll run the tests.\n\nThen fix them."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pytest -q", "description": "Run the tests"},
                },
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "3 passed\nok"}],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": [{"type": "text", "text": "No such file"}],
                    "is_error": True,
                }
            ],
        },
    },
    {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
    {"type": "system", "subtype": "compact_boundary"},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 3,
        "total_cost_usd": 0.0123,
        "result": "Done.",
    },
]
CLAUDE_ECHO = [
    "session started (claude-haiku-5-5)",
    "thinking: Look at the tests first.",
    "I'll run the tests.",
    "Then fix them.",
    "tool Bash: pytest -q",
    "result: 3 passed (+1 line)",
    "tool error: No such file",
    "done: success, 3 turns, $0.0123",
]

# The shape of Copilot CLI 1.0's events: {"type": "<area>.<event>", "data": {...}, "id", ...}.
COPILOT: list[dict[str, Any]] = [
    {"type": "session.start", "data": {"sessionId": "x", "selectedModel": "gpt-5"}, "id": "1"},
    {"type": "user.message", "data": {"content": "Fix the login bug"}},
    {"type": "assistant.turn_start", "data": {"turnId": "0"}},
    {"type": "assistant.message_delta", "data": {"deltaContent": "I"}, "ephemeral": True},
    {"type": "assistant.reasoning", "data": {"reasoningId": "r", "content": "Glob first."}},
    {
        "type": "assistant.message",
        "data": {
            "messageId": "m1",
            "content": "",
            "reasoningText": "Glob first.",  # already shown: not again
            "toolRequests": [{"toolCallId": "c1", "name": "glob", "arguments": {}}],
        },
    },
    {
        "type": "tool.execution_start",
        "data": {"toolCallId": "c1", "toolName": "glob", "arguments": {"pattern": "**/*.py"}},
    },
    {
        "type": "tool.execution_complete",
        "data": {"toolCallId": "c1", "success": True, "result": {"content": "a.py\nb.py\nc.py"}},
    },
    {
        "type": "tool.execution_start",
        "data": {"toolCallId": "c2", "toolName": "bash", "arguments": {"command": "rm x"}},
    },
    {
        "type": "tool.execution_complete",
        "data": {"toolCallId": "c2", "success": False, "error": {"message": "permission denied"}},
    },
    {"type": "assistant.message", "data": {"messageId": "m2", "content": "All done."}},
    {"type": "assistant.turn_end", "data": {"turnId": "0"}},
    {"type": "session.shutdown", "data": {"totalPremiumRequests": 1}},
]
COPILOT_ECHO = [
    "session started (gpt-5)",
    "thinking: Glob first.",
    "tool glob: **/*.py",
    "result: a.py (+2 lines)",
    "tool bash: rm x",
    "tool error: permission denied",
    "All done.",
    "done: 1 premium request",
]


def echo_all(events: list[dict[str, Any]]) -> list[str]:
    echo = worker.Echo("s")
    return [line for e in events for line in echo.lines(json.dumps(e))]


def assistant(*blocks: dict[str, Any]) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def test_claude_stream_json() -> None:
    assert echo_all(CLAUDE) == CLAUDE_ECHO


def test_copilot_jsonl() -> None:
    assert echo_all(COPILOT) == COPILOT_ECHO


LONG = "x" * 500


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("hello world", ["hello world"]),
        ("   ", []),
        (LONG, ["x" * 197 + "..."]),
        ("{not json", ["{not json"]),
        ("[1, 2]", ["[1, 2]"]),
        ('{"type": 3}', ['{"type": 3}']),
        ('{"no": "type"}', ['{"no": "type"}']),
        ('{"type": "hook.start", "data": {}}', ['{"type": "hook.start", "data": {}}']),
        ('{"type": "subagent.progress", "ephemeral": true}', []),
        ('{"type": "assistant.usage", "data": {"outputTokens": 9}}', []),
        ('{"type": "system", "subtype": "init"}', ["session started"]),
        (
            '{"type": "result", "subtype": "error_max_turns", "is_error": true, "num_turns": 1}',
            ["done: error_max_turns, 1 turn"],
        ),
        (
            '{"type": "result", "subtype": "success", "is_error": true, "result": "API 401\\nx"}',
            ["done: error: API 401 (+1 line)"],
        ),
        ('{"type": "result"}', ["done: success"]),
        (
            assistant({"type": "tool_use", "name": "TodoWrite", "input": {"todos": [1, 2]}}),
            ["tool TodoWrite: 2 todos"],
        ),
        (assistant({"type": "tool_use", "name": "Mystery"}), ["tool Mystery"]),
        (assistant({"type": "mcp_tool_use", "input": {"path": "a"}}), ["tool ?: a"]),
        (
            assistant({"type": "tool_use", "name": "Bash", "input": {"command": "a\nb\nc"}}),
            ["tool Bash: a (+2 lines)"],
        ),
        (assistant({"type": "redacted_thinking"}, {"type": "text", "text": " "}), []),
        (
            '{"type": "user", "message": {"content": [{"type": "tool_result", "content": ""}]}}',
            ["result"],
        ),
        ('{"type": "user", "message": {"content": "a replayed prompt"}}', []),
        ('{"type": "session.error", "data": {"message": "boom"}}', ["error: boom"]),
        ('{"type": "abort", "data": {"reason": "user cancelled"}}', ["aborted: user cancelled"]),
        ('{"type": "assistant.intent", "data": {"intent": "Exploring"}}', ["intent: Exploring"]),
        ('{"type": "session.shutdown", "data": {}}', ["done"]),
        (
            '{"type": "tool.execution_complete", "data": {"success": false, "error": "nope"}}',
            ["tool error: nope"],
        ),
        ('{"type": "session.start", "data": "odd"}', ["session started"]),
    ],
)
def test_one_line(line: str, expected: list[str]) -> None:
    assert worker.Echo("s").lines(line) == expected


def test_a_cut_line_is_plain_text() -> None:
    """A line the reader cut is no valid JSON: it prints as text, never parsed."""
    line = '{"type": "result", "subtype": "success"}'
    assert worker.Echo("s").lines(line, truncated=True) == [line]


def test_agent_text_is_cut_to_a_block() -> None:
    text = "\n".join(f"line {i} " + "y" * 90 for i in range(100))
    out = worker.Echo("s").lines(assistant({"type": "text", "text": text}))
    assert out[0].startswith("line 0 ")
    assert out[-1].endswith("...")
    assert sum(len(s) for s in out) <= worker.ECHO_TEXT_CHARS


def test_reasoning_is_labelled_and_indented() -> None:
    out = worker.Echo("s").lines(assistant({"type": "thinking", "thinking": "One.\n\nTwo."}))
    assert out == ["thinking: One.", "  Two."]


def test_every_printed_line_carries_the_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    """Control characters can't start an unprefixed console line (a `::` workflow command)."""
    text = (
        "Fine.\r::add-mask::x\x1b[31m red\x1b[0m\x00"
        "\N{LINE SEPARATOR}::error::y\x85::warning::z\tend"
    )
    echo = worker.Echo("review")
    echo("stdout", assistant({"type": "text", "text": text}), False)
    echo("stderr", "plain\r::stop-commands::t", False)
    err = capsys.readouterr().err
    assert err.splitlines() == [
        "[review] Fine.",
        "[review] ::add-mask::x red",
        "[review] ::error::y",
        "[review] ::warning::z end",
        "[review] plain (+1 line)",
    ]
    assert "\x1b" not in err
    assert "\r" not in err


def test_a_formatting_bug_stops_only_the_echo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(self: worker.Echo, text: str, truncated: bool = False) -> list[str]:
        raise RuntimeError(text)

    monkeypatch.setattr(worker.Echo, "lines", broken)
    echo = worker.Echo("s")
    echo("stdout", "secret one", False)
    echo("stdout", "two", False)
    assert capsys.readouterr().err == "[s] (echo stopped: RuntimeError)\n"


# ---------------------------------------------------------------- whole runs


def jsonl(events: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def worker_lines(err: str, stage_id: str) -> list[str]:
    prefix = f"[{stage_id}] "
    return [line.removeprefix(prefix) for line in err.splitlines() if line.startswith(prefix)]


def stage_logs(events: list[dict[str, Any]], stage_id: str) -> list[str]:
    return [e["data"]["text"] for e in events if e["type"] == "log" and e["stage_id"] == stage_id]


@pytest.mark.parametrize(
    ("stream", "expected"),
    [(CLAUDE, CLAUDE_ECHO), (COPILOT, COPILOT_ECHO)],
    ids=["claude", "copilot"],
)
def test_a_run_echoes_agent_output_during_the_stage(
    server: FakeServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stream: list[dict[str, Any]],
    expected: list[str],
) -> None:
    python_shell(monkeypatch)
    prompt = jsonl(stream) + "plain words\n"
    server.manifest = make_manifest(
        [agent_stage("implement", "stdin", stdin=prompt), shell_stage("lint", "print('lint ok')")]
    )
    assert run_worker(server, tmp_path / "ws") == 0

    err = capsys.readouterr().err
    assert worker_lines(err, "implement") == [*expected, "plain words"]
    assert worker_lines(err, "lint") == [], "shell stages aren't echoed"
    lines = err.splitlines()
    started = next(i for i, s in enumerate(lines) if "stage implement (agent) started" in s)
    finished = next(i for i, s in enumerate(lines) if "stage implement succeeded" in s)
    echoed = [i for i, s in enumerate(lines) if s.startswith("[implement] ")]
    assert started < echoed[0], "printed while the stage runs"
    assert echoed[-1] < finished

    events = server.events()
    assert stage_logs(events, "implement") == prompt.splitlines(), "every raw line still logged"
    assert stage_logs(events, "lint") == ["lint ok"]


def test_agentos_echo_0_turns_it_off(
    server: FakeServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTOS_ECHO", "0")
    prompt = jsonl(CLAUDE)
    server.manifest = make_manifest([agent_stage("implement", "stdin", stdin=prompt)])
    assert run_worker(server, tmp_path / "ws") == 0

    err = capsys.readouterr().err
    assert worker_lines(err, "implement") == []
    assert "stage implement succeeded" in err, "the worker's own progress is unchanged"
    assert stage_logs(server.events(), "implement") == prompt.splitlines()


def test_mailbox_mode_echoes_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = make_manifest([agent_stage("implement", "stdin", stdin=jsonl(COPILOT))])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    args = ["--manifest", str(manifest_path), "--outbox", str(tmp_path / "outbox.jsonl")]
    assert worker.main([*args, "--workdir", str(tmp_path / "ws")]) == 0
    assert worker_lines(capsys.readouterr().err, "implement") == COPILOT_ECHO
