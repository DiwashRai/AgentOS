"""Reading decoded JSON into our types (`parse`: the value, or where and why it doesn't
fit), the stage's wire shape (`_StageRecord`, made a Stage by `_stage`), and `load_package`.
"""

from __future__ import annotations

import inspect
import json
import typing
from dataclasses import dataclass, is_dataclass
from datetime import datetime
from types import NoneType, UnionType
from typing import Literal, TypeVar, cast

from agentos_worker.fit import Misfit, Unfit
from agentos_worker.package import (
    PROTOCOL,
    AgentCheck,
    AgentStage,
    Invocation,
    Package,
    PackageError,
    ShellStage,
    Stage,
    check_loops,
)
from agentos_worker.text import clip

T = TypeVar("T")

# A misfit names what was expected, and the JSON type it got.
_EXPECTED: dict[type, str] = {
    str: "a string",
    int: "an integer",
    bool: "a boolean",
    list: "an array",
    dict: "a JSON object",
}
_LEAVES = (str, int, bool)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    for kind, name in ((bool, "boolean"), (str, "string"), (list, "array"), (dict, "object")):
        if isinstance(value, kind):
            return name
    return "number" if isinstance(value, int | float) else type(value).__name__


def _expect(tp: type[T], value: object) -> T:
    """`value` if its type is exactly `tp` (so True is not an int), else a misfit."""
    if type(value) is not tp:
        raise ValueError(f"expected {_EXPECTED[tp]}, got {_json_type(value)}")
    return value


def _unsupported(tp: object) -> TypeError:
    return TypeError(f"parse can't read {tp!r}: see parse() for the types it reads")


def parse(
    tp: type[T], value: object, *, extra: Literal["forbid", "ignore"] = "forbid"
) -> T | Unfit:
    """`value` (decoded JSON) as a `tp`, or where and why it first doesn't fit, reading fields in
    declared order. The grammar: frozen dataclasses (exactly their keys, or unknown keys ignored
    with `extra="ignore"`; a field with a default is an optional key), tuple[X, ...],
    dict[str, X], X | None, Literal (of strings), str, int (not bool), bool, object (any value,
    unchecked), a datetime (ISO, with a time zone), and a Stage (read flat as a _StageRecord,
    then made one by _stage).
    Any other annotation is a bug in our types: TypeError, naming it."""
    fit = _Fit(extra)
    try:
        return cast(T, fit.value(tp, value))
    except Misfit as exc:  # a rule between fields: the path goes on to the field
        return fit.unfit(exc, f".{exc.field}")
    except ValueError as exc:  # a misfit; `fit.at` is where
        return fit.unfit(exc)


class _Fit:
    """One parse: a reader per kind, each raising ValueError where the value doesn't fit. The
    type forms come from typing (get_type_hints, get_args), which hands them over untyped."""

    def __init__(self, extra: Literal["forbid", "ignore"]) -> None:
        self.extra = extra
        self.at: list[str] = []  # the path being read: a step per field, item or key

    def unfit(self, exc: ValueError, *steps: str) -> Unfit:
        where = "".join([*self.at, *steps]).removeprefix(".")
        return Unfit(f"{where}: {exc}" if where else str(exc))

    def value(self, tp: type[object], value: object) -> object:
        origin = typing.get_origin(tp)
        if tp == Stage:
            return _stage(self.record(_StageRecord, value))
        if origin is tuple:
            return self.items(tp, value)
        if origin is dict:
            return self.mapping(tp, value)
        if origin in (UnionType, typing.Union):
            return self.optional(tp, value)
        if origin is Literal:
            return self.literal(typing.get_args(tp), value)
        if tp is datetime:
            return self.stamp(value)
        if is_dataclass(tp):
            return self.record(tp, value)
        if tp in _LEAVES:
            return _expect(tp, value)
        if tp is object:  # any JSON value, as it came
            return value
        raise _unsupported(tp)

    def item(self, step: str, tp: type[object], value: object) -> object:
        self.at.append(step)
        read = self.value(tp, value)
        self.at.pop()
        return read

    def record(self, tp: type[T], value: object) -> T:
        raw = _expect(dict, value)
        hints = typing.get_type_hints(tp)
        fields = inspect.signature(tp).parameters  # a dataclass's are its fields, in order
        missing = [k for k, f in fields.items() if k not in raw and f.default is f.empty]
        if missing:
            raise ValueError(f"missing key: {missing[0]}")
        unexpected = sorted(map(str, raw.keys() - fields.keys()))
        if unexpected and self.extra == "forbid":
            raise ValueError(f"unexpected key: {unexpected[0]}")
        return tp(**{k: self.item(f".{k}", hints[k], raw[k]) for k in fields if k in raw})

    def items(self, tp: type[object], value: object) -> tuple[object, ...]:
        args = typing.get_args(tp)
        if len(args) != 2 or args[1] is not Ellipsis:
            raise _unsupported(tp)
        return tuple(self.item(f"[{i}]", args[0], v) for i, v in enumerate(_expect(list, value)))

    def mapping(self, tp: type[object], value: object) -> dict[str, object]:
        key, item = typing.get_args(tp)
        if key is not str:
            raise _unsupported(tp)
        raw = _expect(dict, value)
        return {_expect(str, k): self.item(f".{k}", item, v) for k, v in raw.items()}

    def optional(self, tp: type[object], value: object) -> object:
        """X | None, in either order."""
        rest = [arg for arg in typing.get_args(tp) if arg is not NoneType]
        if len(rest) != 1:
            raise _unsupported(tp)
        return None if value is None else self.value(rest[0], value)

    def literal(self, options: tuple[object, ...], value: object) -> object:
        if value not in options:
            listed = " or ".join(json.dumps(option) for option in options)
            raise ValueError(f"expected {listed}, got {clip(json.dumps(value), 100)}")
        return value

    def stamp(self, value: object) -> datetime:
        stamp = datetime.fromisoformat(_expect(str, value))
        if stamp.tzinfo is None:
            raise ValueError("expected a time zone")
        return stamp


@dataclass(frozen=True)
class _StageRecord:
    """A stage as the package has it (agentos.models.ManifestStage); _stage makes it a Stage."""

    id: str
    name: str
    kind: Literal["agent", "shell"]
    timeout_s: int
    argv: tuple[str, ...] | None = None
    stdin: str | None = None
    command: str | None = None
    check: bool = False
    rounds: tuple[Invocation, ...] = ()
    repairs: tuple[Invocation, ...] = ()
    verdict_from: Literal["structured_output", "file"] | None = None
    verdict_repairs: int = 0


def _stage(s: _StageRecord) -> Stage:
    """The stage's variant: the one place the agent and shell rules live (a misfit otherwise)."""
    match s.kind:
        case "shell":
            if s.command is None:
                raise Misfit("command", "a shell stage needs a command")
            if s.argv is not None or s.stdin is not None:
                raise ValueError("a shell stage takes no argv or stdin")
            if s.rounds or s.repairs:
                raise ValueError("a shell stage takes no rounds or repairs")
            return ShellStage(s.id, s.name, s.timeout_s, s.command, s.check)
        case "agent":
            if not s.argv:  # null or empty
                raise Misfit("argv", "expected a program and its arguments, got none")
            if s.command is not None:
                raise Misfit("command", "an agent stage takes no command")
            return AgentStage(s.id, s.name, s.timeout_s, s.argv, s.stdin, s.rounds, _check_of(s))


def _check_of(s: _StageRecord) -> AgentCheck | None:
    """An agent stage's check, if it is one."""
    if not s.check:
        if s.repairs:
            raise Misfit("repairs", "only an agent check is repaired")
        return None
    if s.verdict_from is None:
        raise Misfit("verdict_from", "an agent check needs structured_output or file")
    return AgentCheck(s.verdict_from, s.verdict_repairs if s.repairs else 0, s.repairs)


def load_package(raw: object) -> Package:
    """The job's package: its protocol first, then the whole of it read into our types (parse),
    then its loop shape (check_loops). Anything that doesn't fit is a PackageError."""
    protocol = raw.get("protocol") if isinstance(raw, dict) else None
    if protocol != PROTOCOL:
        raise PackageError(f"unsupported protocol {protocol!r} (worker speaks {PROTOCOL})")
    match parse(Package, raw):
        case Unfit(why=why):
            raise PackageError(f"malformed package: {why}")
        case Package() as package:
            check_loops(package)
            return package
