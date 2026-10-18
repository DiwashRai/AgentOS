"""The result of one git call (`GitResult`) and the `GitRunner` protocol that makes one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GitResult:
    code: int
    out: bytes
    err: bytes

    def tail(self) -> str:
        text = (self.err or self.out).decode("utf-8", "replace").strip()
        return text.splitlines()[-1] if text else f"exit code {self.code}"

    def names(self) -> list[str]:
        """NUL-separated path output (`-z`)."""
        return [p for p in self.out.decode("utf-8", "replace").split("\0") if p]


class GitRunner(Protocol):
    """Runs git in the workspace (Run._git)."""

    def __call__(self, *args: str, stdin: bytes | None = None) -> GitResult: ...
