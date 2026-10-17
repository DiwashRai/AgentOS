"""The TypeScript measures: `ts_measures.mjs` (code lines, suppressions, raw tags, function names)
and the gates' ESLint run (cognitive and cyclomatic complexity), both started at once."""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import ROOT, Finding

WEB = ROOT / "web"
TS_EXCLUDE = ("web/src/api/schema.ts", "*.test.ts", "*.test.tsx", "web/src/domain/fixtures.ts")


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
