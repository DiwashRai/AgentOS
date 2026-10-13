"""Agent runtimes (SPEC §7): argv templates, edited by admins in Settings > Runners (the db
`runtimes` area, `agentos.space_store`) and resolved into a final argv when a manifest is built.

Pure: the settings store passes a `RuntimesConfig` in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentos.models import RUNTIME_NAME_PATTERN, TemplateDefaults


class RuntimeSpec(BaseModel):
    """One runtime's argv template. Placeholders (`{model}`, `{effort}`, `{agent}`, `{prompt}`)
    replace whole argv elements; optional groups are omitted when their value is null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: list[str] = Field(min_length=1)
    model_args: list[str] = Field(default_factory=list)
    effort_args: list[str] = Field(default_factory=list)
    agent_args: list[str] = Field(default_factory=list)
    prompt_via: Literal["stdin", "arg"] = "stdin"
    prompt_args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _prompt_args_match_prompt_via(self) -> Self:
        if not self.argv[0].strip():
            raise ValueError("argv: the first element (the program) must not be blank")
        groups = (self.argv, self.model_args, self.effort_args, self.agent_args, self.prompt_args)
        if any("\x00" in arg for group in groups for arg in group):
            raise ValueError("arguments must not contain NUL characters")
        if self.prompt_via == "arg" and "{prompt}" not in self.prompt_args:
            raise ValueError('prompt_via = "arg" needs prompt_args containing "{prompt}"')
        if self.prompt_via == "stdin" and self.prompt_args:
            raise ValueError('prompt_args are only used with prompt_via = "arg"')
        return self


class NamedRuntime(RuntimeSpec):
    """A runtime as Settings > Runners lists it (and the db stores it): its spec plus its name."""

    name: str = Field(pattern=RUNTIME_NAME_PATTERN)

    def spec(self) -> RuntimeSpec:
        return RuntimeSpec.model_validate(self.model_dump(exclude={"name"}))


class RuntimesConfig(BaseModel):
    """The runtimes by name, and the server default (`default_runtime`: the first in Settings)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_runtime: str
    runtimes: dict[str, RuntimeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_exists(self) -> Self:
        if self.default_runtime not in self.runtimes:
            raise ValueError(f"default_runtime {self.default_runtime!r} is not in [runtimes]")
        return self

    @classmethod
    def from_entries(cls, entries: Sequence[NamedRuntime]) -> RuntimesConfig:
        """From the Settings list: in order, the first is the default. ValueError when the list is
        empty or a name repeats."""
        if not entries:
            raise ValueError("at least one runtime is needed")
        runtimes: dict[str, RuntimeSpec] = {}
        for entry in entries:
            if entry.name in runtimes:
                raise ValueError(f"runtime {entry.name!r} is listed twice")
            runtimes[entry.name] = entry.spec()
        return cls(default_runtime=entries[0].name, runtimes=runtimes)

    def entries(self) -> list[NamedRuntime]:
        """As Settings lists them: the default first, then the others in their order."""
        names = [self.default_runtime, *(n for n in self.runtimes if n != self.default_runtime)]
        return [
            NamedRuntime.model_validate({"name": n, **self.runtimes[n].model_dump()}) for n in names
        ]


# What a Space starts with when no agentos.toml seeds it (agentos.toml.example's runtimes).
BUILTIN_RUNTIMES: tuple[NamedRuntime, ...] = (
    NamedRuntime(
        name="claude",
        argv=[
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
        ],
        model_args=["--model", "{model}"],
        effort_args=["--effort", "{effort}"],
        agent_args=["--agent", "{agent}"],
        prompt_via="stdin",
    ),
    NamedRuntime(
        name="copilot",
        argv=["copilot", "--allow-all", "--no-ask-user", "--output-format", "json"],
        model_args=["--model", "{model}"],
        effort_args=["--effort", "{effort}"],
        agent_args=["--agent", "{agent}"],
        prompt_via="arg",
        prompt_args=["-p", "{prompt}"],
    ),
)


class SpaceDefaults(BaseModel):
    """The effective defaults for an agent stage whose `runtime` / `model` / `effort` is null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: str
    model: str | None
    effort: str | None


def space_defaults(defaults: TemplateDefaults, runtimes: RuntimesConfig) -> SpaceDefaults:
    """The template's defaults, falling back to the server default runtime and to no model or
    effort (the runtime CLI's own default). The one place this chain lives: manifests and
    AppConfig."""
    return SpaceDefaults(
        runtime=defaults.runtime or runtimes.default_runtime,
        model=defaults.model,
        effort=defaults.effort,
    )
