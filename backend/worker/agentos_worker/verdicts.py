"""A loop block check's verdict (`Verdict`: pass, or fail with notes): a shell check's from
its exit code, and the next round's feedback from the failing checks' notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VERDICT_FILE = ".agentos/verdict.json"  # where a `file`-channel check writes its verdict
VERDICT_FILE_BYTES = 256 * 1024
VERDICT_NOTES_MAX = 20_000  # agentos.models.VERDICT_NOTES_MAX
SHELL_CHECK_LINES = 200  # a failing shell check's notes: at most this many last output lines
FEEDBACK_MAX = 40_000  # the combined notes the next round's calls get


@dataclass(frozen=True)
class Verdict:
    """A check's valid verdict in one round."""

    verdict: Literal["pass", "fail"]
    notes: str


def shell_verdict(code: int, lines: list[str]) -> Verdict:
    """A shell check's verdict: exit 0 passes; otherwise the end of its output is the notes."""
    if code == 0:
        return Verdict("pass", "")
    output = "\n".join(lines[-SHELL_CHECK_LINES:])
    if not output.strip():
        return Verdict("fail", f"Exited with code {code} (no output).")
    head = f"Exited with code {code}. The last lines of its output:\n"
    return Verdict("fail", head + output[len(head) - VERDICT_NOTES_MAX :])


def combine_feedback(failing: list[tuple[str, str]]) -> str:
    """The (stage name, notes) of a round's failing checks, as the next round's feedback."""
    text = "\n\n".join(f"### {name}\n{notes.strip()}" for name, notes in failing)
    if len(text) <= FEEDBACK_MAX:
        return text
    marker = "\n[truncated]"
    return text[: FEEDBACK_MAX - len(marker)] + marker
