"""The single pass over each Python file: its defensive patterns by kind (with the context-manager
rule), each function's qualname, its classes with optional fields, code lines and suppressions."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass

from common import KINDS
from python_names import Names, is_optional_field, module_names

CONTEXT_METHODS = {"__enter__", "__exit__", "__aenter__", "__aexit__"}
PY_SUPPRESSION = re.compile(r"noqa|type:\s*ignore|pragma:\s*no cover")
INLINE_MYPY = re.compile(r"#\s*mypy:")  # inline mypy configuration, e.g. `# mypy: ignore-errors`
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
EXITS = (ast.Return, ast.Break, ast.Continue)
TYPE_ATTRS = {"__name__", "__qualname__"}


def _exits(stmts: list[ast.stmt]) -> bool:
    """Whether a return, break or continue appears anywhere in these statements."""
    return any(isinstance(node, EXITS) for stmt in stmts for node in ast.walk(stmt))


def _reraises(handler: ast.ExceptHandler) -> bool:
    """Ends in a bare `raise`, with no way out before it."""
    match handler.body[-1]:
        case ast.Raise(exc=None):
            return not _exits(handler.body)
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
        case func if names.typing_member(func) == "cast":
            return "cast"
        case _:
            return None


def _is_type_probe(node: ast.expr) -> bool:
    """`type(x)`, `x.__class__`, and their `__name__`: compared, they are isinstance checks."""
    match node:
        case ast.Call(func=ast.Name(id="type"), args=[_]):
            return True
        case ast.Attribute(attr="__class__"):
            return True
        case ast.Attribute(attr=attr, value=inner) if attr in TYPE_ATTRS:
            return _is_type_probe(inner)
        case _:
            return False


class Visitor(ast.NodeVisitor):
    """Collects the line of each defensive pattern, by kind, each function's qualname, each class
    with optional fields, and each `@no_type_check`."""

    def __init__(self, names: Names | None = None) -> None:
        self.names = names or Names()
        self.hits: dict[str, list[int]] = {kind: [] for kind in KINDS}
        self.qualnames: dict[int, str] = {}  # def line (and decorator lines) -> qualname
        self.scope: list[str] = []
        self.in_context_manager = [False]  # per enclosing function (or class body)
        self.cleanup_try: list[int] = []  # try blocks in context managers, not counted
        self.optional_fields: list[tuple[int, str, int]] = []  # (line, class qualname, fields)
        self.no_type_check: list[int] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        ops = (ast.Is, ast.IsNot, ast.Eq, ast.NotEq, ast.In, ast.NotIn)
        operands = [node.left, *node.comparators]
        if any(isinstance(op, ops) for op in node.ops) and any(map(_is_type_probe, operands)):
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

    def _shape_pattern(self, node: ast.MatchMapping | ast.MatchSequence) -> None:
        """`case {"k": v}` and `case [a, b]` check the value's type and shape."""
        self.hits["isinstance"].append(node.lineno)
        self.generic_visit(node)

    visit_MatchMapping = _shape_pattern
    visit_MatchSequence = _shape_pattern

    def _try(self, node: ast.Try | ast.TryStar) -> None:
        """A try that only cleans up directly inside a context manager is how cleanup is written,
        and its broad excepts don't swallow anything: no return, break or continue in `finally`,
        and every handler ends in a bare `raise` with no way out before it."""
        cleans_up = not _exits(node.finalbody) and all(map(_reraises, node.handlers))
        if not (self.in_context_manager[-1] and cleans_up):
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

    def _decorators(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            if self.names.typing_member(decorator) == "no_type_check":
                self.no_type_check.append(decorator.lineno)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Pydantic models are wire shapes (they flatten state on purpose): only domain classes
        count their optional fields."""
        self._decorators(node)
        fields = sum(is_optional_field(stmt, self.names) for stmt in node.body)
        self.scope.append(node.name)
        if fields and node.name not in self.names.pydantic_models:
            self.optional_fields.append((node.lineno, ".".join(self.scope), fields))
        self.in_context_manager.append(False)
        self.generic_visit(node)
        self.in_context_manager.pop()
        self.scope.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._decorators(node)
        name = ".".join([*self.scope, node.name])
        for line in [node.lineno, *(d.lineno for d in node.decorator_list)]:
            self.qualnames.setdefault(line, name)
        marked = any(map(self.names.is_context_manager, node.decorator_list))
        self.scope.append(node.name)
        self.in_context_manager.append(marked or node.name in CONTEXT_METHODS)
        self.generic_visit(node)
        self.in_context_manager.pop()
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function


@dataclass
class PyFile:
    """What one pass over a Python file found."""

    path: str
    code_lines: int
    suppressions: list[tuple[int, str]]  # (line, marker)
    hits: dict[str, list[int]]  # kind -> lines
    cleanup_try: list[int]
    optional_fields: list[tuple[int, str, int]]  # (line, class qualname, optional fields)
    qualnames: dict[int, str]

    def qualname(self, line: int, default: str) -> str:
        """The qualname of the function defined (or decorated) at `line`."""
        return self.qualnames.get(line, default)


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


def _comment_markers(text: str) -> list[str]:
    inline = ["mypy:"] if INLINE_MYPY.match(text) else []
    return inline + [m[0] for m in PY_SUPPRESSION.finditer(text)]


def analyse_python(path: str, source: str) -> PyFile:
    tree = ast.parse(source, filename=path)
    walker = Visitor(module_names(tree))
    walker.visit(tree)
    docstrings = _docstrings(tree)
    code: set[int] = set()
    suppressions = [(line, "no_type_check") for line in walker.no_type_check]
    trivia = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            suppressions += [(tok.start[0], marker) for marker in _comment_markers(tok.string)]
        elif tok.type not in trivia and tok.start not in docstrings:
            code.update(range(tok.start[0], tok.end[0] + 1))
    return PyFile(
        path,
        len(code),
        sorted(suppressions),
        walker.hits,
        walker.cleanup_try,
        walker.optional_fields,
        walker.qualnames,
    )
