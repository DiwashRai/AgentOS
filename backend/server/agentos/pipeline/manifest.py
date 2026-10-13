"""Resolve stages to argv/stdin and build the protocol-v1 manifest (SPEC §7).

Pure. Dispatch validates a run with `render_stages` (same code path as the claim-time build), so
a run that dispatched can't fail to render at claim unless the ticket changed in between.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentos.errors import ApiError
from agentos.models import (
    FileEntry,
    Manifest,
    ManifestFile,
    ManifestLimits,
    ManifestStage,
    ManifestTicket,
    ManifestWorkspace,
    Stage,
    TemplateDefaults,
)
from agentos.pipeline.render import TicketRef, render_prompt
from agentos.pipeline.runtime import RuntimesConfig, space_defaults
from agentos.pipeline.scaffold import names
from agentos.validate import MAX_AGENT_LINE_BYTES, MAX_FILE_BYTES, path_key


def _substitute(args: Sequence[str], placeholder: str, value: str) -> list[str]:
    """Placeholders replace whole argv elements only: `--model={model}` stays literal."""
    return [value if a == placeholder else a for a in args]


def resolve_agent(
    stage: Stage, prompt: str, runtimes: RuntimesConfig, *, defaults: TemplateDefaults
) -> tuple[list[str], str | None]:
    """Final argv + stdin for an agent stage: `argv`, then `model_args` (if a model resolves),
    then `effort_args` (if an effort resolves), then `agent_args` (if `stage.agent`), then
    `prompt_args` (if `prompt_via = "arg"`). With `prompt_via = "stdin"` the prompt is the stdin.
    Unknown runtime → 422 `invalid_stage`.

    A null `stage.runtime` / `stage.model` / `stage.effort` takes the Space default
    (`space_defaults`: the template's `defaults`, else the config's `default_runtime` / no model /
    no effort). The Space model and effort belong to the Space runtime, so a stage on another
    runtime without its own gets none. A runtime with empty `effort_args` passes no effort."""
    space = space_defaults(defaults, runtimes)
    name = stage.runtime or space.runtime
    spec = runtimes.runtimes.get(name)
    if spec is None:
        raise ApiError(422, "invalid_stage", f"stage {stage.id}: unknown runtime {name!r}")
    argv = list(spec.argv)
    on_space_runtime = name == space.runtime
    model = stage.model or (space.model if on_space_runtime else None)
    if model:
        argv += _substitute(spec.model_args, "{model}", model)
    effort = stage.effort or (space.effort if on_space_runtime else None)
    if effort:
        argv += _substitute(spec.effort_args, "{effort}", effort)
    if stage.agent:
        argv += _substitute(spec.agent_args, "{agent}", stage.agent)
    if spec.prompt_via == "arg":
        argv += _substitute(spec.prompt_args, "{prompt}", prompt)
        return argv, None
    return argv, prompt


def render_stages(
    run_id: int,
    ticket: TicketRef,
    stages: Sequence[Stage],
    runtimes: RuntimesConfig,
    *,
    defaults: TemplateDefaults,
) -> list[ManifestStage]:
    """Render prompts and resolve runtimes. 422 `render_error` / `invalid_stage` on failure.

    Doesn't re-check the segment (`segment.check_segment` does that at dispatch).
    """
    out: list[ManifestStage] = []
    for stage in stages:
        if stage.kind == "shell":
            out.append(
                ManifestStage(
                    id=stage.id,
                    name=stage.name,
                    kind="shell",
                    timeout_s=stage.timeout_s,
                    command=stage.command,
                )
            )
            continue
        prompt = render_prompt(stage, ticket, run_id)
        argv, stdin = resolve_agent(stage, prompt, runtimes, defaults=defaults)
        out.append(
            ManifestStage(
                id=stage.id,
                name=stage.name,
                kind="agent",
                timeout_s=stage.timeout_s,
                argv=argv,
                stdin=stdin,
            )
        )
    return out


def build_manifest(
    run_id: int,
    ticket: TicketRef,
    stages: Sequence[Stage],
    bundle: Sequence[FileEntry],
    runtimes: RuntimesConfig,
    *,
    defaults: TemplateDefaults,
    library: Sequence[FileEntry] = (),
) -> Manifest:
    """The manifest a worker receives at claim.

    `bundle` is the ticket's current (non-deleted) files → `sync=True`. `library` (M1 seam:
    library-set files) → `sync=False`, listed first; the bundle wins a (case-insensitive) path
    collision. In no-repo mode the workspace's `repo_url`/`base_branch`/`branch` are null.
    """
    n = names(ticket.key, ticket.slug, ticket.workspace_mode)
    bundle_paths = {path_key(f.path) for f in bundle}
    files = [
        ManifestFile(path=f.path, content=f.content, mode=f.mode, sync=False)
        for f in library
        if path_key(f.path) not in bundle_paths
    ] + [ManifestFile(path=f.path, content=f.content, mode=f.mode, sync=True) for f in bundle]
    repo = ticket.workspace_mode == "repo"
    return Manifest(
        run_id=run_id,
        ticket=ManifestTicket(key=ticket.key, slug=ticket.slug, title=ticket.title),
        workspace=ManifestWorkspace(
            mode=ticket.workspace_mode,
            repo_url=ticket.repo_url if repo else None,
            base_branch=ticket.base_branch if repo else None,
            branch=n.branch,
        ),
        files=files,
        sync_dirs=[n.skill_dir],
        stages=render_stages(run_id, ticket, stages, runtimes, defaults=defaults),
        limits=ManifestLimits(max_file_bytes=MAX_FILE_BYTES, max_line_bytes=MAX_AGENT_LINE_BYTES),
    )
