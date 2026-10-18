"""The live echo: every agent call's output is also printed on the worker's stderr as it
arrives (the Actions job console, the client's run log) in a short readable form; the log events
still carry each line as it was. AGENTOS_ECHO=0 in the worker's own environment turns it off.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from typing import Any

from agentos_worker.console import STDERR_LOCK
from agentos_worker.text import clip

ECHO_LINE_CHARS = 200  # a tool call, a tool result, a line nothing recognises
ECHO_TEXT_CHARS = 2000  # one block of agent text or reasoning
ECHO_DETAIL_KEYS = (  # the tool argument shown with a tool call, most telling first
    "file_path",
    "path",
    "notebook_path",
    "command",
    "pattern",
    "url",
    "query",
    "skill",
    "intent",
    "description",
    "prompt",
)
# Event types with nothing worth a console line: Claude stream-json, then Copilot JSONL.
ECHO_QUIET = frozenset(
    {
        "rate_limit_event",
        "stream_event",
        "user.message",
        "assistant.turn_start",
        "assistant.turn_end",
        "assistant.usage",
        "assistant.message_delta",
        "assistant.reasoning_delta",
        "tool.execution_partial_result",
        "tool.execution_progress",
        "session.usage_info",
    }
)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _json_object(text: str) -> dict[str, Any] | None:
    if not text.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _echo_clean(line: str) -> str:
    """One line without ANSI colours or control characters (a stray CR would start a new line in
    the job console, one that the stage prefix doesn't guard)."""
    return _CONTROL_RE.sub(" ", _ANSI_RE.sub("", line)).rstrip()


def _echo_text(text: str, label: str = "") -> list[str]:
    """A block of text cut to ECHO_TEXT_CHARS, blank lines dropped; a label goes on the first
    line and indents the rest."""
    lines = [s for s in map(_echo_clean, clip(text.strip(), ECHO_TEXT_CHARS).splitlines()) if s]
    if not lines or not label:
        return lines
    return [f"{label}: {lines[0].strip()}", *(f"  {s}" for s in lines[1:])]


def _echo_short(text: str, label: str = "") -> list[str]:
    """One line: the first non-blank line of `text` cut to ECHO_LINE_CHARS, plus how many lines
    followed it. Blank text gives just the label."""
    lines = [s.strip() for s in map(_echo_clean, text.splitlines()) if s.strip()]
    if not lines:
        return [label] if label else []
    n = len(lines) - 1
    more = f" (+{n} line{'s' if n != 1 else ''})" if n else ""
    line = clip(lines[0], ECHO_LINE_CHARS) + more
    return [f"{label}: {line}" if label else line]


def _echo_tool(name: object, args: object) -> str:
    """`tool NAME: DETAIL`, the detail being the most telling argument (a path, a command)."""
    label = "tool " + (clip(_echo_clean(_as_str(name)).strip(), ECHO_LINE_CHARS) or "?")
    if isinstance(args, dict):
        for key in ECHO_DETAIL_KEYS:
            detail = _echo_short(_as_str(args.get(key)))
            if detail:
                return f"{label}: {detail[0]}"
        todos = args.get("todos")
        if isinstance(todos, list):
            return f"{label}: {len(todos)} todos"
    return label


def _content_blocks(message: object) -> list[dict[str, Any]]:
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_result_text(content: object) -> str:
    """A Claude tool_result's content: a string, or a list of parts whose text parts count."""
    if isinstance(content, list):
        parts = (p.get("text") for p in content if isinstance(p, dict) and p.get("type") == "text")
        return "\n".join(_as_str(t) for t in parts)
    return _as_str(content)


class Echo:
    """Prints one agent call's output on stderr as it arrives, each line prefixed `[stage id]`.

    Claude stream-json and Copilot JSONL events become agent text, reasoning, tool calls, tool
    results and the closing line; known bookkeeping events print nothing, and anything else
    (plain text, other JSON) prints as plain text, cut short. An OnLine callback."""

    def __init__(self, stage_id: str) -> None:
        self.prefix = f"[{stage_id}] "
        self.reasoning = ""  # the last reasoning shown: Copilot can send it twice
        self.broken = False

    def __call__(self, _stream: str, text: str, truncated: bool) -> None:
        if self.broken:
            return
        try:
            lines = self.lines(text, truncated)
        except Exception as exc:  # noqa: BLE001 cosmetic: a formatting bug must not stop the reader
            self.broken = True
            lines = [f"(echo stopped: {type(exc).__name__})"]
        if lines:
            block = "".join(f"{self.prefix}{line}\n" for line in lines)
            with STDERR_LOCK, contextlib.suppress(OSError, ValueError, AttributeError):
                sys.stderr.write(block)
                sys.stderr.flush()

    def lines(self, text: str, truncated: bool = False) -> list[str]:
        """The console lines (without the prefix) for one line of output."""
        event = None if truncated else _json_object(text)
        kind = event.get("type") if event is not None else None
        if event is None or not isinstance(kind, str):
            return _echo_short(text)
        raw = event.get("data")
        data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        match kind:
            # Claude Code: --output-format stream-json --verbose
            case "system":
                return self._started(event.get("model")) if event.get("subtype") == "init" else []
            case "assistant":
                return self._claude_message(event.get("message"))
            case "user":
                return self._claude_tool_results(event.get("message"))
            case "result":
                return self._claude_result(event)
            # Copilot CLI: --output-format json, {"type": "<area>.<event>", "data": {...}}
            case "session.start":
                return self._started(data.get("selectedModel"))
            case "assistant.message":
                reasoning = self._reasoning(data.get("reasoningText"))
                return [*reasoning, *_echo_text(_as_str(data.get("content")))]
            case "assistant.reasoning":
                return self._reasoning(data.get("content"))
            case "assistant.intent":
                return _echo_short(_as_str(data.get("intent")), "intent")
            case "tool.execution_start":
                return [_echo_tool(data.get("toolName"), data.get("arguments"))]
            case "tool.execution_complete":
                return self._copilot_tool_result(data)
            case "session.error":
                return _echo_short(_as_str(data.get("message")), "error")
            case "abort":
                return _echo_short(_as_str(data.get("reason")), "aborted")
            case "session.shutdown":
                n = data.get("totalPremiumRequests")
                if type(n) not in (int, float):
                    return ["done"]
                return [f"done: {n:g} premium request{'s' if n != 1 else ''}"]
        if kind in ECHO_QUIET or event.get("ephemeral") is True:
            return []
        return _echo_short(text)

    def _started(self, model: object) -> list[str]:
        name = _echo_clean(_as_str(model)).strip()
        return [f"session started ({clip(name, ECHO_LINE_CHARS)})" if name else "session started"]

    def _reasoning(self, text: object) -> list[str]:
        value = _as_str(text).strip()
        if not value or value == self.reasoning:
            return []
        self.reasoning = value
        return _echo_text(value, "thinking")

    def _claude_message(self, message: object) -> list[str]:
        out: list[str] = []
        for block in _content_blocks(message):
            kind = _as_str(block.get("type"))
            if kind == "text":
                out += _echo_text(_as_str(block.get("text")))
            elif kind == "thinking":
                out += self._reasoning(block.get("thinking"))
            elif kind.endswith("tool_use"):  # tool_use, server_tool_use, mcp_tool_use
                out.append(_echo_tool(block.get("name"), block.get("input")))
        return out

    def _claude_tool_results(self, message: object) -> list[str]:
        out: list[str] = []
        for block in _content_blocks(message):
            if block.get("type") == "tool_result":
                label = "tool error" if block.get("is_error") is True else "result"
                out += _echo_short(_tool_result_text(block.get("content")), label)
        return out

    def _claude_result(self, event: dict[str, Any]) -> list[str]:
        subtype = _as_str(event.get("subtype"))
        failed = event.get("is_error") is True or subtype not in ("", "success")
        parts = [subtype if subtype not in ("", "success") else "error" if failed else "success"]
        turns = event.get("num_turns")
        if type(turns) is int:
            parts.append(f"{turns} turn{'s' if turns != 1 else ''}")
        cost = event.get("total_cost_usd")
        if type(cost) in (int, float):
            parts.append(f"${cost:.4f}")
        line = "done: " + ", ".join(parts)
        detail = _echo_short(_as_str(event.get("result"))) if failed else []
        return [f"{line}: {detail[0]}" if detail else line]

    def _copilot_tool_result(self, data: dict[str, Any]) -> list[str]:
        if data.get("success") is False:
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            return _echo_short(_as_str(message), "tool error")
        result = data.get("result")
        content = result.get("content") if isinstance(result, dict) else result
        return _echo_short(_as_str(content), "result")
