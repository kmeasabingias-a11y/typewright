# TypeWright

AI-powered property-based test generator for Python.

TypeWright detects which well-known property classes a Python function should satisfy
(round-trip, idempotence, metamorphic relations, invariants, and more), generates
[Hypothesis](https://hypothesis.readthedocs.io/) property-based tests that assert them,
executes those tests in an isolated sandbox ([Kestrel](https://github.com/)), and reports
the exact inputs that break the function.

> **Status:** under construction. Currently at **Phase 2 — Property Detection** (LLM
> recognizes property classes; HTTP service + AST parsing in place; sandbox execution not
> yet wired).

The maintained specification — goal, architecture, phase plan, and API — lives in
[`PROJECT_BRIEF.md`](PROJECT_BRIEF.md); design decisions and their rationale are in
[`DECISIONS.md`](DECISIONS.md). (`TypeWright_Project_Brief.pdf` is the original founding
spec, kept for the record; where they differ, `PROJECT_BRIEF.md` wins.)

## Prior art

TypeWright productizes the LLM-driven property-based testing technique published by
Anthropic in *Agentic Property-Based Testing* (arXiv:2510.09907, Oct 2025). It is a
deliberate productization in a new form factor (GitHub App + web demo), not novel
research. See `ACKNOWLEDGMENTS.md` (added at launch) for full attribution.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```sh
uv sync          # create the venv and install deps (runtime + dev)
uv run pytest    # run the test suite