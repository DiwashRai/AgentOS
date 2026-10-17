"""What every gate module shares: the repo paths, the defensive kinds, a finding, layers."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTOS = "backend/server/agentos"
FEATURES = "web/src/features/"
# The composition root wires every feature together; its fan-out is its job.
FAN_OUT_EXEMPT = {"main"}
# `is None` and optional parameters are not counted: they are syntax, and capping them pushes code
# towards `if not x`, getattr and Any. The types (the `any` and `optional_fields` gates) carry it.
KINDS = ("isinstance", "try", "broad_except", "getattr_default", "hasattr", "suppress")


@dataclass(frozen=True)
class Finding:
    """One measured function, file or subpackage."""

    path: str
    line: int
    name: str
    score: int

    @property
    def id(self) -> str:
        return f"{self.path}::{self.name}"


def layer_of(path: str, layers: dict[str, list[str]]) -> str:
    for layer, patterns in layers.items():
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return layer
    return "other"
