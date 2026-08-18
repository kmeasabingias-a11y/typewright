# Raw run artifacts

Every number in [`../EVAL_REPORT.md`](../EVAL_REPORT.md) is recomputable from these files.
Nothing here is summarized — the `*_report.md` files list each flagged candidate with the
full inferred property set, the failing input, and the function's real source.

## `sonnet5-2026-08-16/` — the current measurement

Two independent runs over the same 49-function corpus, verification stage on.

| file | what it is |
|---|---|
| `run1_report.md` / `run1_results.json` / `run1.stdout` | Run 1 — 15 flagged, **2 real**, 13% precision, $1.8039 |
| `run2_report.md` / `run2_results.json` / `run2.stdout` | Run 2 — 15 flagged, **3 real**, 20% precision, $1.8492 |
| `run2_ABORTED_by_cap.json` | **A discarded run, kept deliberately.** |

### About `run2_ABORTED_by_cap.json`

This is the first attempt at run 2, destroyed by TypeWright's own daily spend cap: run 1 had
left the daily counter at $1.88 against a $3.00 limit, so 20 of 49 functions returned HTTP
503 — including all three ground-truth functions.

It is checked in because at a glance it looks like a dramatic result ("the real bugs
disappeared between runs") and is in fact an artifact of the measuring apparatus. It is the
evidence behind §7 of the report, and a standing reminder to check status codes before
believing a finding.

## `sonnet4.6-2026-06-30/` — the June baseline

The first campaign, one run, before the verification stage existed.

| file | what it is |
|---|---|
| `sweep2_report.md` / `sweep2_results.json` / `.jsonl` | The 49-function run — 15 flagged, 3 real, ~20%, $2.28 |
| `sweep2_baseline_preD60.json` | The same corpus run *before* the verification stage was built |
| `sweep2.stdout` / `sweep2_clean.stdout` / `sweep2_verif.stdout` | Run logs. `sweep2.stdout` is the 38-function run made *before* the inliner bug was fixed — the fix raised the self-contained count to 49 |
| `sweep_report.md` / `sweep_results.json` | The original 9-function pilot |

Superseded by the August campaign where the two conflict, and retained because it is the
only pre-verification data point and the only cross-model comparison available.

## Reading a report file

Each flagged function gets a section containing:

- every property the engine inferred, with its category and self-assigned confidence;
- each violation found, with the failing input and the exception type;
- the function's real source, exactly as it was sent to the sandbox.

The self-assigned confidence scores are worth looking at: they do **not** separate real bugs
from false positives, which is why confidence gating was tried and rejected (§5 of the report).
