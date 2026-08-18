"""Pilot bug-hunting sweep over real pure-Python library functions.

For each candidate function: take its REAL source (inspect.getsource), drop decorators,
auto-inline the stdlib imports it needs (re/math/...), and SKIP it if anything else is
still free (a companion function or a module constant) — those would crash spuriously in
the sandbox and aren't a real signal. Self-contained ones are POSTed to the live
/v1/analyze; results are ranked into a report for manual triage.
"""
import ast
import builtins
import inspect
import json
import os
import sys
import textwrap
import time
import types
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# Working directory: holds the target libraries under ./libs and receives run
# artifacts. Override with TW_EVAL_WORKDIR; defaults to this script's directory.
SP = os.environ.get("TW_EVAL_WORKDIR") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/libs")

import inflection                                   # noqa: E402
import boltons.strutils as bstr                     # noqa: E402
import boltons.mathutils as bmath                   # noqa: E402
import boltons.iterutils as biter                   # noqa: E402
import humanize                                      # noqa: E402

API = "http://localhost:8001/v1/analyze"
BUILTINS = set(dir(builtins))
STDLIB_NAMES = sys.stdlib_module_names  # the full stdlib top-level module set (py3.10+)
# kept for back-compat with the pilot harness import; resolution now uses __globals__
STDLIB = {"re", "math", "itertools", "functools", "string", "collections", "datetime",
          "decimal", "fractions", "bisect", "heapq", "unicodedata", "operator",
          "textwrap", "random", "statistics", "json"}

# (library label, module, function name). Generous list — the harness keeps only the
# genuinely self-contained ones and reports the rest.
CANDIDATES = [
    ("inflection", inflection, "camelize"),
    ("inflection", inflection, "underscore"),
    ("inflection", inflection, "dasherize"),
    ("inflection", inflection, "humanize"),
    ("inflection", inflection, "titleize"),
    ("inflection", inflection, "ordinal"),
    ("inflection", inflection, "ordinalize"),
    ("inflection", inflection, "pluralize"),
    ("inflection", inflection, "singularize"),
    ("inflection", inflection, "parameterize"),
    ("inflection", inflection, "tableize"),
    ("inflection", inflection, "transliterate"),
    ("boltons.strutils", bstr, "slugify"),
    ("boltons.strutils", bstr, "camel2under"),
    ("boltons.strutils", bstr, "under2camel"),
    ("boltons.strutils", bstr, "ordinalize"),
    ("boltons.strutils", bstr, "cardinalize"),
    ("boltons.strutils", bstr, "bytes2human"),
    ("boltons.strutils", bstr, "strip_ansi"),
    ("boltons.mathutils", bmath, "clamp"),
    ("boltons.mathutils", bmath, "ceil"),
    ("boltons.mathutils", bmath, "floor"),
    ("boltons.iterutils", biter, "first"),
    ("humanize", humanize, "ordinal"),
    ("humanize", humanize, "intcomma"),
    ("humanize", humanize, "apnumber"),
]


def _bound_and_used(fn):
    """Approximate the names bound (params, assignments, nested defs, imports) and the
    names used (Load) inside a function body."""
    bound, used = set(), set()
    a = fn.args
    for arg in list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs):
        bound.add(arg.arg)
    if a.vararg:
        bound.add(a.vararg.arg)
    if a.kwarg:
        bound.add(a.kwarg.arg)

    class V(ast.NodeVisitor):
        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Load):
                used.add(n.id)
            else:
                bound.add(n.id)

        def visit_arg(self, n):
            bound.add(n.arg)

        def visit_FunctionDef(self, n):
            bound.add(n.name)
            self.generic_visit(n)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, n):
            for arg in list(getattr(n.args, "posonlyargs", [])) + list(n.args.args) + list(n.args.kwonlyargs):
                bound.add(arg.arg)
            self.generic_visit(n)

        def visit_ExceptHandler(self, n):
            if n.name:
                bound.add(n.name)
            self.generic_visit(n)

        def visit_Import(self, n):
            for al in n.names:
                bound.add((al.asname or al.name).split(".")[0])

        def visit_ImportFrom(self, n):
            for al in n.names:
                bound.add(al.asname or al.name)

    V().visit(fn)
    return bound, used


def _resolve_imports(fn, func):
    """Resolve each free global to a FAITHFUL stdlib import using the function's actual
    __globals__ — so `from datetime import datetime` is reproduced, not a wrong `import
    datetime`. Anything that isn't a stdlib module/class/function (a library helper or a
    plain module constant) is left unresolved, so the function is skipped."""
    bound, used = _bound_and_used(fn)
    free = used - bound - BUILTINS - {fn.name}
    g = getattr(func, "__globals__", {})
    nodes, needed, unresolved = [], [], []
    for n in sorted(free):
        if n not in g:
            unresolved.append(n)
            continue
        obj = g[n]
        if isinstance(obj, types.ModuleType):
            if obj.__name__.split(".")[0] in STDLIB_NAMES:
                asname = n if obj.__name__ != n else None
                nodes.append(ast.Import(names=[ast.alias(name=obj.__name__, asname=asname)]))
                needed.append(n)
            else:
                unresolved.append(n)  # a library module — don't pull in internals
            continue
        mod = getattr(obj, "__module__", None)
        objname = getattr(obj, "__name__", None)
        if mod and objname and mod.split(".")[0] in STDLIB_NAMES:
            asname = n if objname != n else None
            nodes.append(ast.ImportFrom(module=mod,
                                        names=[ast.alias(name=objname, asname=asname)], level=0))
            needed.append(n)
        else:
            unresolved.append(n)  # library-internal name, or a plain constant — skip
    return nodes, needed, unresolved


def make_self_contained(func):
    """Return (code, needed_imports, unresolved). code is None if not self-contained."""
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    fn = tree.body[0]
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None, [], ["<not a function def>"]
    fn.decorator_list = []
    nodes, need, unresolved = _resolve_imports(fn, func)
    if unresolved:
        return None, need, sorted(unresolved)
    insert_at = 0
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(getattr(fn.body[0], "value", None), ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        insert_at = 1  # keep the docstring first (TypeWright reads it for property detection)
    for node in reversed(nodes):
        fn.body.insert(insert_at, node)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), need, []


def analyze(funcname, code):
    body = json.dumps({"code": code, "function_name": funcname,
                       "include_fix_suggestion": False}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            return r.status, json.loads(r.read()), time.time() - t0
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw}
        return e.code, payload, time.time() - t0
    except Exception as e:
        return None, {"error": repr(e)}, time.time() - t0


def main():
    runnable, skipped = [], []
    for label, mod, name in CANDIDATES:
        try:
            func = getattr(mod, name)
            code, need, unresolved = make_self_contained(func)
        except Exception as e:
            skipped.append({"lib": label, "func": name, "reason": f"extract-error: {e!r}"})
            continue
        if code is None:
            skipped.append({"lib": label, "func": name,
                            "reason": "needs " + ", ".join(unresolved)})
        else:
            runnable.append({"lib": label, "func": name, "code": code, "imports": need})

    print(f"candidates={len(CANDIDATES)} runnable={len(runnable)} skipped={len(skipped)}")
    for s in skipped:
        print(f"  SKIP {s['lib']}.{s['func']}  ({s['reason']})")

    results = []

    def run_one(item):
        status, payload, secs = analyze(item["func"], item["code"])
        return {**item, "status": status, "payload": payload, "secs": round(secs, 1)}

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(run_one, it): it for it in runnable}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            n_bugs = len(r["payload"].get("bugs_found", []) or []) if r["status"] == 200 else "-"
            print(f"  [{r['status']}] {r['lib']}.{r['func']}  bugs={n_bugs}  {r['secs']}s")

    # rank: 200-with-bugs first (by bug count desc), then 200-clean, then errors
    def sortkey(r):
        if r["status"] == 200:
            nb = len(r["payload"].get("bugs_found", []) or [])
            return (0 if nb else 1, -nb, r["lib"], r["func"])
        return (2, 0, r["lib"], r["func"])

    results.sort(key=sortkey)

    with open(SP + "/sweep_results.json", "w") as f:
        json.dump({"results": results, "skipped": skipped}, f, indent=2)

    # markdown report
    lines = ["# Pilot sweep — candidate report", ""]
    total_cost = 0.0
    bugged = [r for r in results if r["status"] == 200 and (r["payload"].get("bugs_found") or [])]
    clean = [r for r in results if r["status"] == 200 and not (r["payload"].get("bugs_found") or [])]
    errs = [r for r in results if r["status"] != 200]
    for r in results:
        if r["status"] == 200:
            total_cost += float(r["payload"].get("metadata", {}).get("llm_cost_usd") or 0)
    lines.append(f"Ran {len(results)} self-contained functions · "
                 f"{len(bugged)} flagged candidates · {len(clean)} clean · {len(errs)} errored · "
                 f"~${total_cost:.4f} LLM cost · {len(skipped)} skipped (not self-contained)")
    lines.append("")
    lines.append("## Flagged candidates (need manual verification)")
    if not bugged:
        lines.append("_none_")
    for r in bugged:
        p = r["payload"]
        lines.append(f"\n### {r['lib']}.{r['func']}  ({len(p['bugs_found'])} finding(s))")
        # show the detected properties + confidence
        for d in (p.get("properties", {}) or {}).get("detected", []):
            lines.append(f"- _property_ `{d.get('relation')}`  "
                         f"[{d.get('property_class')}, conf {d.get('confidence')}]")
        for b in p["bugs_found"]:
            lines.append(f"- **{b.get('severity')}** · violates `{b.get('violated_property')}` · "
                         f"input `{str(b.get('failing_input'))[:120]}` · {b.get('error')} "
                         f"({b.get('test_name')})")
        lines.append("\n```python\n" + r["code"] + "\n```")
    lines.append("\n## Clean (no findings)")
    lines.append(", ".join(f"{r['lib']}.{r['func']}" for r in clean) or "_none_")
    lines.append("\n## Errored")
    for r in errs:
        lines.append(f"- [{r['status']}] {r['lib']}.{r['func']} — {str(r['payload'])[:200]}")
    lines.append("\n## Skipped (not self-contained — companion fn or module constant)")
    for s in skipped:
        lines.append(f"- {s['lib']}.{s['func']} — {s['reason']}")
    with open(SP + "/sweep_report.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nWROTE {SP}/sweep_report.md  (cost ~${total_cost:.4f})")


if __name__ == "__main__":
    main()
