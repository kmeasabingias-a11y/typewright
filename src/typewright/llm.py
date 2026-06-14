"""Shared LLM plumbing: the Instructor-wrapped LiteLLM client both analysis steps use.

Lifted out of ``inference.py`` once a second LLM caller (Phase 3 strategy generation)
appeared — the split DECISIONS.md D18 deferred to "the Phase 3 trigger" (D27). One place
for the provider/Instructor wiring; ``inference.py`` and ``generation.py`` each keep a thin
``_client()`` over this so tests can still stub per-module.
"""

from __future__ import annotations

import instructor
from litellm import completion


def build_client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client (D13/D17)."""
    return instructor.from_litellm(completion)