"""The SPEC §6 path rules agree with the server's: both suites run server/tests/fixtures/
path_cases.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentos_worker import paths

FIXTURE = Path(__file__).resolve().parents[2] / "server" / "tests" / "fixtures" / "path_cases.json"


def _cases() -> list[Any]:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [pytest.param(c["path"], c["ok"], id=repr(c["path"])[:40]) for c in cases]


@pytest.mark.parametrize(("path", "ok"), _cases())
def test_path_rules_match_the_server(path: str, ok: bool) -> None:
    assert (paths.path_error(path) is None) is ok
