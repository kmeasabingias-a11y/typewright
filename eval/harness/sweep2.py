"""Evaluation-scale sweep: auto-discover public functions across pure-Python modules,
keep the genuinely self-contained ones (imports-only inlining, proven faithful in the
pilot), run up to N through the live /v1/analyze, and produce a precision report.
Reuses the pilot harness (sweep.py) for the self-containment transform + the API call.
"""
import inspect
import os
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Working directory: holds the target libraries under ./libs and receives run
# artifacts. Override with TW_EVAL_WORKDIR; defaults to this script's directory.
SP = os.environ.get("TW_EVAL_WORKDIR") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
sys.path.insert(0, SP + "/libs")

import sweep  # make_self_contained, analyze, BUILTINS, STDLIB

N_CAP = 80          # bound cost: ~80 * ~$0.04 ~= $3.2
WORKERS = 5

# Broad set of pure-Python modules to scan.
MODULES = []
def _try(import_path):
    try:
        mod = __import__(import_path, fromlist=["_"])
        MODULES.append(mod)
    except Exception as e:
        print(f"  (could not import {import_path}: {e})")

for p in ["inflection", "humanize", "humps", "roman", "stringcase",
          "boltons.strutils", "boltons.mathutils", "boltons.iterutils",
          "boltons.listutils", "boltons.dictutils", "boltons.funcutils",
          "boltons.formatutils", "boltons.statsutils", "boltons.typeutils",
          "boltons.setutils", "boltons.cacheutils", "boltons.tableutils",
          "boltons.timeutils", "boltons.urlutils"]:
    _try(p)

# Param names that strongly suggest a callable/complex arg Hypothesis can't fuzz well.
CALLABLE_HINTS = {"func", "function", "key", "callback", "cb", "visit", "predicate",
                  "pred", "op", "default_factory", "factory", "get_default", "handler",
                  "hook", "fn", "reducer", "getter", "setter", "comparator", "cmp"}


def fuzzable(func):
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return False
    params = list(sig.parameters.values())
    pos = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if not pos:
        return False                       # nothing to fuzz
    for p in params:
        if p.name in CALLABLE_HINTS:
            return False
    return True


def discover():
    out, seen = [], set()
    for mod in MODULES:
        for name, func in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_"):
                continue
            if getattr(func, "__module__", None) != mod.__name__:
                continue                    # only functions DEFINED in this module
            key = (mod.__name__, name)
            if key in seen:
                continue
            seen.add(key)
            if not fuzzable(func):
                continue
            label = mod.__name__.replace("boltons.", "b.")
            out.append((label, mod, name, func))
    return out


def main():
    discovered = discover()
    runnable, skipped = [], []
    for label, mod, name, func in discovered:
        try:
            code, need, unresolved = sweep.make_self_contained(func)
        except Exception as e:
            skipped.append({"lib": label, "func": name, "reason": f"extract-error: {e!r}"})
            continue
        if code is None:
            skipped.append({"lib": label, "func": name, "reason": "needs " + ", ".join(unresolved)})
        else:
            runnable.append({"lib": label, "func": name, "code": code, "imports": need})

    runnable.sort(key=lambda r: (r["lib"], r["func"]))
    total_runnable = len(runnable)
    if len(runnable) > N_CAP:
        runnable = runnable[:N_CAP]

    print(f"discovered={len(discovered)} runnable={total_runnable} "
          f"running={len(runnable)} (cap {N_CAP}) skipped={len(skipped)}")

    results = []
    lock = threading.Lock()
    jsonl = open(SP + "/sweep2_results.jsonl", "w")

    def run_one(item):
        status, payload, secs = sweep.analyze(item["func"], item["code"])
        rec = {**item, "status": status, "payload": payload, "secs": round(secs, 1)}
        with lock:
            nb = len(payload.get("bugs_found", []) or []) if status == 200 else "-"
            print(f"  [{status}] {item['lib']}.{item['func']}  bugs={nb}  {rec['secs']}s", flush=True)
            jsonl.write(json.dumps({k: rec[k] for k in ("lib", "func", "status", "secs", "imports")}) + "\n")
            jsonl.flush()
        return rec

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(run_one, it) for it in runnable]
        for fut in as_completed(futs):
            results.append(fut.result())
    jsonl.close()

    def sortkey(r):
        if r["status"] == 200:
            nb = len(r["payload"].get("bugs_found", []) or [])
            return (0 if nb else 1, -nb, r["lib"], r["func"])
        return (2, 0, r["lib"], r["func"])
    results.sort(key=sortkey)

    json.dump({"results": results, "skipped": skipped}, open(SP + "/sweep2_results.json", "w"), indent=2)

    bugged = [r for r in results if r["status"] == 200 and (r["payload"].get("bugs_found") or [])]
    clean = [r for r in results if r["status"] == 200 and not (r["payload"].get("bugs_found") or [])]
    errs = [r for r in results if r["status"] != 200]
    total_cost = sum(float(r["payload"].get("metadata", {}).get("llm_cost_usd") or 0)
                     for r in results if r["status"] == 200)

    # per-library breakdown
    libs = {}
    for r in results:
        d = libs.setdefault(r["lib"], {"run": 0, "flagged": 0})
        d["run"] += 1
        if r["status"] == 200 and (r["payload"].get("bugs_found") or []):
            d["flagged"] += 1

    L = ["# Evaluation sweep — report", ""]
    L.append(f"**{len(results)} functions** run across **{len(libs)} modules** · "
             f"**{len(bugged)} flagged** · {len(clean)} clean · {len(errs)} errored · "
             f"~${total_cost:.4f} LLM cost.")
    L.append(f"\nDiscovered {len(discovered)} public functions; {total_runnable} were self-contained "
             f"(imports-only); ran {len(results)} (cap {N_CAP}). {len(skipped)} skipped "
             f"(companion fn / module constant).")
    L.append("\n## Per-module")
    L.append("| module | run | flagged |")
    L.append("|---|---|---|")
    for lib in sorted(libs):
        L.append(f"| {lib} | {libs[lib]['run']} | {libs[lib]['flagged']} |")

    L.append("\n## Flagged candidates (manual verification needed)")
    if not bugged:
        L.append("_none_")
    for r in bugged:
        p = r["payload"]
        L.append(f"\n### {r['lib']}.{r['func']}  ({len(p['bugs_found'])} finding(s))")
        for d in (p.get("properties", {}) or {}).get("detected", []):
            L.append(f"- _prop_ `{d.get('relation')}`  [{d.get('property_class')}, conf {d.get('confidence')}]")
        for b in p["bugs_found"]:
            L.append(f"- **{b.get('severity')}** · violates `{b.get('violated_property')}` · "
                     f"input `{str(b.get('failing_input'))[:120]}` · {b.get('error')}")
        L.append("\n```python\n" + r["code"] + "\n```")

    L.append("\n## Clean (no findings)")
    L.append(", ".join(f"{r['lib']}.{r['func']}" for r in clean) or "_none_")
    L.append("\n## Errored")
    for r in errs:
        L.append(f"- [{r['status']}] {r['lib']}.{r['func']} — {str(r['payload'])[:160]}")
    open(SP + "/sweep2_report.md", "w").write("\n".join(L) + "\n")
    print(f"\nDONE: {len(results)} run, {len(bugged)} flagged, ~${total_cost:.4f}. "
          f"Report: {SP}/sweep2_report.md")


if __name__ == "__main__":
    main()
