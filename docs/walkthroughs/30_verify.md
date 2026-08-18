# 30 — `src/typewright/verify.py`

## What this file is for

TypeWright finds bugs by checking a function against properties it *thinks* the
function should obey. The catch: it sometimes invents a property the function never
actually promised, or feeds it an input the function was never meant to handle — and
then "reports a bug" that isn't one. We measured this: across 49 real library
functions, it flagged 15 candidates and only **3** were real.

**This file is the second opinion.** After a bug is found, it asks a separate,
deliberately skeptical reviewer one job: *"Is this actually a bug, or a false alarm?"*
It's the difference between an intern who shouts "BUG!" at everything and a senior
engineer who looks again and says "no, the function never promised that."

A useful analogy: the rest of the pipeline is a smoke detector. This file is the
firefighter who walks in before you call everyone — most alarms are burnt toast, and
their job is to tell the real fire from the toast *without* disabling the detector.

## A mental model

Four ideas make the file obvious:

1. **It only ever judges — it never changes what was found.** It runs *after* the
   bugs are detected. It can't add a bug, remove a bug, or change which tests ran. It
   just attaches a verdict. So turning it on can't make TypeWright miss a real bug it
   would otherwise have caught — the worst it can do is mislabel one (and we keep
   mislabelled ones visible anyway). This "don't touch detection" choice is what keeps
   *recall* safe.

2. **Two questions, not one.** The 12 false positives we saw came in exactly two
   flavours, so the judge asks exactly two things:
   - **Is the property contractual?** Did the function actually *promise* this?
     (`uppercase('ß')` returns `'SS'`, length 2 — but `uppercase` never promised to
     keep the length the same. Not a real bug.)
   - **Is the input in-domain?** Is this an input the function is *meant* to accept?
     (`flatten_iter(0)` raises — but `0` isn't an iterable; raising is fine. Not a real
     bug.)
   A finding is real only when **both** answers are yes.

3. **This is NOT the "confidence" idea we already rejected.** An earlier instinct
   (decision D57) was to hide low-confidence findings. It doesn't work: the *false*
   property `slugify(s)==slugify(s.upper())` and the *true* property
   `absolute(x)==absolute(-x)` both came back at 0.90 confidence. "How sure are you it
   holds?" can't tell them apart. "Is it *contractual*?" can — `absolute` documents the
   symmetry; `slugify` never claimed it. Different question, separable answer.

4. **It's best-effort, like the fix step.** If the reviewer call fails, the bug is
   just left *unverified* — the analysis still succeeds. A second opinion going missing
   must never turn a perfectly good bug report into an error.

## The whole file

```python
from __future__ import annotations

import instructor

from .config import Settings, get_settings
from .llm import build_client, complete
from .models import Bug, BugVerdict, DetectedProperty, FunctionMetadata

_STAGE = "bug_verification"

_SYSTEM_PROMPT = (
    "You are a STRICT code reviewer auditing an automated bug report. ... "
    "Judge exactly two things, independently:\n"
    "1. property_is_contractual — is the violated property genuinely guaranteed by THIS "
    "function's contract (its docstring, name, and signature)? ...\n"
    "2. input_in_domain — is the failing input one the function is actually meant to accept? ...\n"
    "Default to NOT-a-bug when unsure. ..."
)

_FEW_SHOT = (
    "A) uppercase length on 'ß' => not contractual (over-inferred). NOT a bug.\n"
    "B) flatten_iter(0) raises => not in-domain. NOT a bug.\n"
    "C) to_text length cap broken => contractual + in-domain. REAL bug.\n"
    "D) absolute(x)==absolute(-x) => contractual + in-domain. REAL bug.\n"
)


def _client() -> instructor.Instructor:
    return build_client()


def verify_bug(meta, detected, bug, settings=None, *, model_tier=None) -> BugVerdict:
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if detected is not None:
        property_desc = f"{detected.property_class.value}: {detected.relation}\n(rationale: {detected.rationale})"
    else:
        property_desc = bug.violated_property

    user_prompt = (
        "Audit this bug report.\n\n"
        f"Function under test:\n{meta.source}\n\n"
        f"Violated property:\n{property_desc}\n\n"
        f"Failing input: {bug.failing_input or '(none captured)'}\n"
        f"Observed failure: {bug.error} (classified as {bug.severity.value})\n\n"
        "Is the property genuinely contractual, and is the failing input within its domain?"
    )

    return complete(
        _client, stage=_STAGE, settings=settings, model=model,
        response_model=BugVerdict,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
            {"role": "user", "content": user_prompt},
        ],
    )
```

(The real prompts are longer and spell out the two questions and the four examples in
full — trimmed here for readability.)

## Step-by-step

**`BugVerdict` (in `models.py`, walkthrough 03)** is what the judge returns: the two
booleans `property_is_contractual` and `input_in_domain`, plus a short `reasoning`.
`is_real` is a derived property — it's simply `property_is_contractual and
input_in_domain`, computed in code so the label can never disagree with the two
judgments it's built from. We deliberately *don't* ask the model to also fill in
`is_real`; we compute it.

**`_client()`** is the same one-line test seam every LLM step has (walkthrough 10):
real code builds the Instructor client; tests monkeypatch this to return a canned
verdict, so the test suite never makes a real call.

**`verify_bug(meta, detected, bug, ...)`** builds the prompt and makes one call
through the shared `complete()` helper. It shows the judge three things: the **function
source** (so it can read the docstring and signature to decide what's contractual), the
**violated property** (with the original detector's own rationale, so the judge can push
back on it), and the **failing input + error**. `detected` can be `None` — if we can't
match the bug back to a detected property, we fall back to the raw relation string from
the bug. The stage name `"bug_verification"` is what shows up in the per-analysis trace
timeline and in any error.

**The skeptical stance is the whole trick.** The system prompt tells the reviewer that
*most reports are false alarms* and to *default to not-a-bug when unsure*. That bias
toward "no" is intentional — the point of the stage is to raise precision, so it should
err toward demoting. The four few-shot examples (the ß over-inference, the
`flatten_iter(0)` out-of-domain, and the two real bugs `to_text` / `absolute`) are taken
straight from the real evaluation, which grounds the judge in concrete separations
instead of an abstract rule.

**Where it's wired (in `main.py`, walkthrough 06):** the route calls a small
`_maybe_verify_bugs` helper after the sandbox step. That helper loops over the bugs,
calls `verify_bug` for each, and stores the result on `bug.verification`. It honours the
config toggle `bug_verification_enabled` and the per-request `verify_findings` override,
and it catches failures so a broken verdict never fails the request.

## What could go wrong

- **The judge is itself an LLM, so it isn't perfect.** A subtle case (the ß length
  example is genuinely subtle) can fool it either way. That's why we *demote* rather than
  *delete*: a finding the judge wrongly rejects is still shown, just lower down, so a real
  bug is never silently erased. Recall is protected by design, not by trusting the judge.

- **Circularity.** The judge and the original detector are the same family of model, so
  they can share blind spots — the judge might "re-confirm" the detector's own
  over-inference. We blunt this three ways: a different, skeptical *task framing*; forcing
  the two-question split (which makes it reason about the contract and the domain
  explicitly); and few-shot examples that show it rejecting over-inferences. If a
  benchmark ever shows it rubber-stamping its own mistakes, the next lever is to run the
  judge on a *different* model tier — deliberately left as a future option.

- **Cost.** It's one extra LLM call *per bug found*. Clean analyses (no bugs) cost
  nothing extra; an analysis with two bugs costs two extra calls (~a cent or two). The
  calls go through the same `complete()` chokepoint, so they're counted by the
  per-analysis cost meter and the global monthly cap automatically.

- **Garbage in, garbage out.** The judge decides "contractual" mostly from the
  docstring. A function with a poor or missing docstring gives it little to work with, so
  expect more "uncertain"-flavoured verdicts there. That's a real limit of the approach,
  not a bug in this file.

- **Turning it into a filter.** It would be tempting to just *drop* unconfirmed bugs to
  make the headline number look clean. Don't — that silently hides anything the judge gets
  wrong, which is exactly the failure mode the "annotate, don't drop" rule exists to
  prevent.

## Change history

- **2026-06-30 — created (Phase 10, D60).** New `verify.py`: `verify_bug` makes one
  skeptical second-opinion LLM call (stage `bug_verification`) returning a `BugVerdict`
  (`property_is_contractual` ∧ `input_in_domain` → `is_real`). Motivated by the batch
  bug-hunt eval (49 functions, ~20% precision, two false-positive classes). Wired into
  `/v1/analyze` as a best-effort post-filter that annotates each `Bug.verification`
  without dropping any bug (recall preserved); config `bug_verification_enabled` +
  request `verify_findings`. 184 tests green; the eval is the before/after benchmark,
  to be run once API credit is recharged. Worker/PR-bot path and the web/comment
  two-tier UI are a noted follow-on.
