"""Tests for the shared LLM call shape in ``llm.complete`` — the request kwargs every stage sends.

The per-stage tests (test_inference / test_generation / test_testgen) cover what each stage asks
for; this file covers the parts of the request that are the *provider contract*, where getting it
wrong fails live but not under a hand-written fake.
"""


def test_temperature_is_sent_only_when_configured():
    """D65: omit `temperature` by default (current Claude models 400 on it); send it if pinned."""
    from typewright.config import Settings
    from typewright.llm import complete
    from typewright.models import PropertyDetection

    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return PropertyDetection(detected=[])

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    def run(temperature):
        fake = FakeClient()
        complete(
            lambda: fake,
            stage="property_detection",
            settings=Settings(anthropic_api_key="k", llm_temperature=temperature),
            model="anthropic/claude-sonnet-5",
            response_model=PropertyDetection,
            messages=[{"role": "user", "content": "hi"}],
        )
        return fake.chat.completions.kwargs

    assert "temperature" not in run(None)
    assert run(0.0)["temperature"] == 0.0
