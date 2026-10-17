"""The Python measures beyond the visitor: which files, cognitive complexity (complexipy),
cyclomatic complexity (lizard or ruff C901) and fan-out between agentos subpackages (grimp)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import complexipy
import grimp
import lizard  # type: ignore[import-untyped]  # lizard ships no type hints

from common import AGENTOS, ROOT, Finding
from defaults import PY_CYCLOMATIC_TOOLS
from pyfile import PyFile

PY_SCOPE = (f"{AGENTOS}/**/*.py", "backend/worker/worker.py")


def python_files() -> list[str]:
    paths = {p for pattern in PY_SCOPE for p in ROOT.glob(pattern)}
    return sorted(p.relative_to(ROOT).as_posix() for p in paths if "__pycache__" not in p.parts)


def cognitive_python(files: list[PyFile]) -> list[Finding]:
    found: list[Finding] = []
    for f in files:
        report = complexipy.file_complexity(str(ROOT / f.path), no_ignore=True)
        for fn in report.functions:
            name = f.qualname(fn.line_start, fn.name.replace("::", "."))
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
            name = f.qualname(fn.start_line, fn.name)
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
        name = f.qualname(line, f"<line {line}>")
        found.append(Finding(f.path, line, name, int(score[1])))
    return found


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
