"""The parser for a client's `--env-file` (`parse_env`, a python-dotenv subset); text that
breaks it is an EnvFileError.
"""

from __future__ import annotations

import re

_ENV_BLANK_RE = re.compile(r"[ \t]*(?:#[^\r\n]*)?(?:\r?\n|\Z)")
_ENV_ENTRY_RE = re.compile(
    r"[ \t]*(?:export[ \t]+)?(?P<key>(?!['\"])[^=#\s]+)[ \t]*"
    r"(?:=[ \t]*(?:'(?P<single>(?:\\'|[^'])*)'"
    r'|"(?P<double>(?:\\"|[^"])*)"'
    r"|(?P<bare>(?!['\"])[^\r\n]*)))?"
    r"[ \t]*(?:#[^\r\n]*)?(?:\r?\n|\Z)"
)
_SINGLE_ESCAPE_RE = re.compile(r"\\([\\'])")
_DOUBLE_ESCAPE_RE = re.compile(r"\\([\\'\"abfnrtv])")
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


class EnvFileError(ValueError):
    """An env file line outside the supported subset."""


# Named, not lambdas: `re.sub`'s overloads would type a lambda's match as Any.
def _single_escape(m: re.Match[str]) -> str:
    return m.group(1)


def _double_escape(m: re.Match[str]) -> str:
    return _ESCAPES.get(m.group(1), m.group(1))


def parse_env(text: str) -> dict[str, str | None]:
    """The python-dotenv subset a client's `--env-file` uses: `KEY=value` lines, `#`
    comments (whole-line, or after whitespace in an unquoted value), an optional `export `,
    single quotes (literal but `\\'` and `\\\\`), double quotes (with `\\n`-style escapes), quoted
    values across lines, and a bare `KEY` meaning "unset KEY" (None). `${VAR}` is not expanded."""
    values: dict[str, str | None] = {}
    pos, line = 0, 1
    while pos < len(text):
        blank = _ENV_BLANK_RE.match(text, pos)
        if blank is not None:
            pos, line = blank.end(), line + blank.group(0).count("\n")
            continue

        entry = _ENV_ENTRY_RE.match(text, pos)
        if entry is None:
            raise EnvFileError(f"line {line}: expected KEY=value, KEY or a comment")
        single, double, bare = entry.group("single", "double", "bare")
        if single is not None:
            value: str | None = _SINGLE_ESCAPE_RE.sub(_single_escape, single)
        elif double is not None:
            value = _DOUBLE_ESCAPE_RE.sub(_double_escape, double)
        elif bare is not None:
            value = re.sub(r"\s+#.*", "", bare).rstrip()
        else:
            value = None
        values[entry.group("key")] = value
        pos, line = entry.end(), line + entry.group(0).count("\n")
    return values
