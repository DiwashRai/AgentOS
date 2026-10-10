"""Gate: worker.py must import only the Python standard library."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import worker

WORKER = Path(__file__).resolve().parents[1] / "worker.py"


def test_worker_imports_only_stdlib() -> None:
    tree = ast.parse(WORKER.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "worker.py must not use relative imports"
            imported.add((node.module or "").split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    assert imported - allowed == set(), f"non-stdlib imports: {sorted(imported - allowed)}"


def test_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOS_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        worker.parse_args(["--server", "http://127.0.0.1:8000"])


def test_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_TOKEN", "t" * 43)
    args = worker.parse_args(["--server", "http://127.0.0.1:8000"])
    assert args.token == "t" * 43
