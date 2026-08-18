# 05 — `src/typewright/parser.py`

## What this file is for

This file is TypeWright's **reader**. You hand it a piece of Python source — the
text of a function — and it reads that text carefully and tells you everything
about the function: its name, what arguments it takes, what types those arguments
are, whether it has a default value, what it returns, its docstring, and so on.

Think of a librarian who takes a book and fills out an index card for it: title,
author, number of pages, subject. The book itself doesn't change; the librarian
just *reads* it and writes down the facts in a tidy, structured form. `parser.py`
is that librarian, and the "index card" it fills out is the `FunctionMetadata`
object we met in the models walkthrough (03).

This is the **heart of Phase 1**. Everything earlier (config, logging, models,
errors) was setup. This is the first file that does the actual job: *understand a
function*. Later phases (asking an AI to describe the function, generating tests)
all build on the facts this file extracts.

---

## A mental model: what is an "AST"?

When you read code, you see text — characters, spaces, line breaks. But that text
has a *structure*: this is a function, it has these arguments, this argument has
that type. Python has a built-in tool that turns the flat text into that structure,
called an **Abstract Syntax Tree**, or **AST**.

"Tree" because the structure branches, like a family tree: a *module* contains a
*function*, the function contains a list of *arguments*, an argument has a *name*
and maybe a *type*. "Abstract" because it throws away things that don't matter to
the meaning (like exactly how many blank lines you used) and keeps the essence.

The key point: **we don't parse Python by hand.** Writing code to read text
character-by-character and figure out "is this a function?" would be enormous and
bug-prone. Python already has a flawless parser built in — the same one the
interpreter uses to run your code. We just ask it (`ast.parse`) to hand us the
tree, then we walk the branches we care about.

Two standard-library helpers do most of the work for us:

- **`ast.parse(text)`** — turn source text into the tree.
- **`ast.unparse(node)`** — the reverse: turn a piece of the tree back into text.
  We use this constantly to get clean strings for types, defaults, and the
  signature, without formatting anything by hand.

---

## The whole file

```python
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
```

---

## Step-by-step

### The imports and the `FunctionNode` alias

```python
import ast

from .errors import (...)
from .models import Argument, ArgKind, FunctionMetadata

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
```

We pull in `ast` (Python's parser), the four caller-facing errors from Unit 4, and
the data shapes from Unit 3. The last line is a small convenience: a regular
function and an `async` function are two different node types in the tree
(`FunctionDef` and `AsyncFunctionDef`). Writing `FunctionNode` once lets the
helpers below say "this is either flavour of `def`" without repeating both names.

### `parse_function` — the four-line story

```python
def parse_function(code: str, function_name: str | None = None) -> FunctionMetadata:
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
```

This is the whole job in four beats:

1. **Parse.** `ast.parse(code)` builds the tree. If the text isn't valid Python it
   throws a `SyntaxError` — we catch it and re-raise it as *our* `CodeSyntaxError`,
   so the rest of the program only ever deals with our family of errors. (`from exc`
   keeps the original error attached underneath, which is handy when debugging.)
2. **Find.** `tree.body` is the list of top-level things in the file. We keep only
   the ones that are functions. Because we look at `tree.body` directly — not deep
   inside the tree — we naturally get **only top-level functions** (D7). A function
   defined *inside* another function, or a method inside a class, lives in some
   other node's body, not here, so it's simply not collected.
3. **Select.** Hand the list to `_select_function` to pick the one to analyze.
4. **Build.** Hand the chosen function to `_build_metadata` to fill out the index card.

### `_select_function` — pick exactly one, or complain clearly

```python
def _select_function(functions, function_name):
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
```

There are two cases:

- **The caller named a function.** We look for it. Found → return it. Not found →
  `FunctionNotFoundError` (the Unit 4 slip that says "you asked for one that isn't here").
- **The caller didn't name one.** Then there must be exactly one, or we can't know
  which they meant. Zero functions → `NoFunctionError`. Several → `AmbiguousFunctionError`,
  which lists the names so the caller can pick. Exactly one → return it.

Notice this function never builds anything; it only *chooses*. Keeping "which one?"
separate from "read its details" makes both halves easy to follow.

### `_build_metadata` — fill out the index card

```python
def _build_metadata(node, code):
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
```

Each line reads one fact off the tree:

- **`name`** — the function's name, sitting right on the node.
- **`args`** — the argument list, handled by `_extract_args` (the trickiest part; see below).
- **`return_type`** — the bit after `->`. If there's no return annotation, `node.returns`
  is `None` and we store `None`. Otherwise `ast.unparse` turns it back into text like
  `"tuple[int, int, int]"`.
- **`docstring`** — `ast.get_docstring` is a ready-made helper that pulls the docstring
  (or `None`).
- **`is_async`** — is this the `async def` flavour? A simple type check answers it.
- **`decorators`** — the `@something` lines above the function, each turned back into
  text.
- **`signature`** — the human-readable `(args) -> return` string, built by `_build_signature`.
- **`source`** — the original text of the function, recovered with `ast.get_source_segment`.
  (`or ""` is a safety net: in rare cases that helper can return `None`, and our model
  wants a string.)

### `_build_signature` — rebuild the `(...) -> ...` string

```python
def _build_signature(node):
    signature = f"({ast.unparse(node.args)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature
```

Rather than stitch the signature together argument by argument, we let `ast.unparse`
render the *whole* argument block in one go — it already knows how to place the `/`,
the `*`, defaults, and type hints correctly. Then we wrap it in parentheses and, if
there's a return type, tack on `-> ...`. The result for our example function is
`(v: str) -> tuple[int, int, int]` — exactly what a human would write.

### `_extract_args` — the one genuinely fiddly part

```python
def _extract_args(args):
    result = []

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

    for arg, default_node in zip(args.kwonlyargs, args.kw_defaults):
        default = ast.unparse(default_node) if default_node is not None else None
        result.append(_make_arg(arg, ArgKind.KEYWORD_ONLY, default))

    if args.kwarg is not None:  # **kwargs
        result.append(_make_arg(args.kwarg, ArgKind.VAR_KEYWORD, None))

    return result
```

Python arguments come in five flavours, and the tree stores each flavour in a
different place. This function visits them in the order they'd be *written*, so our
list matches the signature left-to-right.

The subtle bit is **defaults**. Python keeps the defaults for the ordinary
positional arguments in a single list (`args.defaults`) that lines up with the
**end** of the argument run. Example: `def f(a, b, c=1, d=2)` has four arguments but
only two defaults — and those two belong to the *last* two arguments. So we compute
`first_default = (number of positional args) - (number of defaults)`: any argument
at or past that index has a default, and we reach into `args.defaults` at the matching
offset. Get this wrong and defaults attach to the wrong arguments — which is exactly
why it has a careful, named calculation instead of a clever one-liner.

The keyword-only arguments (after a `*`) are friendlier: they come with a parallel
`kw_defaults` list where each slot is either the default or `None` meaning "required",
so we can just walk the two lists together with `zip`.

`*args` and `**kwargs` are single optional slots (`args.vararg`, `args.kwarg`), each
recorded with its own kind. None of these ever have a default.

### `_make_arg` and `_format_syntax_error` — two small helpers

```python
def _make_arg(arg, kind, default):
    return Argument(
        name=arg.arg,
        type_hint=ast.unparse(arg.annotation) if arg.annotation is not None else None,
        default=default,
        kind=kind,
    )
```

`_make_arg` builds one `Argument` record: its name, its type hint (unparsed to text,
or `None` if it has none), its default, and which of the five kinds it is. Every
branch above funnels through this one builder, so the `Argument` shape is constructed
in exactly one place.

```python
def _format_syntax_error(exc):
    if exc.lineno is not None:
        return f"{exc.msg} (line {exc.lineno})"
    return exc.msg or "invalid syntax"
```

When parsing fails, Python's `SyntaxError` carries a message and (usually) a line
number. This turns that into one compact sentence like `invalid syntax (line 1)`,
which becomes the detail in the `CodeSyntaxError` the caller sees.

---

## What could go wrong

### 1. Hand-rolling a Python parser
The biggest mistake would be reading the code text ourselves to find functions and
arguments. That path is enormous and never quite correct. Leaning entirely on
`ast.parse` / `ast.unparse` means we get Python's own, exactly-correct understanding
for free — including every odd corner of the grammar.

### 2. Misaligning defaults
The positional defaults list lines up with the *end* of the arguments, not the start.
A naive `zip(args.args, args.defaults)` would pair the defaults with the *first*
arguments and silently mislabel which parameters are optional. The explicit
`first_default` offset is there precisely to avoid that trap — and it's covered by a
test that mixes required and defaulted arguments.

### 3. Letting a raw `SyntaxError` escape
If we didn't catch `SyntaxError`, a caller pasting broken code would trigger an
*unexpected* error and get a 500 — implying *we* broke, when really *their input*
was malformed. Converting it to `CodeSyntaxError` (a member of our error family)
makes it an honest 400, the caller's-fault bucket.

### 4. Surprise: `source` doesn't include decorator lines
`ast.get_source_segment` returns the function starting from its `def`/`async def`
line — it does **not** include the `@decorator` lines above it. That's intentional
and lossless here: the decorators are captured separately in the `decorators` field,
so no information is lost; the `source` string just begins at `def`. Worth remembering
when a later phase wants the *fully* decorated text.

### 5. Quietly analyzing the wrong function
If a file has several functions and the caller didn't say which, guessing would be
worse than refusing — we might test a function they didn't mean. `_select_function`
refuses with `AmbiguousFunctionError` and lists the choices, so the caller stays in
control.

---

## Summary

`parser.py` is TypeWright's reader. It uses Python's own `ast` module to turn source
text into a structured tree, selects exactly one top-level function (refusing clearly
when the request is ambiguous, missing, or unparseable), and reads off every fact we
need into a `FunctionMetadata` record. `ast.unparse` lets us recover clean text for
types, defaults, decorators, and the full signature without formatting anything by
hand. The only genuinely delicate part is aligning positional defaults to the end of
the argument run — handled by an explicit offset and pinned down by tests. Everything
this file produces is the foundation the later, AI-driven phases build on.

---

## Change history

- **2026-06-10** — Created in Phase 1, Unit 5. `parse_function` plus private helpers
  for selection, signature reconstruction, and argument extraction (all five argument
  kinds, with correct default alignment). Top-level `def`/`async def` only (D7);
  caller mistakes raise the Unit 4 error family. Verified against a battery of cases
  before integration.
- **2026-06-30** — Phase 10 (D61): added `_collect_imports`. `parse_function` now also returns the pasted
  code's **module-level import lines** — carried into the generated test file by testgen, because the parser
  keeps only the function body in `source`, so a top-level `import re` was previously dropped → phantom crash —
  and the set of **all imported top-level module names** (module-level + inside the function), used downstream
  to detect dependencies the sandbox can't satisfy. Relative imports (`from . import x`) are skipped. These land
  on `FunctionMetadata.module_imports` / `.imported_modules`.
