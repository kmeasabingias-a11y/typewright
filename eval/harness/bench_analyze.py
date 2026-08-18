"""Read a verification-ON sweep (sweep2_results.json) and compute the D60 before/after.

Verification is a POST-FILTER, so one run gives both: 'before' = every flagged function,
'after' = the subset with at least one verification-CONFIRMED bug. Precision is measured
against hand-verified ground truth (the 4 real bugs from the eval); any newly-flagged
function not in the labelled set is printed as NEEDS REVIEW so it can be hand-checked.
"""
import json
import os

# Working directory: holds the target libraries under ./libs and receives run
# artifacts. Override with TW_EVAL_WORKDIR; defaults to this script's directory.
SP = os.environ.get("TW_EVAL_WORKDIR") or os.path.dirname(os.path.abspath(__file__))

# Hand-verified ground truth from the 49-fn eval (function has a genuine bug).
# backoff_iter was added 2026-08-16: found only on Sonnet 5, in 1 of 2 runs.
REAL = {"to_text", "get_all_subclasses", "daterange", "backoff_iter"}
# Hand-verified false positives (over-inference / out-of-domain / environment artifact).
KNOWN_FP = {
    "copy_function", "strpdate", "flatten_iter", "subdict", "format_histogram_counts",
    "under2camel", "unwrap_text", "ordinal", "underscore", "alphanumcase",
    "lowercase", "uppercase", "a10n", "frange", "args2cmd", "removeprefix",
    "construct_format_field_str", "titleize", "camel2under",
    # added 2026-08-16 (Sonnet 5 runs); see EVAL_REPORT.md section 3 for the triage.
    "ceil", "floor", "camelize", "partial_ordering", "format_nonexp_repr",
}


def confirmed_bug(b):
    v = b.get("verification")
    return bool(v) and v.get("property_is_contractual") and v.get("input_in_domain")


def main():
    data = json.load(open(SP + "/sweep2_results.json"))
    results = data["results"]
    flagged = [r for r in results if r["status"] == 200 and (r["payload"].get("bugs_found") or [])]

    review = []
    print(f"=== {len(flagged)} flagged functions ===\n")
    for r in sorted(flagged, key=lambda r: r["func"]):
        bugs = r["payload"]["bugs_found"]
        n_conf = sum(1 for b in bugs if confirmed_bug(b))
        label = "REAL" if r["func"] in REAL else ("FP" if r["func"] in KNOWN_FP else "??REVIEW")
        if label == "??REVIEW":
            review.append(r["func"])
        func_confirmed = "CONFIRMED" if n_conf else "demoted"
        print(f"[{label:8}] {r['func']:24} bugs={len(bugs)} confirmed={n_conf}  -> {func_confirmed}")
        for b in bugs:
            v = b.get("verification") or {}
            mark = "✓CONFIRM" if confirmed_bug(b) else "✗demote "
            print(f"            {mark}  {b['violated_property'][:64]}")

    # function-level precision (a function is 'confirmed' if any bug confirmed)
    conf_funcs = [r for r in flagged if any(confirmed_bug(b) for b in r["payload"]["bugs_found"])]
    flagged_names = {r["func"] for r in flagged}
    conf_names = {r["func"] for r in conf_funcs}

    def prec(names):
        real_hit = len(names & REAL)
        total = len(names)
        return real_hit, total, (real_hit / total if total else 0.0)

    rb, tb, pb = prec(flagged_names)
    ra, ta, pa = prec(conf_names)
    print("\n=== FUNCTION-LEVEL precision (vs ground truth) ===")
    print(f"BEFORE (all flagged):     {rb} real / {tb} flagged   = {pb:.0%}")
    print(f"AFTER  (verification on): {ra} real / {ta} confirmed = {pa:.0%}")
    print(f"RECALL of real bugs kept: {len(conf_names & REAL)} / {len(flagged_names & REAL)} "
          f"(real bugs that survived verification)")
    if review:
        print(f"\n!! NEEDS MANUAL REVIEW (newly flagged, not in labelled set): {sorted(review)}")

    # bug-level tally
    all_bugs = [b for r in flagged for b in r["payload"]["bugs_found"]]
    conf_bugs = [b for b in all_bugs if confirmed_bug(b)]
    print(f"\n=== BUG-LEVEL ===")
    print(f"total flagged bugs: {len(all_bugs)}  | verification-confirmed: {len(conf_bugs)}  | "
          f"demoted: {len(all_bugs) - len(conf_bugs)}")


if __name__ == "__main__":
    main()
