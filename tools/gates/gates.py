"""Gates: measured limits on how AgentOS's code grows. Separate from `just check`.

`just gates` runs every gate and prints a pass/fail line per gate, each offender as
`path:line  name  score > limit`. `just ratchet` lowers the baseline where the code improved and
never raises it. `--init` writes the first baseline.

The baseline (`quality-baseline.json` at the repo root) holds the limits, the layers (path globs,
first match wins: core, boundary, shell, worker), the defensive caps per file, the raw-tag caps per
feature, the named existing offenders and a `raises` list. With no baseline every cap is 0, nothing
is named and the limits are the defaults. A number in the baseline that went up against the
committed one (`git show HEAD:...`) needs a new `raises` entry naming it.

The core holds no `Any` (mypy with every `Any` an error, run on the core files only) and no domain
class with optional fields that stand in for a missing state type. The baseline's
`allowed.uncapped` names the kinds a layer may use freely (the boundary checks chaotic input).
"""

from __future__ import annotations

import argparse
import ast
import copy
import fnmatch
import io
import json
import re
import shutil
import subprocess
import sys
import time
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import complexipy
import grimp
import lizard  # type: ignore[import-untyped]  # lizard ships no type hints

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
BASELINE = ROOT / "quality-baseline.json"
AGENTOS = "backend/server/agentos"
PY_SCOPE = (f"{AGENTOS}/**/*.py", "backend/worker/worker.py")
TS_EXCLUDE = ("web/src/api/schema.ts", "*.test.ts", "*.test.tsx", "web/src/domain/fixtures.ts")
FEATURES = "web/src/features/"
# The composition root wires every feature together; its fan-out is its job.
FAN_OUT_EXEMPT = {"main"}

# `cyclomatic_python_tool` picks the Python cyclomatic counter: "lizard" (and/or, ternaries and
# comprehensions count; what the reference ranges were measured with) or "ruff" (C901, McCabe:
# they don't). The named cyclomatic exceptions are that tool's scores, so a switch goes with
# `--rebaseline-cyclomatic` and a `raises` entry naming both changes.
DEFAULT_LIMITS: dict[str, Any] = {
    "cognitive": 15,
    "cyclomatic": 10,
    "cyclomatic_python_tool": "lizard",
    "file_lines": 1000,
    "fan_out": 5,
    "optional_fields": 1,
    "any": 0,
}
PY_CYCLOMATIC_TOOLS = ("lizard", "ruff")
# fnmatch patterns ("*" also matches "/"), relative to the repo root. First match wins.
# core: domain types and rules on parsed values. boundary: everything touching the outside (HTTP,
# db rows, config, plugins). shell: the feature orchestration between them.
DEFAULT_LAYERS = {
    "core": [
        f"{AGENTOS}/models.py",
        f"{AGENTOS}/errors.py",
        f"{AGENTOS}/pipeline/*",
        f"{AGENTOS}/*/board.py",
        f"{AGENTOS}/*/rules.py",
    ],
    "boundary": [
        f"{AGENTOS}/validate.py",  # pure, but it parses raw input
        f"{AGENTOS}/*/routes.py",
        f"{AGENTOS}/*/deps.py",
        f"{AGENTOS}/launchers/*",
        f"{AGENTOS}/sources/*",
        f"{AGENTOS}/stores/*",
        f"{AGENTOS}/db.py",
        f"{AGENTOS}/config.py",
        f"{AGENTOS}/clock.py",
        f"{AGENTOS}/space_store.py",
        f"{AGENTOS}/http_*.py",
        f"{AGENTOS}/csrf.py",
        f"{AGENTOS}/meta.py",
        f"{AGENTOS}/spa.py",
        f"{AGENTOS}/openapi_export.py",
    ],
    "shell": [
        f"{AGENTOS}/*/service.py",
        f"{AGENTOS}/*/loops.py",
        f"{AGENTOS}/*/reaper.py",
        f"{AGENTOS}/*/dispatcher.py",
        f"{AGENTOS}/main.py",
        f"{AGENTOS}/context.py",
        f"{AGENTOS}/ratelimit.py",
        f"{AGENTOS}/ttlcache.py",
        f"{AGENTOS}/__init__.py",
        f"{AGENTOS}/*/__init__.py",
    ],
    "worker": ["backend/worker/worker.py"],
}
# `is None` and optional parameters are not counted: they are syntax, and capping them pushes code
# towards `if not x`, getattr and Any. The types (the `any` and `optional_fields` gates) carry it.
KINDS = ("isinstance", "try", "broad_except", "getattr_default", "hasattr", "suppress")
# What `--init` leaves uncapped per layer (the baseline's `allowed.uncapped` is what applies): the
# boundary is where chaotic input gets checked.
DEFAULT_UNCAPPED = {"boundary": ["isinstance", "try"]}
EXCEPTION_GATES = ("cognitive", "cyclomatic", "file_lines", "fan_out", "optional_fields", "any")
HINTS = {
    "isinstance": "parse it at the boundary; in the core, match on a union of your own types",
    "try": "clean up with `with` (contextlib.closing / ExitStack); "
    "a try belongs only inside a context manager",
    "optional_fields": "fields that are null together: model the states as separate types",
    "any": "parse it at the boundary into a typed value; the core never sees Any",
}
CONTEXT_METHODS = {"__enter__", "__exit__", "__aenter__", "__aexit__"}
PYDANTIC_BASES = {"BaseModel", "RootModel"}
MYPY_ERROR = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+): error: (?P<message>.*)$")
PY_SUPPRESSION = re.compile(r"noqa|type:\s*ignore|pragma:\s*no cover")
BROAD = {"Exception", "BaseException"}
BUILTIN_TYPES = {
    "bool",
    "bytes",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "set",
    "str",
    "tuple",
}


# ---- Measuring Python -------------------------------------------------------------------------


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


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _parse_annotation(text: str) -> ast.expr | None:
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError:
        return None


def _is_optional(annotation: ast.expr, aliases: frozenset[str] = frozenset()) -> bool:
    """Whether the annotation admits None anywhere: `X | None`, `Optional[X]`, `Union[.., None]`,
    a same-module alias of one (`aliases`), or a string annotation holding one."""
    match annotation:
        case ast.Constant(value=str(text)):
            parsed = _parse_annotation(text)
            return parsed is not None and _is_optional(parsed, aliases)
        case ast.Name(id=name):
            return name in aliases
        case ast.BinOp(op=ast.BitOr(), left=left, right=right) if _is_none(left) or _is_none(right):
            return True
        case ast.Subscript(value=ast.Name(id="Literal") | ast.Attribute(attr="Literal")):
            return False
        case ast.Subscript(value=ast.Name(id="Optional") | ast.Attribute(attr="Optional")):
            return True
        case ast.Subscript(
            value=ast.Name(id="Union") | ast.Attribute(attr="Union"), slice=ast.Tuple(elts=elts)
        ) if any(_is_none(e) for e in elts):
            return True
        case _:
            children = ast.iter_child_nodes(annotation)
            return any(_is_optional(c, aliases) for c in children if isinstance(c, ast.expr))


def _alias(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """A module-level `Name = <type>`, `Name: TypeAlias = <type>` or `type Name = <type>`."""
    match stmt:
        case ast.Assign(targets=[ast.Name(id=name)], value=value) if isinstance(
            value, ast.BinOp | ast.Subscript | ast.Name | ast.Constant
        ):
            return name, value
        case ast.AnnAssign(
            target=ast.Name(id=name),
            annotation=ast.Name(id="TypeAlias") | ast.Attribute(attr="TypeAlias"),
            value=ast.expr() as value,
        ):
            return name, value
        case ast.TypeAlias(name=ast.Name(id=name), value=value):
            return name, value
        case _:
            return None


def _optional_aliases(tree: ast.Module) -> frozenset[str]:
    """The module's own aliases of an optional type (`MaybeRun = RunFact | None`), chains too."""
    candidates = [pair for stmt in tree.body if (pair := _alias(stmt))]
    found: frozenset[str] = frozenset()
    while new := {n for n, v in candidates if n not in found and _is_optional(v, found)}:
        found |= new
    return found


@dataclass
class Names:
    """What one module calls the things the visitor looks for."""

    contextlib: set[str] = field(default_factory=lambda: {"contextlib"})
    suppress: set[str] = field(default_factory=lambda: {"suppress"})
    context_managers: set[str] = field(
        default_factory=lambda: {"contextmanager", "asynccontextmanager"}
    )
    optional_aliases: frozenset[str] = frozenset()
    pydantic_models: frozenset[str] = frozenset()


def _pydantic_models(tree: ast.Module) -> frozenset[str]:
    """The module's classes that subclass a pydantic model, directly or through each other."""
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    found: frozenset[str] = frozenset()

    def is_model(base: ast.expr) -> bool:
        match base:
            case ast.Name(id=name):
                return name in PYDANTIC_BASES or name in found
            case ast.Attribute(attr=name):
                return name in PYDANTIC_BASES
            case _:
                return False

    while new := {c.name for c in classes if c.name not in found and any(map(is_model, c.bases))}:
        found |= new
    return found


def _names(tree: ast.Module) -> Names:
    names = Names(optional_aliases=_optional_aliases(tree), pydantic_models=_pydantic_models(tree))
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                names.contextlib |= {
                    a.asname for a in aliases if a.name == "contextlib" and a.asname
                }
            case ast.ImportFrom(module="contextlib", names=aliases):
                for a in aliases:
                    local = a.asname or a.name
                    if a.name == "suppress":
                        names.suppress.add(local)
                    elif a.name in ("contextmanager", "asynccontextmanager"):
                        names.context_managers.add(local)
            case _:
                pass
    return names


def _reraises(handler: ast.ExceptHandler) -> bool:
    match handler.body[-1]:
        case ast.Raise(exc=None):
            return True
        case ast.Raise(exc=ast.Name(id=name)):
            return name == handler.name
        case _:
            return False


def _is_broad(handler: ast.ExceptHandler) -> bool:
    match handler.type:
        case None:
            return True
        case ast.Name(id=name):
            return name in BROAD
        case ast.Tuple(elts=elts):
            return any(isinstance(e, ast.Name) and e.id in BROAD for e in elts)
        case _:
            return False


def _call_kind(node: ast.Call, names: Names) -> str | None:
    match node.func:
        case ast.Name(id="isinstance" | "issubclass"):
            return "isinstance"
        case ast.Name(id="getattr") if len(node.args) >= 3:
            return "getattr_default"
        case ast.Name(id="hasattr"):
            return "hasattr"
        case ast.Name(id=name) if name in names.suppress:
            return "suppress"
        case ast.Attribute(attr="suppress", value=ast.Name(id=module)) if (
            module in names.contextlib
        ):
            return "suppress"
        case _:
            return None


def _is_type_call(node: ast.expr) -> bool:
    match node:
        case ast.Call(func=ast.Name(id="type"), args=[_]):
            return True
        case _:
            return False


def _is_field_optional(stmt: ast.stmt, aliases: frozenset[str]) -> bool:
    match stmt:
        case ast.AnnAssign(annotation=ast.Subscript(value=ast.Name(id="ClassVar"))):
            return False
        case ast.AnnAssign(target=ast.Name(), annotation=annotation):
            return _is_optional(annotation, aliases)
        case _:
            return False


class Defensive(ast.NodeVisitor):
    """Collects the line of each defensive pattern, by kind, each function's qualname, and each
    class with optional fields."""

    def __init__(self, names: Names | None = None) -> None:
        self.names = names or Names()
        self.hits: dict[str, list[int]] = {kind: [] for kind in KINDS}
        self.qualnames: dict[int, str] = {}  # def line (and decorator lines) -> qualname
        self.scope: list[str] = []
        self.in_context_manager = [False]  # per enclosing function (or class body)
        self.cleanup_try: list[int] = []  # try blocks in context managers, not counted
        self.optional_fields: list[tuple[int, str, int]] = []  # (line, class qualname, fields)

    def visit_Compare(self, node: ast.Compare) -> None:
        ops = (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)
        operands = [node.left, *node.comparators]
        if any(isinstance(op, (*ops, ast.In, ast.NotIn)) for op in node.ops) and any(
            _is_type_call(x) for x in operands
        ):
            self.hits["isinstance"].append(node.lineno)  # `type(x) is int`
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        kind = _call_kind(node, self.names)
        if kind:
            self.hits[kind].append(node.lineno)
        self.generic_visit(node)

    def visit_MatchClass(self, node: ast.MatchClass) -> None:
        """`case str(x)` is an isinstance check in other clothes."""
        if isinstance(node.cls, ast.Name) and node.cls.id in BUILTIN_TYPES:
            self.hits["isinstance"].append(node.lineno)
        self.generic_visit(node)

    def _try(self, node: ast.Try | ast.TryStar) -> None:
        """A try that only cleans up (finally, or excepts that re-raise) directly inside a context
        manager is how cleanup is written; its broad excepts don't swallow anything."""
        if not (self.in_context_manager[-1] and all(map(_reraises, node.handlers))):
            self.hits["try"].append(node.lineno)
            self.generic_visit(node)
            return
        self.cleanup_try.append(node.lineno)
        for handler in node.handlers:
            self.generic_visit(handler)
        for stmt in [*node.body, *node.orelse, *node.finalbody]:
            self.visit(stmt)

    visit_Try = _try
    visit_TryStar = _try

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad(node):
            self.hits["broad_except"].append(node.lineno)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Pydantic models are wire shapes (they flatten state on purpose): only domain classes
        count their optional fields."""
        aliases = self.names.optional_aliases
        fields = sum(_is_field_optional(stmt, aliases) for stmt in node.body)
        self.scope.append(node.name)
        if fields and node.name not in self.names.pydantic_models:
            self.optional_fields.append((node.lineno, ".".join(self.scope), fields))
        self.in_context_manager.append(False)
        self.generic_visit(node)
        self.in_context_manager.pop()
        self.scope.pop()

    def _is_context_manager(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        def marks(decorator: ast.expr) -> bool:
            match decorator:
                case ast.Name(id=name):
                    return name in self.names.context_managers
                case ast.Attribute(attr="contextmanager" | "asynccontextmanager", value=module):
                    return isinstance(module, ast.Name) and module.id in self.names.contextlib
                case _:
                    return False

        return node.name in CONTEXT_METHODS or any(map(marks, node.decorator_list))

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = ".".join([*self.scope, node.name])
        for line in [node.lineno, *(d.lineno for d in node.decorator_list)]:
            self.qualnames.setdefault(line, name)
        self.scope.append(node.name)
        self.in_context_manager.append(self._is_context_manager(node))
        self.generic_visit(node)
        self.in_context_manager.pop()
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function


@dataclass
class PyFile:
    path: str
    code_lines: int
    suppressions: list[tuple[int, str]]
    defensive: Defensive


def _docstrings(tree: ast.Module) -> set[tuple[int, int]]:
    """Where each docstring starts: docstrings are documentation, not code lines."""
    starts: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            match node.body:
                case [ast.Expr(value=ast.Constant(value=str()) as doc), *_]:
                    starts.add((doc.lineno, doc.col_offset))
                case _:
                    pass
    return starts


def analyse_python(path: str, source: str) -> PyFile:
    tree = ast.parse(source, filename=path)
    walker = Defensive(_names(tree))
    walker.visit(tree)
    docstrings = _docstrings(tree)
    code: set[int] = set()
    suppressions: list[tuple[int, str]] = []
    trivia = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            suppressions += [(tok.start[0], m[0]) for m in PY_SUPPRESSION.finditer(tok.string)]
        elif tok.type not in trivia and tok.start not in docstrings:
            code.update(range(tok.start[0], tok.end[0] + 1))
    return PyFile(path, len(code), suppressions, walker)


def layer_of(path: str, layers: dict[str, list[str]]) -> str:
    for layer, patterns in layers.items():
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return layer
    return "other"


def python_files() -> list[str]:
    paths = {p for pattern in PY_SCOPE for p in ROOT.glob(pattern)}
    return sorted(p.relative_to(ROOT).as_posix() for p in paths if "__pycache__" not in p.parts)


def cognitive_python(files: list[PyFile]) -> list[Finding]:
    found: list[Finding] = []
    for f in files:
        report = complexipy.file_complexity(str(ROOT / f.path), no_ignore=True)
        for fn in report.functions:
            name = f.defensive.qualnames.get(fn.line_start, fn.name.replace("::", "."))
            found.append(Finding(f.path, fn.line_start, name, fn.complexity))
    return found


def _ignore_comments(tokens: Iterator[str], reader: Any) -> Iterator[str]:
    """Lizard's comment step without its directives: `#lizard forgive*` and "GENERATED CODE"
    comments would drop functions from the count, so comments are only skipped here."""
    for token in tokens:
        comment = reader.get_comment_from_token(token)
        if comment is None:
            yield token
        else:
            yield from "\n" * comment.count("\n")


_LIZARD = lizard.FileAnalyzer(
    [
        lizard.preprocessing,
        _ignore_comments,
        lizard.line_counter,
        lizard.token_counter,
        lizard.condition_counter,
    ]
)


def cyclomatic_python(files: list[PyFile], limits: dict[str, Any]) -> list[Finding]:
    tool = limits["cyclomatic_python_tool"]
    if tool == "ruff":
        return cyclomatic_ruff(files, limits["cyclomatic"])
    if tool != "lizard":
        raise ValueError(
            f"limits.cyclomatic_python_tool: {tool!r}, not one of {PY_CYCLOMATIC_TOOLS}"
        )
    return cyclomatic_lizard(files)


def cyclomatic_lizard(files: list[PyFile]) -> list[Finding]:
    """Lizard's cyclomatic complexity, every function (its forgive comments don't count)."""
    found: list[Finding] = []
    for f in files:
        source = (ROOT / f.path).read_text(encoding="utf-8")
        for fn in _LIZARD.analyze_source_code(f.path, source).function_list:
            name = f.defensive.qualnames.get(fn.start_line, fn.name)
            found.append(Finding(f.path, fn.start_line, name, fn.cyclomatic_complexity))
    return found


def cyclomatic_ruff(files: list[PyFile], limit: int) -> list[Finding]:
    """ruff C901 (McCabe), the functions over `limit`: run with its own threshold and no project
    config, so `# noqa` can't hide one."""
    command = [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "C901"]
    command += ["--config", f"lint.mccabe.max-complexity={limit}", "--ignore-noqa", "--no-cache"]
    by_path = {(ROOT / f.path).resolve(): f for f in files}
    command += ["--exit-zero", "--output-format", "json", *map(str, by_path)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    found: list[Finding] = []
    for item in json.loads(result.stdout):
        f = by_path[Path(item["filename"]).resolve()]
        line = int(item["location"]["row"])
        score = re.search(r"\((\d+) > \d+\)", item["message"])
        if not score:
            raise RuntimeError(f"unexpected ruff C901 message: {item['message']}")
        name = f.defensive.qualnames.get(line, f"<line {line}>")
        found.append(Finding(f.path, line, name, int(score[1])))
    return found


def start_any(paths: list[str]) -> subprocess.Popen[str]:
    """mypy on the core files with every `Any` an error, beside the normal `just check` mypy (its
    own cache, so neither invalidates the other's). Imported modules' errors are not reported."""
    command = [sys.executable, "-m", "mypy", "--disallow-any-explicit", "--disallow-any-expr"]
    command += ["--follow-imports=silent", "--cache-dir=.mypy_cache/gates-any"]
    command += ["--no-error-summary", "--no-color-output"]
    command += [p.removeprefix("backend/") for p in paths]
    return subprocess.Popen(
        command, cwd=ROOT / "backend", stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )


def finish_any(run: subprocess.Popen[str]) -> dict[str, list[tuple[int, str]]]:
    """Per core file, its `Any` errors as (line, message)."""
    out, _ = run.communicate()
    if run.returncode not in (0, 1):
        raise RuntimeError(f"mypy exited with {run.returncode}")
    errors: dict[str, list[tuple[int, str]]] = {}
    for line in out.splitlines():
        if hit := MYPY_ERROR.match(line):
            path = "backend/" + hit["path"].replace("\\", "/")
            errors.setdefault(path, []).append((int(hit["line"]), hit["message"]))
    return errors


def fan_out() -> dict[str, set[str]]:
    """Per direct child of agentos (subpackage or module), the other subpackages it imports."""
    sys.path.insert(0, str(ROOT / "backend" / "server"))
    graph = grimp.build_graph("agentos", cache_dir=None)
    unit = {m: m.split(".")[1] for m in graph.modules if m != "agentos"}
    subpackages = {unit[m] for m in unit if m.count(".") >= 2}
    imports: dict[str, set[str]] = {}
    for module, source in unit.items():
        targets = {unit[t] for t in graph.find_modules_directly_imported_by(module) if t in unit}
        imports.setdefault(source, set()).update((targets & subpackages) - {source})
    return imports


# ---- Measuring TypeScript ---------------------------------------------------------------------


@dataclass
class TsRun:
    files: list[str]
    measures: subprocess.Popen[str]
    eslint: subprocess.Popen[str]


def ts_files() -> list[str]:
    paths = sorted({*WEB.glob("src/**/*.ts"), *WEB.glob("src/**/*.tsx")})
    rel = [p.relative_to(ROOT).as_posix() for p in paths]
    return [p for p in rel if not any(fnmatch.fnmatchcase(p, x) for x in TS_EXCLUDE)]


def start_ts(limits: dict[str, Any]) -> TsRun:
    """Start both node processes at once; they run while Python is measured."""
    node = shutil.which("node") or "node"
    files = ts_files()
    in_web = [p.removeprefix("web/") for p in files]
    script = Path(__file__).with_name("ts_measures.mjs")
    measures = [node, str(script), str(WEB), *in_web]
    eslint = [node, "node_modules/eslint/bin/eslint.js", "--config", "eslint.gates.config.js"]
    eslint += ["--no-inline-config", "--format", "json", "--rule"]
    eslint += [f"complexity: [error, {limits['cyclomatic']}]", "--rule"]
    eslint += [f"sonarjs/cognitive-complexity: [error, {limits['cognitive']}]", *in_web]

    def start(argv: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(argv, cwd=WEB, stdout=subprocess.PIPE, text=True, encoding="utf-8")

    return TsRun(files, start(measures), start(eslint))


def _innermost(functions: list[dict[str, Any]], line: int, column: int) -> str:
    at = (line, column)
    inside = [f for f in functions if tuple(f["start"]) <= at <= tuple(f["end"])]
    if not inside:
        return f"<line {line}>"
    return str(max(inside, key=lambda f: tuple(f["start"]))["name"])


def finish_ts(run: TsRun) -> tuple[dict[str, Any], list[Finding], list[Finding]]:
    """The per-file measures, then the cognitive (sonarjs) and cyclomatic (complexity) findings."""
    out, _ = run.measures.communicate()
    if run.measures.returncode != 0:
        raise RuntimeError("ts_measures.mjs failed")
    measures: dict[str, Any] = {f"web/{k}": v for k, v in json.loads(out).items()}
    report, _ = run.eslint.communicate()
    if run.eslint.returncode not in (0, 1):
        raise RuntimeError(f"eslint exited with {run.eslint.returncode}")
    rules = {"sonarjs/cognitive-complexity": "cognitive", "complexity": "cyclomatic"}
    found: dict[str, list[Finding]] = {"cognitive": [], "cyclomatic": []}
    for result in json.loads(report):
        path = Path(result["filePath"]).resolve().relative_to(ROOT).as_posix()
        for message in result["messages"]:
            if message.get("fatal"):
                raise RuntimeError(f"eslint could not parse {path}: {message['message']}")
            gate = rules.get(message.get("ruleId") or "")
            score = re.search(r"(?:complexity of|from) (\d+)", message["message"])
            if not gate or not score:
                continue
            line, column = int(message["line"]), int(message["column"])
            name = _innermost(measures[path]["functions"], line, column)
            found[gate].append(Finding(path, line, name, int(score[1])))
    return measures, found["cognitive"], found["cyclomatic"]


# ---- All measures -----------------------------------------------------------------------------


@dataclass
class Measures:
    files: int = 0
    cognitive: list[Finding] = field(default_factory=list[Finding])
    cyclomatic: list[Finding] = field(default_factory=list[Finding])
    file_lines: list[Finding] = field(default_factory=list[Finding])
    fan_out: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    layer: dict[str, str] = field(default_factory=dict[str, str])  # Python path -> layer
    # path -> kind -> lines
    defensive: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    cleanup_try: list[str] = field(default_factory=list[str])  # "path:line", not counted
    optional_fields: list[Finding] = field(default_factory=list[Finding])  # core classes
    any: list[Finding] = field(default_factory=list[Finding])  # core files
    # core path -> (line, message) per `Any` error
    any_errors: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    suppressions: list[str] = field(default_factory=list[str])  # "path:line  marker"
    raw_tags: dict[str, int] = field(default_factory=dict[str, int])  # feature -> count

    def scores(self, gate: str) -> dict[str, int]:
        """The current score per named id for an exception gate (the worst on a name clash)."""
        if gate == "fan_out":
            return {k: len(v) for k, v in self.fan_out.items() if k not in FAN_OUT_EXEMPT}
        out: dict[str, int] = {}
        for f in self.findings(gate):
            out[f.id] = max(out.get(f.id, 0), f.score)
        return out

    def findings(self, gate: str) -> list[Finding]:
        found: dict[str, list[Finding]] = {
            "cognitive": self.cognitive,
            "cyclomatic": self.cyclomatic,
            "file_lines": self.file_lines,
            "optional_fields": self.optional_fields,
            "any": self.any,
        }
        return found[gate]

    def counts(self, uncapped_kinds: dict[str, list[str]]) -> dict[str, dict[str, int]]:
        """Per file, its counts of the kinds its layer caps."""
        out: dict[str, dict[str, int]] = {}
        for path, kinds in self.defensive.items():
            uncapped = uncapped_kinds.get(self.layer[path], [])
            row = {k: len(lines) for k, lines in kinds.items() if lines and k not in uncapped}
            if row:
                out[path] = row
        return out


def measure(layers: dict[str, list[str]], limits: dict[str, Any]) -> Measures:
    ts = start_ts(limits)
    paths = python_files()
    m = Measures(layer={p: layer_of(p, layers) for p in paths})
    mypy = start_any([p for p in paths if m.layer[p] == "core"])
    py = [analyse_python(p, (ROOT / p).read_text(encoding="utf-8")) for p in paths]
    m.files = len(py) + len(ts.files)
    m.cognitive = cognitive_python(py)
    m.cyclomatic = cyclomatic_python(py, limits)
    m.fan_out = fan_out()
    for f in py:
        m.file_lines.append(Finding(f.path, 1, "file", f.code_lines))
        m.suppressions += [f"{f.path}:{line}  {marker}" for line, marker in f.suppressions]
        m.defensive[f.path] = f.defensive.hits
        m.cleanup_try += [f"{f.path}:{line}" for line in f.defensive.cleanup_try]
        if m.layer[f.path] == "core":
            m.optional_fields += [Finding(f.path, *c) for c in f.defensive.optional_fields]

    m.any_errors = finish_any(mypy)
    m.any = [Finding(p, errors[0][0], "Any", len(errors)) for p, errors in m.any_errors.items()]
    ts_measures, cognitive, cyclomatic = finish_ts(ts)
    m.cognitive += cognitive
    m.cyclomatic += cyclomatic
    for path, data in ts_measures.items():
        m.file_lines.append(Finding(path, 1, "file", data["code_lines"]))
        m.suppressions += [f"{path}:{line}  {marker}" for line, marker in data["suppressions"]]
        if path.startswith(FEATURES) and path.endswith(".tsx"):
            feature = path.removeprefix(FEATURES).split("/")[0]
            m.raw_tags[feature] = m.raw_tags.get(feature, 0) + len(data["raw_tags"])
    return m


# ---- Baseline ---------------------------------------------------------------------------------


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill what a baseline leaves out with the strictest value (a missing cap is 0)."""
    allowed = raw.get("allowed", {})
    exceptions = raw.get("exceptions", {})
    return {
        "version": raw.get("version", 1),
        "layers": raw.get("layers", DEFAULT_LAYERS),
        "limits": DEFAULT_LIMITS | raw.get("limits", {}),
        "allowed": {
            "uncapped": allowed.get("uncapped", {}),
            "defensive": allowed.get("defensive", {}),
            "suppressions": allowed.get("suppressions", 0),
            "raw_tags": allowed.get("raw_tags", {}),
        },
        "exceptions": {gate: exceptions.get(gate, {}) for gate in EXCEPTION_GATES},
        "raises": raw.get("raises", []),
    }


def flatten(baseline: dict[str, Any]) -> dict[str, int]:
    flat = {f"limits.{k}": v for k, v in baseline["limits"].items() if isinstance(v, int)}
    allowed = baseline["allowed"]
    for layer, uncapped in allowed["uncapped"].items():  # a kind uncapped counts as 1: a raise
        flat |= {f"allowed.uncapped.{layer}.{kind}": 1 for kind in uncapped}
    for path, kinds in allowed["defensive"].items():
        flat |= {f"allowed.defensive.{path}.{kind}": n for kind, n in kinds.items()}
    flat["allowed.suppressions"] = allowed["suppressions"]
    flat |= {f"allowed.raw_tags.{f}": n for f, n in allowed["raw_tags"].items()}
    for gate, named in baseline["exceptions"].items():
        flat |= {f"exceptions.{gate}.{name}": n for name, n in named.items()}
    return flat


def unexplained_raises(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What went up from `old` to `new` without a new `raises` entry (with a reason) naming it."""
    kept = len(old["raises"])
    if new["raises"][:kept] != old["raises"]:
        return ["raises: earlier entries were changed or removed"]
    named = [e.get("what", "") for e in new["raises"][kept:] if str(e.get("reason", "")).strip()]
    before = flatten(old)
    raised = [k for k, v in flatten(new).items() if v > before.get(k, 0)]
    if new["layers"] != old["layers"]:
        raised.append("layers")
    tool = "cyclomatic_python_tool"
    if new["limits"][tool] != old["limits"][tool]:
        raised.append(f"limits.{tool}")
    return [
        f"{key}: {before.get(key, 'none')} -> {flatten(new).get(key, 'changed')}"
        for key in sorted(raised)
        if not any(key == what or key.startswith(f"{what}.") for what in named)
    ]


def git_show(path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _ratchet_defensive(allowed: dict[str, Any], m: Measures) -> list[str]:
    """Lower each file's caps to its counts; a cap that reaches 0 (and then its file) goes."""
    changes: list[str] = []
    caps: dict[str, dict[str, int]] = allowed["defensive"]
    counts = m.counts(allowed["uncapped"])
    for path, kinds in list(caps.items()):
        for kind, cap in list(kinds.items()):
            now = counts.get(path, {}).get(kind, 0)
            if now < cap:
                kinds[kind] = now
                changes.append(f"allowed.defensive.{path}.{kind}: {cap} -> {now}")
            if not kinds[kind]:
                del kinds[kind]
        if not kinds:
            del caps[path]
    return changes


def ratchet(baseline: dict[str, Any], m: Measures) -> list[str]:
    """Lower every cap and named score the code now beats. Never raises one."""
    allowed = baseline["allowed"]
    changes = _ratchet_defensive(allowed, m)
    if len(m.suppressions) < allowed["suppressions"]:
        changes.append(f"allowed.suppressions: {allowed['suppressions']} -> {len(m.suppressions)}")
        allowed["suppressions"] = len(m.suppressions)
    for feature, cap in list(allowed["raw_tags"].items()):
        now = m.raw_tags.get(feature, 0)
        if now < cap:
            allowed["raw_tags"][feature] = now
            changes.append(f"allowed.raw_tags.{feature}: {cap} -> {now}")
    for gate in EXCEPTION_GATES:
        current, limit = m.scores(gate), baseline["limits"][gate]
        named: dict[str, int] = baseline["exceptions"][gate]
        for name, cap in list(named.items()):
            now = current.get(name, 0)
            if now <= limit:
                del named[name]
                changes.append(f"exceptions.{gate}.{name}: removed (now {now})")
            elif now < cap:
                named[name] = now
                changes.append(f"exceptions.{gate}.{name}: {cap} -> {now}")
    return changes


def initial_baseline(m: Measures) -> dict[str, Any]:
    limits = dict(DEFAULT_LIMITS)
    return {
        "version": 2,
        "layers": DEFAULT_LAYERS,
        "limits": limits,
        "allowed": {
            "uncapped": DEFAULT_UNCAPPED,
            "defensive": dict(sorted(m.counts(DEFAULT_UNCAPPED).items())),
            "suppressions": len(m.suppressions),
            "raw_tags": dict(sorted(m.raw_tags.items())),
        },
        "exceptions": {
            gate: {k: v for k, v in sorted(m.scores(gate).items()) if v > limits[gate]}
            for gate in EXCEPTION_GATES
        },
        "raises": [],
    }


def save(baseline: dict[str, Any]) -> None:
    BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")


# ---- Gates ------------------------------------------------------------------------------------


@dataclass
class Result:
    name: str
    ok: bool
    summary: str
    details: list[str] = field(default_factory=list[str])
    hint: str = ""  # how to fix a failure


def _over(name: str, found: list[Finding], baseline: dict[str, Any]) -> list[Finding]:
    """The worst finding per id that is over its named score or the limit, worst first."""
    limit: int = baseline["limits"][name]
    named: dict[str, int] = baseline["exceptions"][name]
    worst: dict[str, Finding] = {}
    for f in found:
        if f.id not in worst or f.score > worst[f.id].score:
            worst[f.id] = f
    over = [f for f in worst.values() if f.score > named.get(f.id, limit)]
    return sorted(over, key=lambda f: (-f.score, f.path, f.line))


def _named_gate(name: str, found: list[Finding], baseline: dict[str, Any], what: str) -> Result:
    limit: int = baseline["limits"][name]
    named: dict[str, int] = baseline["exceptions"][name]
    over = _over(name, found, baseline)
    details = [f"{f.path}:{f.line}  {f.name}  {f.score} > {named.get(f.id, limit)}" for f in over]
    top = max((f.score for f in found), default=0)
    summary = f"{len(over)} {what} over the limit {limit} ({len(named)} named), worst {top}"
    return Result(name, not over, summary, details, HINTS.get(name, ""))


def gate_any(m: Measures, baseline: dict[str, Any]) -> Result:
    """mypy's `Any` errors per core file, each offending file's errors listed."""
    result = _named_gate("any", m.any, baseline, "core files with Any errors")
    for f in _over("any", m.any, baseline):
        result.details += [f"    {f.path}:{line}  {msg}" for line, msg in m.any_errors[f.path]]
    return result


def gate_defensive(m: Measures, baseline: dict[str, Any]) -> Result:
    """Each file's capped kinds against its own caps; a per-layer table as the summary."""
    caps: dict[str, dict[str, int]] = baseline["allowed"]["defensive"]
    uncapped_kinds: dict[str, list[str]] = baseline["allowed"]["uncapped"]
    table = {layer: {kind: [0, 0] for kind in KINDS} for layer in [*baseline["layers"], "other"]}
    places: list[str] = []
    for path, kinds in sorted(m.defensive.items()):
        layer = m.layer[path]
        for kind, lines in kinds.items():
            cap = caps.get(path, {}).get(kind, 0)
            table[layer][kind][0] += len(lines)
            table[layer][kind][1] += cap
            if len(lines) > cap and kind not in uncapped_kinds.get(layer, []):
                fix = f"  fix: {HINTS[kind]}" if kind in HINTS else ""
                places += [f"{path}  {kind}  {len(lines)} > {cap}{fix}"]
                places += [f"    {path}:{line}" for line in lines]
    header = "".join(f"{kind[:10]:>11}" for kind in KINDS)
    details = [f"{'layer':<9}{header}"]
    for layer, kinds in table.items():
        uncapped = uncapped_kinds.get(layer, [])
        cells = (f"{n}/{'-' if kind in uncapped else cap}" for kind, (n, cap) in kinds.items())
        details.append(f"{layer:<9}" + "".join(cell.rjust(11) for cell in cells))
    failed = sum(not line.startswith(" ") for line in places)
    summary = (
        f"{failed} file/kind counts over their cap (per layer below: count/cap, - is uncapped); "
        f"{len(m.cleanup_try)} try blocks cleaning up in context managers not counted"
    )
    return Result("defensive", not failed, summary, details + places)


def gate_fan_out(m: Measures, baseline: dict[str, Any]) -> Result:
    limit: int = baseline["limits"]["fan_out"]
    named: dict[str, int] = baseline["exceptions"]["fan_out"]
    details: list[str] = []
    for unit, targets in sorted(m.fan_out.items(), key=lambda kv: -len(kv[1])):
        cap = named.get(unit, limit)
        if unit not in FAN_OUT_EXEMPT and len(targets) > cap:
            path = f"{AGENTOS}/{unit}"
            details.append(
                f"{path}  imports  {len(targets)} > {cap}  ({', '.join(sorted(targets))})"
            )
    counts = ", ".join(f"{u} {len(t)}" for u, t in sorted(m.fan_out.items()) if t)
    summary = f"{len(details)} over {limit} other subpackages (main exempt): {counts}"
    return Result("fan_out", not details, summary, details)


def gate_suppressions(m: Measures, baseline: dict[str, Any]) -> Result:
    cap: int = baseline["allowed"]["suppressions"]
    total = len(m.suppressions)
    details = m.suppressions if total > cap else []
    return Result("suppressions", total <= cap, f"{total} suppressions, cap {cap}", details)


def gate_raw_tags(m: Measures, baseline: dict[str, Any]) -> Result:
    caps: dict[str, int] = baseline["allowed"]["raw_tags"]
    details: list[str] = []
    failed = 0
    for feature, count in sorted(m.raw_tags.items(), key=lambda kv: -kv[1]):
        cap = caps.get(feature, 0)
        failed += count > cap
        mark = "  over" if count > cap else ""
        details.append(f"{FEATURES}{feature}  raw tags  {count}/{cap}{mark}")
    total = sum(m.raw_tags.values())
    summary = f"{failed} of {len(m.raw_tags)} features over their cap, {total} raw tags"
    return Result("raw_tags", not failed, summary, details)


def gate_baseline(baseline: dict[str, Any]) -> Result:
    committed = git_show(BASELINE.name)
    if committed is None:
        return Result("baseline", True, "no committed baseline to compare with")
    missing = unexplained_raises(normalise(json.loads(committed)), baseline)
    summary = f"{len(missing)} raised without a `raises` entry with a reason"
    return Result("baseline", not missing, summary, missing)


def run_gates(baseline: dict[str, Any], m: Measures) -> list[Result]:
    return [
        _named_gate("cognitive", m.cognitive, baseline, "functions"),
        _named_gate("cyclomatic", m.cyclomatic, baseline, "functions"),
        gate_defensive(m, baseline),
        _named_gate("optional_fields", m.optional_fields, baseline, "core domain classes"),
        gate_any(m, baseline),
        gate_fan_out(m, baseline),
        gate_suppressions(m, baseline),
        _named_gate("file_lines", m.file_lines, baseline, "files"),
        gate_raw_tags(m, baseline),
        gate_baseline(baseline),
    ]


def report(results: list[Result], m: Measures, seconds: float, can_ratchet: bool) -> Iterator[str]:
    yield f"gates: {m.files} files in {seconds:.1f} s"
    for r in results:
        yield f"  {'ok  ' if r.ok else 'FAIL'} {r.name:<15} {r.summary}"
        yield from (f"         {line}" for line in r.details)
        if not r.ok and r.hint:
            yield f"         fix: {r.hint}"
    if can_ratchet:
        yield "ratchet available: run `just ratchet`"
    failed = [r.name for r in results if not r.ok]
    yield f"FAIL: {', '.join(failed)}" if failed else "PASS"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ratchet", action="store_true", help="lower the baseline, never raise")
    group.add_argument("--init", action="store_true", help="write the first baseline")
    group.add_argument(
        "--rebaseline-cyclomatic",
        action="store_true",
        help="rewrite the named cyclomatic exceptions (after switching the Python tool)",
    )
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # a Windows pipe may not be UTF-8

    started = time.monotonic()
    exists = BASELINE.exists()
    baseline = normalise(json.loads(BASELINE.read_text(encoding="utf-8")) if exists else {})
    if args.init and exists:
        print(f"{BASELINE.name} exists already; edit it or run `just ratchet`")
        return 2
    if (args.ratchet or args.rebaseline_cyclomatic) and not exists:
        print(f"no {BASELINE.name} yet; write the first one with --init")
        return 2
    m = measure(baseline["layers"], baseline["limits"])

    if args.init:
        save(initial_baseline(m))
        print(f"wrote {BASELINE.name}")
        return 0
    if args.rebaseline_cyclomatic:
        limit = baseline["limits"]["cyclomatic"]
        scores = sorted(m.scores("cyclomatic").items())
        baseline["exceptions"]["cyclomatic"] = {k: v for k, v in scores if v > limit}
        save(baseline)
        print("rewrote exceptions.cyclomatic: add a `raises` entry for it, with the reason")
        return 0
    if args.ratchet:
        changes = ratchet(baseline, m)
        save(baseline)
        print("\n".join(changes) if changes else "nothing to ratchet")
        return 0
    results = run_gates(baseline, m)
    can_ratchet = exists and bool(ratchet(copy.deepcopy(baseline), m))
    for line in report(results, m, time.monotonic() - started, can_ratchet):
        print(line)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
