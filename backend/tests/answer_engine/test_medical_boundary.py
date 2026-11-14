from datetime import UTC, datetime

import pytest

from backend.answer_engine import RulesAnswerEngine
from backend.answer_engine.adapters import GeminiOpenAdapter


@pytest.mark.parametrize("question", [
    "Should I take two aspirin?", "Can I stop taking my tablets?",
    "What dose should I take?", "What treatment should I use?",
    "Do I have dementia?", "What does this pain mean?", "How many pills should I take?",
])
def test_medical_decisions_never_reach_optional_provider(question):
    class Forbidden:
        def answer(self, question):
            pytest.fail("A medical decision must not reach any optional provider")

    engine = RulesAnswerEngine(open_adapter=Forbidden(), navigation_adapter=Forbidden())
    answer = engine.answer(question, [], datetime.now(UTC), "Europe/Helsinki")
    assert engine.classify(question) == "medical"
    assert "cannot make medical or dosing decisions" in answer.answer
    assert answer.evidence == []
    # Direct adapter use preserves the same routing guard before any network call.
    adapter = GeminiOpenAdapter(api_key="synthetic-key", model="synthetic-model")
    adapter._bounded = lambda operation: pytest.fail("No provider request is allowed")
    assert adapter.answer(question) is None


def test_observation_question_remains_sensor_intent():
    assert RulesAnswerEngine().classify("Did I already take my pills?") == "pills"
