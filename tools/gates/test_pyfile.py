"""The single pass over a Python file: defensive patterns and their look-alikes, the
context-manager rule, optional fields, suppressions, code lines, and the complexity tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

import baseline as bl
import defaults
import python_measures
import ts_measures
from common import Finding
from pyfile import PyFile, analyse_python
from results import named_gate


def _analyse(source: str) -> PyFile:
    return analyse_python("x.py", textwrap.dedent(source))


def _hits(source: str) -> dict[str, int]:
    return {kind: len(lines) for kind, lines in _analyse(source).hits.items() if lines}


def test_defensive_patterns_and_their_look_alikes() -> None:
    source = """
        import contextlib
        from typing import Optional

        def f(a: int | None, b: Optional[str], c: int) -> list[int] | None:
            if a is None or b == None:  # not counted: syntax, the types carry it
                return getattr(c, "x", None)
            if hasattr(c, "y") or isinstance(c, int):
                getattr(c, "z")
            with contextlib.suppress(OSError):
                pass
            match c:
                case int():
                    pass
            try:
                pass
            except Exception:
                pass
    """
    assert _hits(source) == {
        "getattr_default": 1,
        "hasattr": 1,
        "isinstance": 2,
        "suppress": 1,
        "try": 1,
        "broad_except": 1,
    }


def test_type_probes_shape_patterns_casts_and_a_renamed_suppress_are_look_alikes() -> None:
    source = """
        import typing as t
        from contextlib import suppress as quietly
        from typing import cast as trust

        def f(run, other):
            if type(run) is int or type(other) == str:
                pass
            if run.__class__ is dict or type(run).__name__ == "Run":
                pass
            match run:
                case {"id": _}:
                    pass
                case [first, *_]:
                    pass
            with quietly(OSError):
                return trust(int, t.cast(str, other))
    """
    assert _hits(source) == {"isinstance": 6, "suppress": 1, "cast": 2}


def test_a_try_counts_unless_it_cleans_up_inside_a_real_context_manager() -> None:
    source = """
        import contextlib
        from contextlib import contextmanager as cm

        @cm
        def tx(conn):
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise

        @contextlib.asynccontextmanager
        async def session(conn):
            try:
                yield conn
            finally:
                conn.close()

        class Lock:
            def __exit__(self, *exc):
                try:
                    self.release()
                except OSError:
                    pass

        def close(conn):
            try:
                conn.close()
            finally:
                pass
    """
    assert _hits(source) == {"try": 2}  # __exit__ swallows an error; close is no context manager


def test_cleanup_that_can_swallow_or_a_fake_context_manager_still_counts() -> None:
    source = """
        from contextlib import contextmanager

        def asynccontextmanager(f):
            return f

        @contextmanager
        def returns_in_finally(conn):
            try:
                yield conn
            finally:
                return

        @contextmanager
        def returns_in_handler(conn, quiet):
            try:
                yield conn
            except BaseException:
                if quiet:
                    return
                raise

        @asynccontextmanager
        async def fake(conn):
            try:
                yield conn
            finally:
                conn.close()
    """
    assert _hits(source) == {"try": 3, "broad_except": 1}


def _optional_fields(source: str) -> dict[str, int]:
    return {name: n for _, name, n in _analyse(source).optional_fields}


def test_a_domain_class_with_two_optional_fields_is_flagged() -> None:
    source = """
        from typing import Optional
        from pydantic import BaseModel
        MaybeInt = int | None

        class Run:
            started_at: "datetime | None"
            owner: MaybeInt
            name: str

        class Ticket:
            owner: Optional[str] = None
            title: str

        class PatchModel(BaseModel):
            pass

        class TicketPatch(PatchModel):  # a wire shape: flat on purpose
            owner: str | None = None
            title: str | None = None
    """
    found = [Finding("x.py", *c) for c in _analyse(source).optional_fields]
    result = named_gate("optional_fields", found, bl.normalise({}), "classes")
    assert {f.name: f.score for f in found} == {"Run": 2, "Ticket": 1}
    assert [line.split()[1] for line in result.details] == ["Run"]


def test_only_a_fields_outermost_type_makes_it_optional() -> None:
    source = """
        import typing
        from collections.abc import Callable
        from typing import Annotated, ClassVar as CV, Final, Optional as Opt

        class Facts:
            renamed: Opt[int]
            qualified: typing.Optional[str]
            annotated: Annotated[int | None, "doc"]
            final: Final[str | None]
            union: typing.Union[int, None]
            items: list[int | None]
            table: dict[str, int | None]
            callback: Callable[[int | None], int]
            shared: typing.ClassVar[int | None]
            aliased_shared: CV[int | None]
    """
    assert _optional_fields(source) == {"Facts": 5}


def test_suppressions_include_inline_mypy_config_and_no_type_check() -> None:
    source = """
        # mypy: allow-any-expr
        import typing
        from typing import no_type_check as unchecked

        @unchecked
        def f(x):  # noqa: ANN001
            return x

        @typing.no_type_check
        class C:
            y = 1  # type: ignore[assignment]
    """
    markers = [marker for _, marker in _analyse(source).suppressions]
    assert sorted(markers) == ["mypy:", "no_type_check", "no_type_check", "noqa", "type: ignore"]


def test_docstrings_and_comments_are_not_code_lines() -> None:
    source = '"""Module.\n\nMore."""\n\n# a comment\ndef f():\n    """Doc."""\n    return 1\n'
    assert analyse_python("x.py", source).code_lines == 2


def _py_file(tmp_path: Path, source: str) -> PyFile:
    path = tmp_path / "f.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8", newline="\n")
    report = analyse_python(str(path), path.read_text(encoding="utf-8"))
    report.path = str(path)  # absolute, so ROOT / path is the file
    return report


def _cyclomatic(report: PyFile, tool: str, limit: int = 10) -> dict[str, int]:
    limits = defaults.DEFAULT_LIMITS | {"cyclomatic": limit, "cyclomatic_python_tool": tool}
    return {f.name: f.score for f in python_measures.cyclomatic_python([report], limits)}


def test_lizard_ignores_its_forgive_comments(tmp_path: Path) -> None:
    report = _py_file(
        tmp_path,
        """\
        def f(xs, flag):  # lizard forgives
            #lizard forgives
            ys = [x for x in xs if x] if flag and xs else []
            match flag:
                case 1:
                    return ys
                case _:
                    return None
        # GENERATED CODE
        def g(x):
            return x or 1
        """,
    )

    assert _cyclomatic(report, "lizard") == {"f": 7, "g": 2}  # f: 1 + for, if, ternary, and, cases


def test_both_python_tools_score_and_only_lizard_counts_boolean_operators(tmp_path: Path) -> None:
    report = _py_file(
        tmp_path,
        """\
        def h(a, b, c):  # noqa: C901
            if a and b or c:
                return 1
            return 0
        """,
    )

    assert _cyclomatic(report, "lizard") == {"h": 4}
    assert _cyclomatic(report, "ruff", limit=1) == {"h": 2}  # ruff lists only those over the limit


def _ts_measures(tmp_path: Path, source: str) -> dict[str, Any]:
    (tmp_path / "Page.tsx").write_text(textwrap.dedent(source), encoding="utf-8", newline="\n")
    rel = os.path.relpath(tmp_path / "Page.tsx", ts_measures.WEB)
    script = Path(ts_measures.__file__).with_name("ts_measures.mjs")
    out = subprocess.run(
        ["node", str(script), str(ts_measures.WEB), rel], capture_output=True, text=True, check=True
    )
    measured: dict[str, Any] = next(iter(json.loads(out.stdout).values()))
    return measured


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_ts_measures_reads_comments_tags_and_names(tmp_path: Path) -> None:
    measured = _ts_measures(
        tmp_path,
        """\
        // eslint-disable-next-line no-console
        export function Page() {
          return (
            <div>
              // text, not a comment
              <Card onClick={() => 1} />
              <span />
            </div>
          );
        }
        """,
    )

    assert measured["code_lines"] == 9
    assert [marker for _, marker in measured["suppressions"]] == ["eslint-disable-next-line"]
    assert measured["raw_tags"] == [4, 7]
    assert [f["name"] for f in measured["functions"]] == ["Page", "Page.onClick(callback)"]
