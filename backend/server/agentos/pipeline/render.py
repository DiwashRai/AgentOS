"""Sandboxed Jinja rendering of prompts and scaffold entries (SPEC §7 "Render").

Pure: callers load the ticket and pass a `TicketRef`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from agentos.errors import ApiError
from agentos.models import FileEntry, Stage, TicketType, WorkspaceMode
from agentos.pipeline.scaffold import names
from agentos.validate import (
    check_bundle_size,
    check_file_size,
    check_unique_paths,
    validate_block_content,
    validate_path,
)


@dataclass(frozen=True, slots=True)
class TicketRef:
    """The ticket fields rendering and manifest building need, as plain values."""

    key: str
    slug: str
    title: str
    description: str
    type: TicketType
    source_url: str | None
    repo_url: str | None
    base_branch: str | None
    workspace_mode: WorkspaceMode


def _yaml_unsafe(c: str) -> bool:
    """Characters a YAML double-quoted scalar can't hold raw (DEL, C1 controls, surrogates,
    U+FFFE/U+FFFF), plus the line separators YAML parsers fold (NEL is in C1; LS, PS). JSON allows
    them raw. json.dumps already escapes C0 controls."""
    o = ord(c)
    return 0x7F <= o <= 0x9F or o in (0x2028, 0x2029, 0xFFFE, 0xFFFF) or 0xD800 <= o <= 0xDFFF


def yaml_safe_json(value: Any) -> str:
    """`tojson` that is valid JSON *and* a valid YAML double-quoted scalar.

    Jinja's own `tojson` uses `ensure_ascii`, which writes astral characters (emoji) as surrogate
    pair escapes (`\\ud83d\\ude00`); YAML treats each `\\u` escape as one character, so the pair
    becomes two invalid lone surrogates. This keeps non-ASCII raw (UTF-8) instead, and escapes
    only what YAML can't hold. It also doesn't HTML-escape `<>&'`, which plain-text files don't
    need.
    """
    text = json.dumps(value, ensure_ascii=False)
    return "".join(f"\\u{ord(c):04x}" if _yaml_unsafe(c) else c for c in text)


_ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
_ENV.filters["tojson"] = yaml_safe_json


def render_context(
    ticket: TicketRef, *, stage: Stage | None = None, run_id: int | None = None
) -> dict[str, Any]:
    """SPEC §7 context. `stage` and `run` are absent (not null) when not given, so a scaffold
    entry that references them fails with a render error instead of rendering 'None'."""
    n = names(ticket.key, ticket.slug, ticket.workspace_mode)
    ctx: dict[str, Any] = {
        "ticket": {
            "key": ticket.key,
            "title": ticket.title,
            "description": ticket.description,
            "type": ticket.type,
            "source_url": ticket.source_url,
            "repo_url": ticket.repo_url,
            "base_branch": ticket.base_branch,
            "branch": n.branch,
        },
        "skill": {"name": n.skill_name},
        "paths": {
            "skill_dir": n.skill_dir,
            "skill_md": n.skill_md,
            "tasks_md": n.tasks_md,
            "learnings": n.learnings,
            "swarm": n.swarm,
            "out": n.out,
        },
    }
    if stage is not None:
        ctx["stage"] = {"id": stage.id, "name": stage.name}
    if run_id is not None:
        ctx["run"] = {"id": run_id}
    return ctx


def _render(source: str, ctx: dict[str, Any], where: str) -> str:
    try:
        return _ENV.from_string(source).render(ctx)
    except TemplateError as e:
        raise ApiError(422, "render_error", f"{where}: {e.message or type(e).__name__}") from e
    except Exception as e:  # e.g. ZeroDivisionError from `{{ 1/0 }}` in user-written Jinja
        raise ApiError(422, "render_error", f"{where}: {type(e).__name__}: {e}") from e


def render_prompt(stage: Stage, ticket: TicketRef, run_id: int) -> str:
    """Render one agent stage's prompt. Errors → 422 `render_error` naming the stage."""
    ctx = render_context(ticket, stage=stage, run_id=run_id)
    return _render(stage.prompt, ctx, f"stage {stage.id}")


def _prefixed(e: ApiError, where: str) -> ApiError:
    return ApiError(e.status, e.code, f"{where}: {e.message}")


def render_scaffold(entries: Sequence[FileEntry], ticket: TicketRef) -> list[FileEntry]:
    """Render a template scaffold (paths and content) for a ticket, once, at creation.

    Jinja errors → 422 `render_error`; an invalid rendered path, duplicate path (case-insensitive)
    or a marker inside block content → 422 `validation_error`; oversize → 413 `too_large`. Each
    message names the entry (its unrendered path).
    """
    ctx = render_context(ticket)
    out: list[FileEntry] = []
    for entry in entries:
        where = f"scaffold entry {entry.path!r}"
        path = _render(entry.path, ctx, f"{where} path")
        content = _render(entry.content, ctx, f"{where} content")
        try:
            path = validate_path(path)
            check_file_size(content)
            if entry.mode == "block":
                validate_block_content(content)
        except ApiError as e:
            raise _prefixed(e, where) from e
        out.append(FileEntry(path=path, content=content, mode=entry.mode))
    try:
        check_unique_paths(f.path for f in out)
        check_bundle_size(f.content for f in out)
    except ApiError as e:
        raise _prefixed(e, "scaffold") from e
    return out


# Dummy tickets for checking a template's scaffold when it is saved (SPEC §6): one per workspace
# mode, with an awkward title, so `{% if ticket.branch %}` paths are exercised both ways.
DUMMY_TICKETS = (
    TicketRef(
        key="AOS-1",
        slug="example",
        title='Example: "quoted" #title',
        description="Example description.",
        type="task",
        source_url=None,
        repo_url="https://example.com/org/repo.git",
        base_branch="main",
        workspace_mode="repo",
    ),
    TicketRef(
        key="AOS-1",
        slug="example",
        title="Example",
        description="",
        type="task",
        source_url="https://example.com/browse/AOS-1",
        repo_url=None,
        base_branch=None,
        workspace_mode="none",
    ),
)


def check_scaffold(entries: Sequence[FileEntry]) -> None:
    """Template save: the scaffold must render to valid, unique paths for any ticket."""
    for ticket in DUMMY_TICKETS:
        render_scaffold(entries, ticket)
