"""Gate: the worker (worker.py and the agentos_worker package) imports only the Python standard
library and itself, absolutely and at module level, so a replaced zip is never re-read mid-run."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import worker

WORKER_DIR = Path(__file__).resolve().parents[1]
SOURCES = [WORKER_DIR / "worker.py", *sorted((WORKER_DIR / "agentos_worker").rglob("*.py"))]
ALLOWED = set(sys.stdlib_module_names) | {"__future__", "agentos_worker"}


def _imports(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]


def _in_functions(tree: ast.AST) -> list[int]:
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    return sorted({node.lineno for fn in functions for node in _imports(fn)})


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


def test_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOS_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        worker.parse_args(["--server", "http://127.0.0.1:8000"])


def test_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_TOKEN", "t" * 43)
    args = worker.parse_args(["--server", "http://127.0.0.1:8000"])
    assert args == worker.LiveMode("http://127.0.0.1:8000", "t" * 43, None, None)
