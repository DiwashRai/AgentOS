"""Agent runtime definitions: argv templates from `agentos.toml` (SPEC §7).

Pure: config.py parses the TOML into these; manifest building resolves them into a final argv.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeSpec(BaseModel):
    """One `[runtimes.<name>]` table. Placeholders (`{model}`, `{agent}`, `{prompt}`) replace
    whole argv elements; optional groups are omitted when their value is null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: list[str] = Field(min_length=1)
    model_args: list[str] = Field(default_factory=list)
    agent_args: list[str] = Field(default_factory=list)
    prompt_via: Literal["stdin", "arg"] = "stdin"
    prompt_args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _prompt_args_match_prompt_via(self) -> Self:
        if self.prompt_via == "arg" and "{prompt}" not in self.prompt_args:
            raise ValueError('prompt_via = "arg" needs prompt_args containing "{prompt}"')
        if self.prompt_via == "stdin" and self.prompt_args:
            raise ValueError('prompt_args are only used with prompt_via = "arg"')
        return self


class RuntimesConfig(BaseModel):
    """The whole `agentos.toml`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_runtime: str
    runtimes: dict[str, RuntimeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_exists(self) -> Self:
        if self.default_runtime not in self.runtimes:
            raise ValueError(f"default_runtime {self.default_runtime!r} is not in [runtimes]")
        return self
