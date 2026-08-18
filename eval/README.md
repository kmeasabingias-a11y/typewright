# Precision evaluation

**How often is TypeWright right?** This directory holds the measurement, the harness that
produced it, and the raw outputs behind every number — including the results that were
unflattering.

## ▶ [**EVAL_REPORT.md**](EVAL_REPORT.md) — the write-up

> ### Headline: **13–20% precision** — roughly one flagged candidate in six is a real bug.

| | |
|---|---|
| Corpus | 49 self-contained functions (of 146 discovered) across 13 modules |
| Runs | 2 (Sonnet 5, 2026-08-16) + 1 baseline (Sonnet 4.6, 2026-06-30) |
| Precision | **13%** and **20%** — ~17% average |
| Run-to-run overlap | **67%** (12 of 18 union candidates agreed) |
| Real bugs found | **4**, all still unfixed in `boltons` 26.1.0 |
| Cost | ~$0.037 per function |

Targets were `boltons`, `inflection`, and `stringcase` — real, widely-installed,
pure-Python libraries, not a synthetic benchmark.

## Why this is in the repository

Publishing "my tool found bugs in a famous library" is easy. This tries to do the harder
and more useful thing — say how often the tool is *wrong*, and why:

- **Every flagged candidate was hand-verified** against the real library before being called
  real or false. The false positives are enumerated and explained, not summarized away.
- **A stable failure-mode taxonomy** — the false positives fall into three classes that
  reproduce across two different models, and the largest class has an identifiable root cause.
- **A negative result about one of TypeWright's own features.** The adversarial verification
  stage (D60) was built specifically to raise precision. Measured, it *lowers* useful yield:
  it demotes a real bug and promotes a harness artifact, in both runs. It is reported here
  rather than quietly dropped.
- **Two occasions where the measurement harness corrupted its own data**, both documented —
  an inliner bug that manufactured five phantom crashes, and TypeWright's own spend cap
  silently returning HTTP 503 for 20 of 49 functions, which read as non-determinism until
  the status codes were checked.
- **Two borderline verdicts are flagged, not buried**, with the arithmetic shown for a reader
  who would judge them the other way.

## The four verified bugs

All four reproduce on `boltons` **26.1.0**, re-verified 2026-08-18 on CPython 3.12.

| function | defect |
|---|---|
| `iterutils.backoff_iter` | `ZeroDivisionError` at `factor=1.0` — which the function's own validation explicitly admits |
| `tableutils.to_text` | returns a string longer than the `maxlen` it was given, for `maxlen ≤ 3` |
| `typeutils.get_all_subclasses` | `TypeError` on any class whose subtree contains a metaclass |
| `timeutils.daterange` | month-overflow `ValueError` on a documented-valid step |

Full reproductions in [§4 of the report](EVAL_REPORT.md).

## Layout

```
EVAL_REPORT.md    the evaluation write-up
harness/          the benchmark harness (discovery, self-containment, scoring)
runs/             raw outputs backing every number in the report
```

- **[`harness/`](harness/)** — see [`harness/README.md`](harness/README.md) to re-run it,
  and read the operator-cap warning there first.
- **[`runs/`](runs/)** — per-run reports, JSON results, and logs for both campaigns, so the
  numbers can be recomputed rather than taken on trust. See [`runs/README.md`](runs/README.md).

## Honest scope

TypeWright is an **exploratory candidate generator, not an authoritative bug finder.** At
~17% precision it is useful for surfacing candidates a human then triages, and not useful as
an unattended gate. That is the conclusion the measurement supports, and publishing the
number is the point of this directory.
