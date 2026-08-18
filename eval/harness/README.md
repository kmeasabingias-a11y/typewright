# The benchmark harness

Three scripts. `sweep.py` is the core transform and API client; `sweep2.py` is the driver
that discovers functions and runs the sweep; `bench_analyze.py` scores a completed run
against hand-verified ground truth.

## What `sweep.py` actually does

The non-obvious part of this benchmark is getting real library functions into a sandbox
*without* changing them. For each candidate:

1. Take the **real source** via `inspect.getsource` — not a paraphrase, not a stub.
2. Drop decorators.
3. Inline the stdlib imports the function needs, resolved through the function's actual
   `__globals__` — so `from datetime import datetime` is reproduced faithfully rather than
   guessed at. (Guessing here is what produced five phantom bugs in the June run; see §7 of
   the report.)
4. **Skip the function entirely** if anything else is still free — a companion function or a
   module-level constant. Those would crash spuriously in the sandbox and are not a signal.

Only ~34% of discovered functions survive step 4. That ratio is itself a finding.

## Re-running it

```bash
# 0) Install the target libraries into ./libs (not checked in)
WORKDIR=$(pwd)
uv pip install --target "$WORKDIR/libs" \
    inflection boltons humanize pyhumps roman stringcase more-itertools

# 1) Start Kestrel (the sandboxed test executor)
KESTREL_EXECUTOR_DOCKER_IMAGE=typewright-test-runtime:0.2 \
KESTREL_EXECUTE_TIMEOUT_SECONDS=60 KESTREL_EXECUTE_OUTPUT_CAP_BYTES=262144 \
uv run --directory /path/to/Kestrel uvicorn kestrel.app:create_app --factory --port 8000

# 2) Start TypeWright, with ALL operator caps lifted — see the warning below
TYPEWRIGHT_RATE_LIMIT_ENABLED=false \
TYPEWRIGHT_MAX_DAILY_COST_USD=50 \
TYPEWRIGHT_MAX_MONTHLY_COST_USD=200 \
uv run --directory /path/to/TypeWright uvicorn typewright.main:app --port 8001

# 3) Run the sweep, then score it
python3 sweep2.py
python3 bench_analyze.py
```

`SP` (the working directory) defaults to this script's directory; override with
`TW_EVAL_WORKDIR`. The target libraries are expected under `$SP/libs`.

## ⚠️ Lift every operator cap before an evaluation batch

**This has gone wrong twice, in two different ways, and both times it looked like a finding
rather than a bug.**

- **June:** the rate limiter (10 req/min/IP) throttled the operator's own batch.
- **August:** the daily spend cap returned HTTP 503 for 20 of 49 functions — including all
  three ground-truth functions — after a previous run left the counter at $1.88 against a
  $3.00 limit. The result read as dramatic non-determinism until the status codes were
  actually checked.

Both times a production safety guard worked exactly as designed and silently poisoned the
measurement. Lift the rate limit **and** both spend caps, and check them again after each
run — the daily counter persists in `runs.db`.

**Known gap:** `sweep2.py` currently records a non-200 response as a result rather than
aborting. It should abort loudly on any non-200. A 503 is not a data point.

## Cost

Roughly **$0.037 per function**, so a full 49-function sweep is ~$1.85. Budget ~$4 for a
two-run campaign. Costs scale with the model tier configured on the TypeWright side.
