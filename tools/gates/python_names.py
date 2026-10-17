"""What one Python module calls the things the defensive visitor looks for: its contextlib
aliases, its own aliases of optional types, its pydantic models. And whether an annotation is
optional."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

PYDANTIC_BASES = {"BaseModel", "RootModel"}


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _parse_annotation(text: str) -> ast.expr | None:
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError:
        return None


def _is_optional(annotation: ast.expr, aliases: frozenset[str] = frozenset()) -> bool:
    """Whether the annotation admits None anywhere: `X | None`, `Optional[X]`, `Union[.., None]`,
    a same-module alias of one (`aliases`), or a string annotation holding one."""
    match annotation:
        case ast.Constant(value=str(text)):
            parsed = _parse_annotation(text)
            return parsed is not None and _is_optional(parsed, aliases)
        case ast.Name(id=name):
            return name in aliases
        case ast.BinOp(op=ast.BitOr(), left=left, right=right) if _is_none(left) or _is_none(right):
            return True
        case ast.Subscript(value=ast.Name(id="Literal") | ast.Attribute(attr="Literal")):
            return False
        case ast.Subscript(value=ast.Name(id="Optional") | ast.Attribute(attr="Optional")):
            return True
        case ast.Subscript(
            value=ast.Name(id="Union") | ast.Attribute(attr="Union"), slice=ast.Tuple(elts=elts)
        ) if any(_is_none(e) for e in elts):
            return True
        case _:
            children = ast.iter_child_nodes(annotation)
            return any(_is_optional(c, aliases) for c in children if isinstance(c, ast.expr))


def _alias(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """A module-level `Name = <type>`, `Name: TypeAlias = <type>` or `type Name = <type>`."""
    match stmt:
        case ast.Assign(targets=[ast.Name(id=name)], value=value) if isinstance(
            value, ast.BinOp | ast.Subscript | ast.Name | ast.Constant
        ):
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


def _optional_aliases(tree: ast.Module) -> frozenset[str]:
    """The module's own aliases of an optional type (`MaybeRun = RunFact | None`), chains too."""
    candidates = [pair for stmt in tree.body if (pair := _alias(stmt))]
    found: frozenset[str] = frozenset()
    while new := {n for n, v in candidates if n not in found and _is_optional(v, found)}:
        found |= new
    return found


@dataclass
class Names:
    """What one module calls the things the visitor looks for."""

    contextlib: set[str] = field(default_factory=lambda: {"contextlib"})
    suppress: set[str] = field(default_factory=lambda: {"suppress"})
    context_managers: set[str] = field(
        default_factory=lambda: {"contextmanager", "asynccontextmanager"}
    )
    optional_aliases: frozenset[str] = frozenset()
    pydantic_models: frozenset[str] = frozenset()


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


def module_names(tree: ast.Module) -> Names:
    names = Names(optional_aliases=_optional_aliases(tree), pydantic_models=_pydantic_models(tree))
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                names.contextlib |= {
                    a.asname for a in aliases if a.name == "contextlib" and a.asname
                }
            case ast.ImportFrom(module="contextlib", names=aliases):
                for a in aliases:
                    local = a.asname or a.name
                    if a.name == "suppress":
                        names.suppress.add(local)
                    elif a.name in ("contextmanager", "asynccontextmanager"):
                        names.context_managers.add(local)
            case _:
                pass
    return names


def is_field_optional(stmt: ast.stmt, aliases: frozenset[str]) -> bool:
    match stmt:
        case ast.AnnAssign(annotation=ast.Subscript(value=ast.Name(id="ClassVar"))):
            return False
        case ast.AnnAssign(target=ast.Name(), annotation=annotation):
            return _is_optional(annotation, aliases)
        case _:
            return False
