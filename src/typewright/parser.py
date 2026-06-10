"""AST parser: turn a chunk of Python source into rich ``FunctionMetadata``.

This is the heart of Phase 1. It takes the ``code`` (and an optional
``function_name``) from a request, parses it with Python's own ``ast`` module,
selects the right top-level function, and reads everything later phases will
need: name, arguments (with type hints, defaults, and how each is passed),
return type, docstring, decorators, a reconstructed signature, and the original
source text.

Scope (DECISIONS.md D7): **top-level** ``def`` / ``async def`` only. Methods,
nested functions, and lambdas are intentionally not discovered; the GitHub diff
path (Phase 7) is when methods earn their extra edge cases. When the caller
hands us something we cannot analyze, we raise a ``TypeWrightError`` from
``errors.py`` — which the API layer (Unit 6) turns into a 400.
"""

import ast

from .errors import (
    AmbiguousFunctionError,
    CodeSyntaxError,
    FunctionNotFoundError,
    NoFunctionError,
)
from .models import Argument, ArgKind, FunctionMetadata

# Either flavour of `def`. Named once so the helpers can spell the type cleanly.
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def parse_function(code: str, function_name: str | None = None) -> FunctionMetadata:
    """Parse ``code`` and return metadata for one top-level function.

    When ``function_name`` is given, that function is returned, or
    ``FunctionNotFoundError`` is raised if no top-level function matches. When it
    is omitted, the source must contain exactly one top-level function: zero
    raises ``NoFunctionError`` and several raise ``AmbiguousFunctionError``.

    Raises ``CodeSyntaxError`` if ``code`` is not valid Python. All of these are
    subclasses of ``TypeWrightError`` (-> HTTP 400).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeSyntaxError(_format_syntax_error(exc)) from exc

    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    node = _select_function(functions, function_name)
    return _build_metadata(node, code)


def _select_function(
    functions: list[FunctionNode], function_name: str | None
) -> FunctionNode:
    """Choose which parsed function to analyze, or raise a caller-facing error."""
    if function_name is not None:
        for fn in functions:
            if fn.name == function_name:
                return fn
        raise FunctionNotFoundError(function_name)

    if not functions:
        raise NoFunctionError()
    if len(functions) > 1:
        raise AmbiguousFunctionError([fn.name for fn in functions])
    return functions[0]


def _build_metadata(node: FunctionNode, code: str) -> FunctionMetadata:
    """Read every field we expose now (or will need later) from one function node."""
    return FunctionMetadata(
        name=node.name,
        args=_extract_args(node.args),
        return_type=ast.unparse(node.returns) if node.returns is not None else None,
        docstring=ast.get_docstring(node),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=[ast.unparse(dec) for dec in node.decorator_list],
        signature=_build_signature(node),
        source=ast.get_source_segment(code, node) or "",
    )


def _build_signature(node: FunctionNode) -> str:
    """Reconstruct the signature text, e.g. ``(v: str) -> tuple[int, int, int]``."""
    signature = f"({ast.unparse(node.args)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _extract_args(args: ast.arguments) -> list[Argument]:
    """Flatten ``ast.arguments`` into our ordered list of ``Argument`` records.

    Order mirrors the written signature: positional-only, positional-or-keyword,
    ``*args``, keyword-only, ``**kwargs``.
    """
    result: list[Argument] = []

    # Positional-only (before `/`) then positional-or-keyword share one list of
    # defaults that aligns to the *tail* of the combined run.
    positional = args.posonlyargs + args.args
    first_default = len(positional) - len(args.defaults)
    posonly_count = len(args.posonlyargs)
    for index, arg in enumerate(positional):
        kind = (
            ArgKind.POSITIONAL_ONLY
            if index < posonly_count
            else ArgKind.POSITIONAL_OR_KEYWORD
        )
        default = None
        if index >= first_default:
            default = ast.unparse(args.defaults[index - first_default])
        result.append(_make_arg(arg, kind, default))

    if args.vararg is not None:  # *args
        result.append(_make_arg(args.vararg, ArgKind.VAR_POSITIONAL, None))

    # Keyword-only (after `*`); a slot's default is None when the arg is required.
    for arg, default_node in zip(args.kwonlyargs, args.kw_defaults):
        default = ast.unparse(default_node) if default_node is not None else None
        result.append(_make_arg(arg, ArgKind.KEYWORD_ONLY, default))

    if args.kwarg is not None:  # **kwargs
        result.append(_make_arg(args.kwarg, ArgKind.VAR_KEYWORD, None))

    return result


def _make_arg(arg: ast.arg, kind: ArgKind, default: str | None) -> Argument:
    """Build one ``Argument`` from an ``ast.arg`` plus its kind and default."""
    return Argument(
        name=arg.arg,
        type_hint=ast.unparse(arg.annotation) if arg.annotation is not None else None,
        default=default,
        kind=kind,
    )


def _format_syntax_error(exc: SyntaxError) -> str:
    """A compact, caller-useful description of why parsing failed."""
    if exc.lineno is not None:
        return f"{exc.msg} (line {exc.lineno})"
    return exc.msg or "invalid syntax"
