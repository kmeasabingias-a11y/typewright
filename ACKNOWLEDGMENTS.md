# Acknowledgments

## Prior art: *Agentic Property-Based Testing*

TypeWright is a productization of the LLM-driven property-based testing technique
introduced in:

> **Agentic Property-Based Testing: Finding Bugs Across the Python Ecosystem**
> Muhammad Maaz, Liam DeVoe, Zac Hatfield-Dodds, Nicholas Carlini.
> arXiv:2510.09907, 10 October 2025. <https://arxiv.org/abs/2510.09907>

That paper demonstrates an LLM agent that analyzes Python modules, infers the properties
a function should satisfy from its code and documentation, synthesizes property-based
tests, and runs them to find real bugs — reporting a 56% validity rate across 100 popular
packages (86% among top-ranked reports), with bugs identified in projects including
NumPy and several patches merged upstream. The core idea TypeWright builds on is theirs: **use an LLM to recognize a
function's intended properties, then let property-based testing do the falsifying.**

### What TypeWright takes, and what it changes

TypeWright is **a deliberate productization in a new form factor, not novel research.**
We make no claim to the underlying technique.

- **Borrowed:** the central pipeline — LLM-inferred properties → generated
[Hypothesis](https://hypothesis.readthedocs.io/) tests → execution → counter-example
reporting.
- **Different form factor:** TypeWright ships as a GitHub App that comments on pull
requests and as a paste-a-function web demo, rather than a research harness run across a
package corpus.
- **A deliberate guardrail:** where the paper infers properties partly from a function's
*body*, TypeWright restricts detection to *recognizing well-known property classes* from
the name, signature, type hints, and docstring — never from what the body computes — to
avoid a circular oracle that would only re-assert the implementation. This is an
engineering trade-off for false-positive control, not an improvement on the research
(see `DECISIONS.md`, D23/D26).
- **Architecture:** TypeWright cleanly separates the AI pipeline from sandboxed code
execution, which runs in a standalone service (Kestrel). The cost controls, rate
limiting, tracing, and fix-verification loop are our own product engineering.

### Citation

```bibtex
@misc{maaz2025agentic,
title         = {Agentic Property-Based Testing: Finding Bugs Across the Python Ecosystem},
author        = {Maaz, Muhammad and DeVoe, Liam and Hatfield-Dodds, Zac and Carlini, Nicholas},
year          = {2025},
eprint        = {2510.09907},
archivePrefix = {arXiv},
url           = {https://arxiv.org/abs/2510.09907}
}
```

## Hypothesis

Every test TypeWright generates is a [Hypothesis](https://hypothesis.readthedocs.io/)
property-based test. Hypothesis does the actual work of generating inputs, shrinking
failures to minimal counter-examples, and replaying them. TypeWright would not exist
without it. Our thanks to the Hypothesis maintainers and contributors — two of whom
(Zac Hatfield-Dodds and Liam DeVoe) are also authors of the paper above.

## Tooling

TypeWright also stands on:

- [FastAPI](https://fastapi.tiangolo.com/) and [Pydantic](https://docs.pydantic.dev/) —
the HTTP service and structured models.
- [httpx](https://www.python-httpx.org/) — the HTTP client TypeWright uses to drive
Kestrel and the GitHub API.
- [LiteLLM](https://github.com/BerriAI/litellm) and
[Instructor](https://python.useinstructor.com/) — structured, validated LLM calls.
- [arq](https://arq-docs.helpmanual.io/) and [Redis](https://redis.io/) — the background
worker queue behind the GitHub App.
- [uv](https://docs.astral.sh/uv/) — packaging and environments.