"""What one Python module calls the things the visitor looks for (contextlib and typing members,
resolved through its imports; its own aliases of optional types; its pydantic models), and whether
a class field is optional."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

PYDANTIC_BASES = {"BaseModel", "RootModel"}
TYPING_MODULES = {"typing", "typing_extensions"}
# typing members recognised by their bare name too (lenient: counting more is the safe side)
TYPING_BARE = {"Optional", "Union", "Annotated", "Final", "ClassVar", "cast", "no_type_check"}
CONTEXT_MANAGERS = {"contextmanager", "asynccontextmanager"}


@dataclass
class Names:
    """What one module calls the things the visitor looks for."""

    contextlib: set[str] = field(default_factory=set[str])  # local names of the module
    context_managers: set[str] = field(default_factory=set[str])  # imported from contextlib
    suppress: set[str] = field(default_factory=lambda: {"suppress"})
    typing: set[str] = field(default_factory=lambda: set(TYPING_MODULES))  # module names
    typing_members: dict[str, str] = field(default_factory=dict[str, str])  # local -> member
    optional_aliases: set[str] = field(default_factory=set[str])
    pydantic_models: frozenset[str] = frozenset()

    def typing_member(self, node: ast.expr) -> str | None:
        """The typing member a name or attribute stands for (`Opt` -> Optional, `t.Final`)."""
        match node:
            case ast.Name(id=name):
                return self.typing_members.get(name, name if name in TYPING_BARE else None)
            case ast.Attribute(attr=attr, value=ast.Name(id=module)) if module in self.typing:
                return attr
            case _:
                return None

    def is_context_manager(self, decorator: ast.expr) -> bool:
        """The real contextlib.(async)contextmanager, as this module imported it."""
        match decorator:
            case ast.Name(id=name):
                return name in self.context_managers
            case ast.Attribute(attr=attr, value=ast.Name(id=module)):
                return attr in CONTEXT_MANAGERS and module in self.contextlib
            case _:
                return False


def _parse_annotation(text: str) -> ast.expr | None:
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError:
        return None


def is_optional_type(node: ast.expr, names: Names) -> bool:
    """Whether a value of this type may be None itself: `X | None`, `Optional[X]`, `Union[..,
    None]`, a same-module alias of one, a string holding one, through `Annotated`/`Final`. What it
    contains doesn't count: `list[int | None]` is never None."""
    match node:
        case ast.Constant(value=None):
            return True
        case ast.Constant(value=str(text)):
            parsed = _parse_annotation(text)
            return parsed is not None and is_optional_type(parsed, names)
        case ast.Name(id=name):
            return name in names.optional_aliases
        case ast.BinOp(op=ast.BitOr(), left=left, right=right):
            return is_optional_type(left, names) or is_optional_type(right, names)
        case ast.Subscript(value=form, slice=inner):
            args = inner.elts if isinstance(inner, ast.Tuple) else [inner]
            match names.typing_member(form):
                case "Optional":
                    return True
                case "Union":
                    return any(is_optional_type(arg, names) for arg in args)
                case "Annotated" | "Final":
                    return is_optional_type(args[0], names)
                case _:
                    return False
        case _:
            return False


def _is_classvar(node: ast.expr, names: Names) -> bool:
    match node:
        case ast.Constant(value=str(text)):
            parsed = _parse_annotation(text)
            return parsed is not None and _is_classvar(parsed, names)
        case ast.Subscript(value=form):
            return names.typing_member(form) == "ClassVar"
        case _:
            return names.typing_member(node) == "ClassVar"


def is_optional_field(stmt: ast.stmt, names: Names) -> bool:
    match stmt:
        case ast.AnnAssign(target=ast.Name(), annotation=annotation):
            return not _is_classvar(annotation, names) and is_optional_type(annotation, names)
        case _:
            return False


def _alias(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """A module-level `Name = <type>`, `Name: TypeAlias = <type>` or `type Name = <type>`."""
    match stmt:
        case ast.Assign(targets=[ast.Name(id=name)], value=value) if isinstance(
            value, ast.BinOp | ast.Subscript | ast.Name
        ) or (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            return name, value
        case ast.AnnAssign(
            target=ast.Name(id=name),
            annotation=ast.Name(id="TypeAlias") | ast.Attribute(attr="TypeAlias"),
            value=ast.expr() as value,
        ):
            return name, value
        case ast.TypeAlias(name=ast.Name(id=name), value=value):
            return name, value
        case _:
            return None


def _add_optional_aliases(tree: ast.Module, names: Names) -> None:
    """The module's own aliases of an optional type (`MaybeRun = RunFact | None`), chains too."""
    candidates = [pair for stmt in tree.body if (pair := _alias(stmt))]
    known = names.optional_aliases
    while new := {n for n, v in candidates if n not in known and is_optional_type(v, names)}:
        known |= new


def _pydantic_models(tree: ast.Module) -> frozenset[str]:
    """The module's classes that subclass a pydantic model, directly or through each other."""
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    found: frozenset[str] = frozenset()

    def is_model(base: ast.expr) -> bool:
        match base:
            case ast.Name(id=name):
                return name in PYDANTIC_BASES or name in found
            case ast.Attribute(attr=name):
                return name in PYDANTIC_BASES
            case _:
                return False

    while new := {c.name for c in classes if c.name not in found and any(map(is_model, c.bases))}:
        found |= new
    return found


def _add_import(names: Names, node: ast.Import | ast.ImportFrom) -> None:
    pairs = [(a.name, a.asname or a.name) for a in node.names]
    match node:
        case ast.Import():
            names.contextlib |= {local for name, local in pairs if name == "contextlib"}
            names.typing |= {local for name, local in pairs if name in TYPING_MODULES}
        case ast.ImportFrom(module="contextlib"):
            names.suppress |= {local for name, local in pairs if name == "suppress"}
            names.context_managers |= {local for name, local in pairs if name in CONTEXT_MANAGERS}
        case ast.ImportFrom(module=module) if module in TYPING_MODULES:
            names.typing_members |= {local: name for name, local in pairs}
        case _:
            pass


def module_names(tree: ast.Module) -> Names:
    names = Names(pydantic_models=_pydantic_models(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            _add_import(names, node)
    _add_optional_aliases(tree, names)
    return names
