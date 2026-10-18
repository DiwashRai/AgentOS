"""Where a check's verdict comes from: the CLI's result line or the verdict file, checked
by `checked_verdict` (a Verdict, or why the value isn't one).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos_worker.files import read_regular
from agentos_worker.fit import Unfit
from agentos_worker.parse import parse
from agentos_worker.text import clip, decode_utf8
from agentos_worker.verdicts import VERDICT_FILE, VERDICT_FILE_BYTES, VERDICT_NOTES_MAX, Verdict


def checked_verdict(value: object) -> Verdict | Unfit:
    """`value` as a check's verdict, or why it isn't one. The shape is
    agentos.models.VERDICT_SCHEMA ({"verdict": "pass"|"fail", "notes": str}, nothing else), plus
    the notes rules."""
    verdict = parse(Verdict, value)
    match verdict:
        case Verdict(notes=notes) if len(notes) > VERDICT_NOTES_MAX:
            return Unfit(f"notes: longer than {VERDICT_NOTES_MAX} characters")
        case Verdict(verdict="fail", notes=notes) if not notes.strip():
            return Unfit('notes: must not be empty when the verdict is "fail"')
        case _:
            return verdict


def result_verdict(result: dict[str, Any] | None) -> tuple[object, str | None]:
    """(value, error): the `structured_output` of a CLI's last stream-json result line."""
    if result is None:
        return None, "the CLI printed no result line"
    value = result.get("structured_output")
    if value is None:
        subtype = result.get("subtype")
        detail = f" (subtype: {clip(str(subtype), 100)})" if subtype else ""
        return None, f"the CLI's result has no structured_output{detail}"
    return value, None


def file_verdict(root: Path) -> tuple[object, str | None]:
    """(value, error): the JSON in the workspace's verdict file (a regular file, no symlinks)."""
    data, err = read_regular(root, VERDICT_FILE, limit=VERDICT_FILE_BYTES)
    if err:
        return None, f"{VERDICT_FILE}: {err}"
    if data is None:
        return None, f"{VERDICT_FILE} was not written"
    text = decode_utf8(data)
    if text is None:
        return None, f"{VERDICT_FILE} is not valid UTF-8"
    try:
        return json.loads(text.removeprefix("\ufeff")), None
    except (ValueError, RecursionError) as exc:
        return None, f"{VERDICT_FILE} is not valid JSON: {exc}"
