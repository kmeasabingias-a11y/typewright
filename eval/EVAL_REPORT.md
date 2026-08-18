# TypeWright — bug-finding precision evaluation

**A labelled 49-function benchmark of real library code, run through the live `/v1/analyze`
engine, with every flagged candidate hand-verified against the actual library source.**

Two measurement campaigns:

| | June 2026 | August 2026 |
|---|---|---|
| Date | 2026-06-30 | 2026-08-16 |
| Model | Claude Sonnet 4.6 (`claude-sonnet-4-6`) | Claude Sonnet 5 (`claude-sonnet-5`) |
| Runs | 1 | 2 |
| Verification stage (D60) | not yet built | on |

> ## Headline: **13–20% precision** (~17% average, n=2 runs, Sonnet 5)
>
> Roughly one in six flagged candidates is a real bug. TypeWright is an **exploratory candidate
> generator, not an authoritative bug finder** — and this document exists to say so with a number
> attached rather than a disclaimer.

The measured artifact here is not "we found bugs in famous libraries." It is the evaluation:
a precision figure, a stable failure-mode taxonomy, a run-to-run reproducibility measurement,
a clean negative result about the project's own verification stage, and an honest record of
two occasions where the measurement harness corrupted its own data.

---

## 1. Results

### Sonnet 5 — two runs, 2026-08-16

| | Run 1 | Run 2 |
|---|---|---|
| Functions run | 49 | 49 |
| Flagged | 15 | 15 |
| **Verified real bugs** | **2** | **3** |
| False positives | 13 | 12 |
| **Raw precision** | **13%** (2/15) | **20%** (3/15) |
| Clean (no findings) | 33 | 34 |
| Errored | 1 (`daterange`, 504 timeout) | 0 |
| LLM cost | $1.8039 | $1.8492 |

**Publishable range: 13–20%, ~17% average.** Cost ≈ **$0.037 per function** — *cheaper* than
June's $0.047/fn despite Sonnet 5 producing more output, because Sonnet 5 was under introductory
pricing ($2/$10 per MTok, through 2026-08-31) rather than the standard $3/$15.

Total campaign spend was **$4.90**, of which $3.65 is the two clean runs above; the remainder was
a first attempt at run 2 that had to be discarded (see §5).

### June 2026 baseline — Sonnet 4.6

49 functions · 15 flagged · **3 real** · **precision ~20%** · $2.28. Superseded by the above
where the two conflict; retained because it is the only pre-D60 data point.

### Corpus

146 public functions discovered across 13 pure-Python modules (`inflection`, `stringcase`, and 11
`boltons.*` submodules: strutils, mathutils, iterutils, dictutils, funcutils, formatutils,
statsutils, typeutils, tableutils, timeutils, urlutils). Only **49 (~34%) were self-contained**
enough to run standalone; 97 were skipped because they depend on a companion function or a
module-level constant. That ratio is itself a finding about how much library code is inseparable
from its module context. `humps` / `roman` / `humanize` were discovered but contributed nothing —
every function in them depends on module state.

---

## 2. Reproducibility: the flag count is stable, the flag set is not

Both Sonnet-5 runs flagged **exactly 15** functions. That number is stable. *Which* 15 is not.

- Union across both runs: **18** functions
- Agreed by both runs: **12**
- **Run-to-run overlap: 12/18 = 67%**
- Unique to run 1: `format_histogram_counts`, `ceil`, `ordinal`
- Unique to run 2: `format_nonexp_repr`, `backoff_iter`, `floor`

**The June result that "real bugs vanish between runs" did not reproduce.** Both Sonnet-5 runs
found the same two core real bugs (`to_text`, `get_all_subclasses`). On this benchmark Sonnet 5 is
materially more reproducible than Sonnet 4.6: **the noise is in the tail, not the core.** The
practical consequence is that a single run under-reports — run 2's extra real bug (`backoff_iter`)
appeared in only one of two sweeps.

---

## 3. All 18 flagged candidates — triaged

`R1`/`R2` mark which run flagged each function.

| # | function | R1 | R2 | the engine's finding | **verdict** | reason |
|---|---|:-:|:-:|---|---|---|
| 1 | `tableutils.to_text` | ● | ● | `len(to_text(obj, maxlen)) <= maxlen` violated | ✅ **REAL** | truncation breaks for `maxlen ≤ 3` |
| 2 | `typeutils.get_all_subclasses` | ● | ● | crash on `cls=type` / `object` | ✅ **REAL** | unguarded `type.__subclasses__()` in the recursion |
| 3 | `iterutils.backoff_iter` | — | ● | `ZeroDivisionError` at `factor=1.0` | ✅ **REAL** | the library's own guard admits `factor == 1.0` |
| 4 | `funcutils.copy_function` | ● | ● | `ImportError` on `lambda: None` | ❌ harness artifact | works fine in a normal env; sandbox-only |
| 5 | `iterutils.flatten_iter` | ● | ● | `TypeError` on `nested=0` | ❌ out-of-domain | `0` is not iterable; raising is correct |
| 6 | `timeutils.strpdate` | ● | ● | `ValueError` on `date(999,1,1)` | ❌ stdlib quirk | Python's own pre-1000-year strftime/strptime asymmetry |
| 7 | `funcutils.partial_ordering` | ● | ● | `TypeError` on a synthesized class | ❌ out-of-domain | decorator documents that `__le__`/`__ge__` must exist |
| 8 | `strutils.under2camel` | ● | ● | idempotence / length growth on `''` | ❌ over-inference | neither property is promised |
| 9 | `strutils.unwrap_text` | ● | ● | idempotence on `'\x1e0'` | ❌ over-inference | `\x1e` is a `splitlines()` boundary; not promised |
| 10 | `inflection.camelize` | ● | ● | first-letter case flag on `'ʟ'` | ❌ over-inference | Unicode titlecase; relation not promised |
| 11 | `stringcase.lowercase` | ● | ● | case-fold roundtrip on `'ß'` | ❌ over-inference | `'ß'.upper() == 'SS'`; not case-invariant |
| 12 | `stringcase.uppercase` | ● | ● | `len(upper(s)) == len(s)` on `'ß'` | ❌ over-inference | length never promised |
| 13 | `stringcase.alphanumcase` | ● | ● | invariant on `'_'` | ❌ over-inference | stripping non-alphanumerics is the documented job |
| 14 | `inflection.ordinal` | ● | — | `ordinal(n) == ordinal(n+100)` at `n=-1` | ❌ over-inference | that relation is not part of the contract |
| 15 | `mathutils.ceil` | ● | — | `ValueError` at `ceil(0, [-1])` | ❌ out-of-domain | documented to raise when no option qualifies |
| 16 | `mathutils.floor` | — | ● | same shape as `ceil` | ❌ out-of-domain | same — documented to raise |
| 17 | `statsutils.format_histogram_counts` | ● | — | `ZeroDivisionError` on `[(0.0, 0)]` | ⚠️ judgement → FP | all-zero histogram; not produced by `get_histogram_counts` |
| 18 | `funcutils.format_nonexp_repr` | — | ● | `TypeError` on `obj=None, opt_names=['length']` | ⚠️ judgement → FP | asks for an attribute the object doesn't have |

**Run 1: 2 real · 13 FP. Run 2: 3 real · 12 FP.**

⚠️ **Two verdicts are judgement calls at the boundary** (#17, #18), and this report flags them
rather than burying them. Both are "degenerate input that the documented API arguably admits."
Counted as real, precision would read **20% / 27%** instead of 13% / 20%. They are counted as
false positives here because in each case the input cannot arise from the library's own
documented usage. A reader who disagrees can recompute — that is the point of showing the row.

### Not flagged, but real

`timeutils.daterange(start, stop, step=(0,1,1))` is a hand-verified real bug (month-overflow
`ValueError`), found in June. Neither Sonnet-5 run reported it: run 1 **errored** on it (504, the
30s test-execution budget), and run 2 ran it clean but flagged nothing. That is a **false negative
on a known-good target**, and it belongs in the precision picture as much as the false positives do.

---

## 4. The four verified bugs

All four re-verified by hand against **boltons 26.1.0** (current release, re-run 2026-08-18 on
CPython 3.12). All four reproduce there. Three remain unfixed upstream; `backoff_iter` was
independently fixed on `master` after 26.1.0 shipped — see the note below, which is
corroboration rather than a correction.

### 1. `iterutils.backoff_iter(start, stop, factor=1.0)` — `ZeroDivisionError` *(new, Sonnet 5)*

```python
>>> list(backoff_iter(1.0, 1.0, factor=1.0))
ZeroDivisionError: float division by zero
>>> list(backoff_iter(1.0, 1.0, factor=1.0, count=3))
[1.0, 1.0, 1.0]        # passing count avoids it — the log path is the problem
```

The function's **own validation rejects anything below 1.0** — `backoff_iter(1.0, 10.0, factor=0.5)`
raises `ValueError: expected factor >= 1.0, not 0.5` — so `factor == 1.0` is explicitly declared
in-domain by the library itself. It then computes
`count = 1 + math.ceil(math.log(stop/denom, factor))`, and `math.log(1.0)` is `0`.

> **Independently confirmed.** This bug was found and fixed by another contributor in
> [PR #428](https://github.com/mahmoud/boltons/pull/428) — opened 2026-07-17, merged
> 2026-07-18, six hours after 26.1.0 was released. The accepted fix raises a descriptive
> `ValueError` when `count is None` and `factor == 1.0`, matching option 2 of the three
> remedies this analysis proposed. It is therefore present in the current *release* and
> absent from current `master`.
>
> That a human found the same defect independently, and that the maintainer merged a fix
> for it, is **third-party evidence that this finding was a genuine bug rather than a false
> positive** — which is the claim a precision evaluation most needs corroborated. It is
> recorded here for that reason, and because a report that quietly dropped it would be less
> honest than one that says a competitor got there first.

This is the cleanest of the four — there is no judgement call about whether the input is
"documented valid," because the library's own guard admits it. Same class as
`get_all_subclasses`: *a guard that admits an input, followed by an unguarded crash on it.*

Worth noting what the engine actually inferred here. The property it wrote was:

> `backoff_iter(...) does not raise for valid domain (start>=0, stop>=start>0, factor>=1, -1<=jitter<=1, count>=0 or 'repeat')`

It read the library's own guards out of the source and turned them into the precondition — then
found the input that satisfies them and crashes. That is textbook-correct property inference.

### 2. `tableutils.to_text(obj, maxlen)` — violates its own length cap

```python
>>> to_text(12345, maxlen=2)
'1234...'        # 7 characters, for a maxlen of 2
>>> to_text(None, maxlen=1)
'No...'          # 5 characters, for a maxlen of 1
```

Truncation is `text[:maxlen - 3] + '...'`. For `maxlen ≤ 3` the slice index goes negative and
`'...'` is appended anyway, so the result is *longer* than the cap. No documented lower bound on
`maxlen`. The clearest of the four: a full-domain wrong answer, not a crash.

### 3. `typeutils.get_all_subclasses(cls)` — crashes on a valid type

```python
>>> get_all_subclasses(int)        # returns a list — fine
>>> get_all_subclasses(object)     # TypeError: unbound method type.__subclasses__() needs an argument
```

The `try/except TypeError` guards only the *first* `cls.__subclasses__()` call. Inside the
recursion loop it eventually reaches `type` (a subclass of `object`) and calls
`type.__subclasses__()` unguarded. It therefore fails on any class whose subtree contains a
metaclass.

### 4. `timeutils.daterange(start, stop, step=(0,1,1))` — month-overflow crash

```python
>>> list(daterange(date(2000,1,1), None, (0,1,1)))
ValueError: day is out of range for month
```

The `(year, month, day)` tuple step calls `now.replace(month=...)` without clamping the day; as
the day component climbs it eventually lands on a short month (Feb 30) and raises. The docstring's
own example `(0,1,0)` is safe; `(0,1,1)` is documented-valid.

---

## 5. Negative result: the verification stage (D60) is net-negative

TypeWright ships an adversarial second-opinion stage that re-examines each candidate bug and asks
*is this property actually contractual? is this input actually in-domain?* It was built to raise
precision. **On this benchmark it does not — it makes things worse, deterministically.**

| | Run 1 | Run 2 |
|---|---|---|
| Raw | 2 real / 15 flagged = 13% | 3 real / 15 flagged = 20% |
| After D60 verification | 1 real / 4 confirmed = 25% | 1 real / 4 confirmed = 25% |

The 25% looks like a win until you read which bug survived. **In both runs the verifier:**

1. **demoted `to_text`** — a real bug carrying the exactly-correct property
   `len(to_text(obj, maxlen)) <= maxlen`; and
2. **confirmed `copy_function`** — a harness artifact that works perfectly in a normal
   environment, and which alone accounted for 6 bogus findings (18% of everything flagged).

So the stage costs a real bug and keeps an artifact, in both runs. June's softer read ("no clear
win") is now the stronger claim. **It is kept in the product, off the critical path, and reported
here as a negative result rather than quietly dropped** — an LLM judging another LLM's output was
the intuitive fix and it did not survive measurement.

This is the third rejected precision idea with evidence behind it. Also tried and rejected:
**confidence gating** (D57 — the model's own confidence scores don't separate real from FP) and
**cross-run agreement** (D60 — see §2: the FPs that repeat are exactly the systematic ones).

---

## 6. The false-positive taxonomy

Stable across both models — the same three classes, in the same rough proportion, on Sonnet 4.6
and Sonnet 5.

**A. Over-inferred properties (the dominant class).** The engine asserts something the function
never promised. Three recurring sub-shapes:

- *Unicode assumptions* — `len(uppercase('ß')) == len('ß')` (it's `'SS'`), the `lowercase`
  case-fold roundtrip, `camelize('ʟ')`.
- *Unpromised idempotence* — `under2camel`, `unwrap_text`. Neither docstring claims it.
- *Unpromised metamorphic relations* — `ordinal(n) == ordinal(n+100)`, `subdict` supersets.

This is the fundamental precision limit and the target of every lever in §8.

**B. Out-of-domain inputs.** The generated strategy feeds an input the contract excludes, and the
function correctly raises: `flatten_iter(0)`, `ceil(0, [-1])` and `floor` (both *documented* to
raise when no option qualifies), `partial_ordering` handed a synthesized class that lacks the
comparison methods the decorator requires.

**C. Environment and harness artifacts.** `copy_function`'s sandbox `ImportError` (6 findings from
one function); `strpdate` inheriting CPython's own pre-1000-year strftime/strptime asymmetry —
a real quirk, but not a boltons bug.

**Cosmetic, not a class of its own:** `failing_input` still captures Hypothesis's inline comments,
so a reported input reads `start=1.0, # or any other generated value stop=1.0,`. Ugly in the
report; harmless to the verdict.

---

## 7. Methodology, and two ways the harness lied to us

The rigor claims are worth more than the precision number, so here is what was actually done —
including the parts that went wrong.

**Self-containment filter.** Each candidate's real source (`inspect.getsource`) was made standalone
by inlining the stdlib imports it needs, resolved through the function's actual `__globals__` so
that `from datetime import datetime` is reproduced faithfully rather than guessed. Anything still
referencing a companion function or module constant was **skipped**, not stubbed.

**Every flagged candidate was reproduced by hand** against the real library before being called
real or false — false positives included. Nothing in §3 is a model's self-assessment.

### Self-inflicted measurement failure #1 (June): the inliner manufactured bugs

The first inliner emitted `import datetime` where boltons actually uses
`from datetime import datetime`. Every `strpdate` call then failed on an attribute that didn't
exist, producing **5 phantom crashes** that looked exactly like real findings. Caught during
hand-verification, not by any automated check. Fixing it (the `__globals__` resolution above)
also raised the self-contained count from 38 to 49.

**If the hand-verification step had been skipped, those 5 phantoms would have been reported as
bugs in a real library.**

### Self-inflicted measurement failure #2 (August): our own cost cap corrupted a run

The first attempt at run 2 was silently destroyed by **TypeWright's own daily spend cap**. Run 1
had left the daily counter at $1.88 against a $3.00 limit, so **20 of 49 functions returned
HTTP 503** — including all three ground-truth functions. The result read as dramatic
non-determinism ("the real bugs vanished!") until the status codes were actually checked.

This is the *second* occurrence of the same class of error: in June, the D53 rate limiter 429'd the
operator's own batch at >10 req/min. Both times a production safety guard worked exactly as
designed and silently poisoned the measurement.

Two standing rules came out of it:

1. **An operator batch must explicitly lift both caps** — restart with
   `TYPEWRIGHT_MAX_DAILY_COST_USD` and `TYPEWRIGHT_MAX_MONTHLY_COST_USD` raised.
2. **`sweep2.py` must abort loudly on any non-200** rather than recording it as a result. A 503
   is not a data point.

There is a general lesson here worth more than the specific fix: **the harness is part of the
experiment.** Two of the most dramatic-looking results this project produced — "phantom `strpdate`
crashes" and "the real bugs are non-deterministic" — were both artifacts of the measuring
apparatus, and both were caught only by checking the boring thing (the actual import statement,
the actual status code).

---

## 8. Where the precision actually goes — ranked levers

Derived from this data. **None of these are built** — they are post-launch work, listed so the
number above has a direction attached.

1. **Require textual grounding (highest value).** Make the detection stage cite the exact
   docstring or signature substring that licenses each property, then **deterministically drop any
   property whose citation isn't literally present in the source.** This attacks class A — the
   dominant FP class — at its origin: `uppercase` never says anything about length, so the
   citation cannot exist, so the property never survives to be tested. Deterministic filters over
   LLM output are the shape that has actually worked in this codebase twice (D57's symtable guard,
   D61's import allowlist), whereas asking an LLM to self-assess has now failed three times.
   Rough estimate on this data: 2/15 → ~2/6 ≈ **33%**.
2. **Type-hint conformance on the generated input.** Reject a failing input that violates the
   function's own annotations. Kills `flatten_iter(0)` and most of class B for free.
3. **Two-tier reporting.** Split findings into *grounded* and *speculative* rather than one flat
   list. No engine change at all — the cheapest real win available, and it makes the 13–20% honest
   at the UI layer instead of in a footnote.

**Rejected, with evidence:** confidence gating (D57), cross-run agreement (D60), and more or
better LLM judges (D60 + §5 above).

---

## 9. What this benchmark is now

A **labelled corpus**: 49 self-contained functions with 4 known-real bugs and a hand-triaged
false-positive set, plus a recorded false negative (`daterange`). Any future precision change can
be measured against it rather than argued about.

Every number in this document is recomputable from the raw artifacts checked in beside it:
[`runs/sonnet5-2026-08-16/`](runs/sonnet5-2026-08-16/) for the August campaign and
[`runs/sonnet4.6-2026-06-30/`](runs/sonnet4.6-2026-06-30/) for June — including
`run2_ABORTED_by_cap.json`, the run destroyed by our own spend cap in §7, kept as evidence.

The harness is in [`harness/`](harness/): `sweep.py` (self-containment transform +
`/v1/analyze` client), `sweep2.py` (discovery and driver), and `bench_analyze.py`
(before/after scoring against ground truth). See [`harness/README.md`](harness/README.md) to
re-run it — and read the operator-cap warning there first.

**The honest summary: about one flagged candidate in six is a real bug, the tool found four real
defects in a widely-used library — three of them still unfixed, and the fourth independently
confirmed by a human who found and fixed it a month earlier — and the most useful thing it produced
was a clean negative result about its own verification stage.**
