"""One pass over a Python file: its defensive patterns by kind (with the context-manager rule),
each function's qualname, its classes with optional fields, its code lines and suppressions."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass

from common import KINDS
from python_names import Names, is_field_optional, module_names

CONTEXT_METHODS = {"__enter__", "__exit__", "__aenter__", "__aexit__"}
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
        fields = sum(is_field_optional(stmt, aliases) for stmt in node.body)
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
    walker = Defensive(module_names(tree))
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
