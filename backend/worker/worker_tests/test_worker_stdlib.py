"""Gate: the worker (worker.py and the agentos_worker package) imports only the Python standard
library and itself, absolutely and at module level and never dynamically, so a replaced zip is
never re-read mid-run. Its core (the bottom layer of pyproject's worker layers contract, the one
list every other copy is checked against) imports only a short stdlib allowlist and calls no I/O
or exec builtins."""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from agentos_worker import cli

WORKER_DIR = Path(__file__).resolve().parents[1]
BACKEND = WORKER_DIR.parent
ROOT = BACKEND.parent
ENTRY = WORKER_DIR / "worker.py"
SOURCES = [ENTRY, *sorted((WORKER_DIR / "agentos_worker").rglob("*.py"))]
ALLOWED = set(sys.stdlib_module_names) | {"__future__", "agentos_worker"}
# import-linter sees only top-level names, so `urllib.parse` passing there would let
# `urllib.request` in too: this list is exact.
CORE_STDLIB = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "re",
    "typing",
    "unicodedata",
    "urllib.parse",
}
# Ways to import or reach builtins by name at run time; only the entry script's package check
# (`importlib.util.find_spec`) is allowed.
DYNAMIC_MODULES = {"importlib", "builtins"}
DYNAMIC_NAMES = {"__import__", "import_module", "__builtins__"}
ENTRY_DYNAMIC = {"importlib.util", "importlib.util.find_spec"}
CORE_BANNED_BUILTINS = {"open", "print", "input", "exec", "eval", "compile", "breakpoint"}

PYPROJECT = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
CONTRACTS = {c["name"]: c for c in PYPROJECT["tool"]["importlinter"]["contracts"]}
CORE = {m.strip() for m in CONTRACTS["Worker: shell -> boundary -> core"]["layers"][-1].split(":")}
CORE_SOURCES = [WORKER_DIR / "agentos_worker" / f"{name}.py" for name in sorted(CORE)]


def _imports(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]


def _in_functions(tree: ast.AST) -> list[int]:
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    return sorted({node.lineno for fn in functions for node in _imports(fn)})


def _imported(tree: ast.AST) -> set[str]:
    """Every module imported, `from agentos_worker import x` as `agentos_worker.x`."""
    imported: set[str] = set()
    for node in _imports(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif node.module == "agentos_worker":
            imported.update(f"agentos_worker.{alias.name}" for alias in node.names)
        else:
            imported.add(node.module or "")
    return imported


def _dotted(node: ast.expr) -> str | None:
    """`a.b.c` for a chain of attributes on a name, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _dynamic(tree: ast.AST) -> set[str]:
    """What reaches import machinery or builtins by name: modules, names, attribute chains."""
    found = {m for m in _imported(tree) if m.split(".")[0] in DYNAMIC_MODULES}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in DYNAMIC_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in DYNAMIC_NAMES:
                found.add(node.attr)
            dotted = _dotted(node)
            if dotted is not None and dotted.split(".")[0] in DYNAMIC_MODULES:
                found.add(dotted)
    return found


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.relative_to(WORKER_DIR).as_posix())
def test_worker_imports_only_stdlib_at_module_level(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in _imports(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        else:
            assert node.level == 0, f"{source.name}:{node.lineno}: a relative import"
            assert node.module != "agentos_worker.tunables", "read tunables as `tunables.X`"
            imported.add(node.module or "")
    tops = {name.split(".")[0] for name in imported}
    assert tops - ALLOWED == set(), f"non-stdlib imports: {sorted(tops - ALLOWED)}"
    assert _in_functions(tree) == [], "imports inside a function"


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.relative_to(WORKER_DIR).as_posix())
def test_worker_never_imports_dynamically(source: Path) -> None:
    allowed = ENTRY_DYNAMIC if source == ENTRY else set()
    found = _dynamic(ast.parse(source.read_text(encoding="utf-8"))) - allowed
    assert found == set(), "import at module level by name; never through importlib or builtins"


@pytest.mark.parametrize("source", CORE_SOURCES, ids=lambda p: p.stem)
def test_worker_core_imports_only_its_allowlist(source: Path) -> None:
    allowed = CORE_STDLIB | {f"agentos_worker.{name}" for name in CORE}
    imported = _imported(ast.parse(source.read_text(encoding="utf-8")))
    assert imported - allowed == set(), "the worker's core is pure: do the I/O in a boundary module"


@pytest.mark.parametrize("source", CORE_SOURCES, ids=lambda p: p.stem)
def test_worker_core_calls_no_io_builtins(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert names & CORE_BANNED_BUILTINS == set(), "the worker's core is pure: no I/O or exec"


def _default_core_files() -> set[str]:
    """tools/gates/defaults.py's DEFAULT_LAYERS["core"] entries of the form f"{WORKER}/x.py"."""
    tree = ast.parse((ROOT / "tools" / "gates" / "defaults.py").read_text(encoding="utf-8"))
    (layers,) = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and _dotted(n.targets[0]) == "DEFAULT_LAYERS"
    ]
    assert isinstance(layers, ast.Dict)
    core = layers.values[[ast.literal_eval(k) for k in layers.keys if k].index("core")]
    assert isinstance(core, ast.List)
    files: set[str] = set()
    for item in core.elts:
        match item:
            case ast.JoinedStr(
                values=[
                    ast.FormattedValue(value=ast.Name(id="WORKER")),
                    ast.Constant(value=str(rest)),
                ]
            ):
                files.add(f"backend/worker/agentos_worker{rest}")
    return files


def test_every_copy_of_the_worker_core_list_is_the_same() -> None:
    files = {f"backend/worker/agentos_worker/{name}.py" for name in CORE}
    no_io: list[str] = CONTRACTS["Worker core: no I/O"]["source_modules"]
    assert {m.removeprefix("agentos_worker.") for m in no_io} == CORE
    ignores: dict[str, Any] = PYPROJECT["tool"]["ruff"]["lint"]["per-file-ignores"]
    (clock_glob,) = [k for k, v in ignores.items() if k.startswith("!") and "TID251" in v]
    assert set(re.findall(r"worker/agentos_worker/(\w+)\.py", clock_glob)) == CORE
    baseline = json.loads((ROOT / "quality-baseline.json").read_text(encoding="utf-8"))
    worker_core = {p for p in baseline["layers"]["core"] if p.startswith("backend/worker/")}
    assert worker_core == files
    assert _default_core_files() == files


def test_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOS_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        cli.parse_args(["--server", "http://127.0.0.1:8000"])


def test_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_TOKEN", "t" * 43)
    args = cli.parse_args(["--server", "http://127.0.0.1:8000"])
    assert args == cli.LiveMode("http://127.0.0.1:8000", "t" * 43, None, None)
