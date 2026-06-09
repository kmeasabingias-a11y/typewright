# TypeWright

AI-powered property-based test generator for Python.

TypeWright infers what a Python function is supposed to do, generates
[Hypothesis](https://hypothesis.readthedocs.io/) property-based tests, executes them in
an isolated sandbox ([Kestrel](https://github.com/)), and reports the exact inputs that
break the function.

> **Status:** under construction. Currently at **Phase 1 — Foundation** (HTTP service +
> AST parsing; no LLM or sandbox integration yet).

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