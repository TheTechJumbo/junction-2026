from datetime import UTC, datetime

import pytest

from backend.answer_engine import RulesAnswerEngine
from backend.answer_engine.adapters import GeminiOpenAdapter


@pytest.mark.parametrize("question", [
    "Should I take two aspirin?", "Can I stop taking my tablets?",
    "What dose should I take?", "What treatment should I use?",
    "Do I have dementia?", "What does this pain mean?", "How many pills should I take?",
    "Should my mother start warfarin?", "Is taking 10mg of donepezil safe for me?",
    "How much metformin should I use?", "Should my father start a new medication?",
    "Can she stop her tablets?", "Is this medicine safe for my mum?",
    "How many tablets per day?", "Could we reduce the amount of this medicine?",
    "Which drugs should my father combine?", "Does warfarin interact with my medication?",
    "Should I increase an unknown medicine to 100 μg?", "Is 5 milligrams safe?",
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



@pytest.mark.parametrize(("question", "intent"), [
    ("Did I already take my pills?", "pills"),
    ("When was the pill box opened?", "pills"),
    ("Was the kitchen medication box opened today?", "pills"),
    ("Where is the pill box?", "navigation"),
    ("Is it safe to open the door?", "door"),
    ("Can my mother start the washing machine?", "open"),
    ("How much paint should I use?", "open"),
    ("Can she stop the washing machine?", "open"),
])
def test_decision_guard_preserves_observations_navigation_and_nonmedical_questions(question, intent):
    assert RulesAnswerEngine().classify(question) == intent
