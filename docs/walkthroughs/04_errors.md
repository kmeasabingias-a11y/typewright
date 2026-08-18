# 04 — `src/typewright/errors.py`

## What this file is for

This file is TypeWright's set of **labeled rejection slips**.

When someone sends us a chunk of code to analyze, sometimes we can't do it — maybe
what they sent isn't valid Python, or they asked for a function that isn't there.
When that happens, the program needs to *stop* and say, clearly, "I can't continue,
and here's exactly why." This file defines those specific "here's why" messages.

Think of a passport desk. If something's wrong, the officer doesn't just say "no" —
they hand you a slip that names the problem: *photo missing*, *form unsigned*,
*wrong queue*. Each slip is a different, clearly-labeled reason. This file is our
drawer of those slips. Later, the front desk (the web layer, in `main.py`) reads the
slip and turns it into the right response for the caller.

---

## A mental model: exceptions, raising, and families

Three small ideas make this file obvious.

**1. An "exception" is the program's way of stopping and complaining.**
Normally code runs top to bottom. When something goes wrong, code can *raise* an
exception — think of it as throwing a flare. Everything stops and the flare flies
upward until some part of the program *catches* it and decides what to do. Python
has built-in flares (like `ValueError`), and you can also make your own — which is
exactly what we do here.

**2. "Raising" vs "catching."**
- *Raising* = "I give up here, something's wrong" (throwing the flare).
- *Catching* = "I saw the flare, I'll handle it" (deciding what the user sees).
This file only **defines** the flares. It doesn't raise them (the parser will) and
it doesn't catch them (the web layer will). It just describes them.

**3. A "family" of errors via a base class.**
We define one parent, `TypeWrightError`, and several children that inherit from it.
"Inherit" just means a child *is a kind of* the parent — a `CodeSyntaxError`
**is a** `TypeWrightError`, the same way a poodle is a dog. That family link is the
whole trick: later, the web layer can ask one simple question — "is this flare one
of *our* family?" — and answer it for all four children at once.

Why does that matter? Because it lets us split errors into two buckets:

- **"The caller's fault"** (bad input) → our family → answer with **400 Bad Request**.
- **"Our fault"** (an unexpected bug) → *not* our family → answer with
  **500 Internal Server Error**.

Keeping that line crisp is the entire point of the file.

Phase 2 adds one **named** member of the second bucket: `PipelineError`. It is deliberately
*outside* the `TypeWrightError` family (so it still maps to 500), but it carries the extra
detail of *which* analysis step failed — useful once there are several steps that could.

---

## The whole file

```python
"""Domain errors for TypeWright, mapped to HTTP status codes at the edge.

Phase 1 (DECISIONS.md D8): every error defined here is a *caller* mistake — bad
input — and maps to 400 Bad Request. Anything that is NOT a ``TypeWrightError``
is treated as an unexpected failure and surfaces as 500. Keeping that split in
the type system lets the HTTP layer pick a status code with a single
``isinstance`` check, instead of inspecting messages or guessing.
"""


class TypeWrightError(Exception):
    """Base class for every expected, caller-facing TypeWright error.

    Raise a subclass when the *caller* handed us something we can't analyze.
    The API layer turns any ``TypeWrightError`` into a 400 with the message as
    the response detail; everything else becomes a 500.
    """


class CodeSyntaxError(TypeWrightError):
    """The submitted ``code`` is not valid Python and could not be parsed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Submitted code is not valid Python: {detail}")


class NoFunctionError(TypeWrightError):
    """The code parsed, but holds no top-level function to analyze."""

    def __init__(self) -> None:
        super().__init__("No top-level function found in the submitted code.")


class FunctionNotFoundError(TypeWrightError):
    """A ``function_name`` was requested but no top-level function matches it."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No top-level function named {name!r} was found.")


class AmbiguousFunctionError(TypeWrightError):
    """Several top-level functions exist and no ``function_name`` was given."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        joined = ", ".join(repr(n) for n in names)
        super().__init__(
            f"Multiple top-level functions found ({joined}); "
            "specify `function_name` to choose one."
        )


class PipelineError(Exception):
    """An internal analysis stage failed — our fault, not the caller's (500).

    Deliberately NOT a ``TypeWrightError``: the caller's input was fine, but a
    stage of the pipeline (e.g. LLM property detection) could not complete.
    ``stage`` names the failing step so the API can include it in the 500
    response (§7.1: "500 ... Response includes failure stage"; D15).
    """

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"Pipeline stage {stage!r} failed: {detail}")
```

---

## Step-by-step

### The base class

```python
class TypeWrightError(Exception):
    """Base class for every expected, caller-facing TypeWright error."""
```

`class TypeWrightError(Exception)` means "make a new kind of error, based on
Python's built-in `Exception`." It has no code of its own — its job is to be the
**family name**. Every error below inherits from it, so the web layer can catch the
whole family in one line. It's "ours," and "ours" means "the caller's input was the
problem," which means "answer with 400."

### `CodeSyntaxError` — "that's not valid Python"

```python
class CodeSyntaxError(TypeWrightError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Submitted code is not valid Python: {detail}")
```

Raised when the text we were given doesn't even parse as Python (a missing colon, an
unclosed bracket). `__init__` is the setup that runs when the error is created:

- `self.detail = detail` keeps the precise reason (Python tells us things like
  *"unexpected EOF while parsing"*) so later code can use it as structured data, not
  just text.
- `super().__init__(...)` hands a friendly, complete sentence up to the built-in
  `Exception` machinery — that sentence is what gets shown. (`super()` means "the
  parent"; we're letting the normal exception parent store the message the usual way.)

### `NoFunctionError` — "there's nothing to analyze"

```python
class NoFunctionError(TypeWrightError):
    def __init__(self) -> None:
        super().__init__("No top-level function found in the submitted code.")
```

The code was valid Python, but it had no top-level function in it (maybe it was just
a couple of variable assignments). There's no extra detail to keep here, so this one
just sets a fixed message.

### `FunctionNotFoundError` — "you asked for a function that isn't here"

```python
class FunctionNotFoundError(TypeWrightError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No top-level function named {name!r} was found.")
```

The caller can optionally say *which* function they want (`function_name`). If they
name one we can't find, this fires. We store `self.name` (the thing they asked for)
and build the message with `{name!r}`.

> The `!r` is a small but nice detail: it prints the value's *repr*, which for text
> adds quotes. So a missing name `foo` shows as `'foo'` — making it unmistakable in
> the message, and showing up odd cases like an empty name `''` clearly.

### `AmbiguousFunctionError` — "which one did you mean?"

```python
class AmbiguousFunctionError(TypeWrightError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        joined = ", ".join(repr(n) for n in names)
        super().__init__(
            f"Multiple top-level functions found ({joined}); "
            "specify `function_name` to choose one."
        )
```

This is the opposite problem: the caller *didn't* say which function, but the code
has several. We can't guess, so we list what we found and ask them to pick.

- `self.names = names` keeps the full list.
- `", ".join(repr(n) for n in names)` turns `["a", "b"]` into the text `'a', 'b'` —
  each name quoted (that's the `repr(n)` again) and separated by commas — so the
  message reads naturally.

### `PipelineError` — "*our* machinery broke" (Phase 2)

```python
class PipelineError(Exception):
    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"Pipeline stage {stage!r} failed: {detail}")
```

This one is the odd cousin, and on purpose. Notice it inherits from plain `Exception`,
**not** from `TypeWrightError`. Every error above means *the caller* sent us something bad
(→ 400). `PipelineError` means the opposite: the caller's input was perfectly fine, but one
of *our own* steps — for example, asking the AI model to detect a function's properties —
failed. That's our fault, so it must map to **500**, and staying outside the
`TypeWrightError` family is exactly what keeps it in the 500 bucket.

It also carries one extra piece of information the 400 errors don't need:

- `self.stage` — the *name* of the step that failed (e.g. `"property_detection"`). The
  project brief says a 500 response should tell you which stage broke (§7.1), and decision
  **D15** is to carry that here so the web layer can include it.
- `self.detail` — the underlying reason, kept as data for logs and the response message.

So this single file now defines **both** sides of the 400/500 split: the caller-fault family
(`TypeWrightError` and its children) and the first named our-fault error (`PipelineError`).
The phases that actually run the analysis pipeline (Phase 2 onward) are what raise it.

---

### `SandboxTimeoutError` — "the tests ran out of time" (Phase 5)

```python
class SandboxTimeoutError(Exception):
    def __init__(self, budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        super().__init__(f"Test execution exceeded the {budget_seconds}s time budget.")
```

Phase 5 runs the generated tests in the Kestrel sandbox. Sometimes a test run simply takes too
long and the sandbox stops it. That's a **third** kind of situation, different from both families
above:

- It's **not the caller's fault** (their function and request were fine) — so not a 400.
- It's **not a broken step** (nothing crashed; the tests ran, they just didn't finish) — so not a
  500.

It's its own thing, and the project brief gives it its own status code: **504** ("the work
exceeded the time budget"). So we give it its own exception class — inheriting plain `Exception`,
like `PipelineError`, to stay out of the 400 family — and the web layer maps it to 504 (decision
**D42**). It carries `budget_seconds` so the message can tell the caller exactly which budget was
exceeded. Keeping it a distinct type means the edge can pick the status with one more `isinstance`
check, the same tidy pattern as the 400/500 split.

Why not just return a normal 200 with an empty bug list when a run times out? Because that would
read identically to "we analyzed it and found no bugs" — a false all-clear. For a tool whose whole
value is *trustworthy* bug reports, silently turning "we don't know" into "looks fine" is the worst
outcome, so a timeout gets its own honest signal.

---

## What could go wrong

### 1. Treating our bugs as the caller's mistake
If we'd reused a built-in like `ValueError` for these, we'd be in trouble: lots of
library code throws `ValueError` for reasons that are *our* bug. Catching `ValueError`
broadly at the web layer would slap a "400 — your input was bad" label on what is
really *our* 500. A private family (`TypeWrightError`) keeps the two apart: only the
errors we deliberately defined become 400s.

### 2. Leaking confusing internals to the caller
Each error here carries a sentence written *for the caller* — plain, about their
input. We never dump a raw stack trace or an internal detail they can't act on. The
caller should always be able to read the message and know what to fix.

### 3. Losing the structured reason
It would be tempting to skip `self.detail` / `self.name` / `self.names` and keep only
the message string. But a string is hard to act on later. Storing the actual values
means future code (logging, metrics, smarter responses) can use the data directly
instead of trying to parse it back out of a sentence.

### 4. Forgetting these are only *definitions*
This file never decides the HTTP status code and never catches anything. It only
describes the errors. The actual "→ 400" mapping lives in `main.py` (Unit 6). If you
go looking here for where 400 is returned, you won't find it — and that separation is
intentional: *what can go wrong* lives here, *what the user sees* lives at the edge.

---

## Summary

`errors.py` defines a small family of clearly-labeled errors for the things a caller
can get wrong: invalid Python, no function, a named function that's missing, or too
many functions with no name chosen. They all inherit from one base, `TypeWrightError`,
which lets the web layer answer the whole family with **400 Bad Request** in a single
check — while anything *not* in the family stays a **500**, our signal that the bug is
ours, not the caller's. Phase 2 adds `PipelineError` as the first *named* 500: when one of
our own analysis steps fails, it records which stage broke. The file only *describes* these
errors; raising them is the parser's (and pipeline's) job, and turning them into HTTP
responses is the web layer's.

---

## Change history

- **2026-06-10** — Created in Phase 1, Unit 4. Four caller-facing errors
  (`CodeSyntaxError`, `NoFunctionError`, `FunctionNotFoundError`,
  `AmbiguousFunctionError`) under one base `TypeWrightError`, per DECISIONS.md D8.
- **2026-06-12** — Phase 2 groundwork: added `PipelineError(stage, detail)`, an internal
  500-class failure deliberately kept *outside* the `TypeWrightError` family; it names the
  failing pipeline stage for the 500 response (§7.1, D15).
- **2026-06-13** — Phase 2 redirect (D23): the docstring's example stage name changed from
  `"contract_inference"` to `"property_detection"` to match the renamed pipeline step. No
  behavior change — `PipelineError` is unchanged; only the illustrative stage string moved.
- **2026-06-19** — Phase 5, Unit 3: added `SandboxTimeoutError(budget_seconds)` — a third,
  distinct error (plain `Exception`, outside `TypeWrightError`) that the web layer maps to **504**
  when a sandbox test run exceeds its time budget (D42). It's neither a caller fault (400) nor a
  broken stage (500), so it gets its own type + handler rather than a false-clean 200.
- **2026-06-25** — Phase 7: added `GitHubError(detail)` — a GitHub API call failed (auth, fetch, or
  comment). Deliberately NOT request-scoped (the worker runs off a queue, not behind the API), so unlike
  the others here it maps to **no** HTTP status; the worker catches it and logs/skips. D48.
- **2026-06-28** — Phase 9 (Unit 2): added `CostBudgetExceededError(spent_usd, limit_usd)` — an analysis
  crossed its LLM-cost ceiling. Like `SandboxTimeoutError`, it's a plain `Exception` (neither a 400 caller
  fault nor a 500 broken stage) with its own handler → **402 Payment Required** (body carries
  `spent_usd`/`limit_usd`). Raised from the cost meter at the LLM chokepoint; the best-effort fix step
  catches it and drops the fix rather than failing the request (D44/D52).
- **2026-06-28** — Phase 9 (Unit 3): added `RateLimitedError(retry_after)` — a client exceeded its request
  rate. Like the other non-request-family errors, a plain `Exception` with its own handler → **429 Too Many
  Requests** + a `Retry-After` header (and `retry_after` in the body). Raised by the route when the
  `RateLimiter` blocks a per-IP (`/v1/analyze`) or per-installation (webhook) check. D53.
- **2026-06-28** — Phase 9 (Unit 5): added `SandboxUnavailableError(retry_after, detail)` — Kestrel couldn't
  serve the run (unreachable, or a 429/502/503/504). A plain `Exception` with its own handler → **503 Service
  Unavailable** + `Retry-After`. Distinct from `PipelineError` (a logic bug → 500): this is an availability
  problem, so "retry later," not "we broke." The best-effort fix step catches it on its verify run and drops
  the fix instead. D55.
- **2026-06-30** — Phase 10 (D58): added `MonthlyBudgetExceededError(spent_usd, limit_usd, retry_after)` — the
  service's **global monthly** LLM-spend ceiling is used up. Like the other non-request-family errors, a plain
  `Exception` with its own handler → **503 Service Unavailable** + `Retry-After` (seconds to month rollover).
  Distinct from `CostBudgetExceededError` (one analysis's own budget → 402, the caller's concern): this is the
  *operator's* aggregate spend, so it's "temporarily unavailable," not "your request was too expensive." Raised
  by `MonthlyCostMeter.check()` at the LLM chokepoint; the best-effort fix step catches it and drops the fix.
- **2026-08-16** — Phase 10 (D62): `MonthlyBudgetExceededError` gained a keyword-only **`period`**
  (default `"Monthly"`, so every existing raise and its message are unchanged). The new `DailyCostMeter`
  raises the same error with `period="Daily"`, which flows into the message, the 503 body's `period` field,
  and a `Retry-After` measured to that period's rollover rather than the month's. One error class, one
  handler, two ceilings. The demo access gate (also D62) does **not** add an error type — it raises a plain
  `HTTPException(403)`, following the precedent set by the 404 on `GET /v1/runs/{id}` (D50).
