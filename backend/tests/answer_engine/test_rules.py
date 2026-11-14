import builtins
import json
from datetime import date, datetime, time, timedelta
from itertools import permutations
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.answer_engine import RulesAnswerEngine, RulesConfig
from contracts.models import Alert, Event, QuestionRecord

NOW = datetime.fromisoformat("2026-11-14T10:00:00+02:00")
DAY = NOW.date()
TZ = "Europe/Helsinki"


def event(id="pills", type="pillbox_open", ts="2026-11-14T08:00:00+02:00", **kwargs):
    return Event(id=id, type=type, ts=ts, source=kwargs.pop("source", "sensor"),
                 object=kwargs.pop("object", "pillbox"), zone=kwargs.pop("zone", "kitchen"),
                 evidence_url=kwargs.pop("evidence_url", None),
                 confidence=kwargs.pop("confidence", 1), **kwargs)


@pytest.fixture
def engine():
    return RulesAnswerEngine(clock=lambda: NOW)


@pytest.mark.parametrize(("question", "intent"), [
    ("Did I already take my pills?", "pills"), ("Have I taken my medication?", "pills"),
    ("Did I take a tablet?", "pills"), ("my meds", "pills"), ("PILLBOX?", "pills"),
    ("Did I lock the door?", "door"), ("Is the front door closed?", "door"),
    ("Is it locked?", "door"), ("Where is the pill box?", "navigation"),
    ("Where's the front door?", "navigation"), ("How do I get to the bathroom?", "navigation"),
    ("Directions to bathroom", "navigation"), ("Where am I?", "navigation"),
    ("What is the weather?", "open"), ("What is a doorstep?", "open"),
    ("What does pillory mean?", "open"), ("Tell me a joke", "open"),
])
def test_classification(engine, question, intent):
    assert engine.classify(question) == intent


def test_contract_fixture_answer(engine):
    fixture = Path("contracts/fixtures/answer.json")
    expected = json.loads(fixture.read_text())
    observations = [Event.model_validate(e) for e in json.loads(
        Path("contracts/fixtures/events.json").read_text())]
    result = engine.answer("Did I take my pills?", observations,
                           datetime.fromisoformat("2026-11-14T08:20:00+02:00"), TZ)
    assert result.model_dump(mode="json") == expected


def test_pills_opening_never_claims_ingestion(engine):
    result = engine.answer("Did I take my pills?", [event()], NOW, TZ)
    assert result.answer == ("The pill box was opened at 08:00. "
                             "I cannot confirm that the pills were taken.")
    assert result.evidence == [event()]


@pytest.mark.parametrize("question", ["Did I take my pills?", "Did I lock the door?"])
@pytest.mark.parametrize("observations", [[], [event(source="vision")],
    [event(source="voice")], [event(object=None)], [event(object="stove")],
    [event(confidence=0.1)], [event(ts="2026-11-13T08:00:00+02:00")],
    [event(ts="2026-11-14T10:00:00.000001+02:00")]])
def test_missing_unreliable_or_future_is_uncertain(engine, question, observations):
    result = engine.answer(question, observations, NOW, TZ)
    assert result.evidence == []
    assert "cannot confirm" in result.answer
    assert not result.answer.startswith("Yes")


@pytest.mark.parametrize("question", ["Did I take pills yesterday?", "Did I lock the door last night?"])
def test_unsupported_historical_request_does_not_use_today(engine, question):
    result = engine.answer(question, [event()], NOW, TZ)
    assert result.evidence == []
    assert "today" in result.answer


def test_latest_pill_opening_independent_of_arrival_order(engine):
    first = event()
    last = event(id="last", ts="2026-11-14T08:40:00+02:00")
    for events in permutations([first, last, event(id="future", ts=NOW + timedelta(seconds=1))]):
        result = engine.answer("pills?", events, NOW, TZ)
        assert result.evidence == [last]
        assert "08:40" in result.answer


@pytest.mark.parametrize("type,state", [("door_open", "opened"), ("door_closed", "closed")])
def test_door_observation_not_lock_proof(engine, type, state):
    door = event(type=type, object="front_door", zone="entrance")
    result = engine.answer("Did I lock the door?", [door], NOW, TZ)
    assert f"was {state} at 08:00" in result.answer
    assert "cannot confirm whether it is locked" in result.answer
    assert result.evidence == [door]


def test_later_opening_overrides_close(engine):
    closed = event(id="closed", type="door_closed", object="front_door")
    opened = event(id="opened", type="door_open", object="front_door",
                   ts="2026-11-14T08:01:00+02:00")
    for events in permutations([opened, closed]):
        assert engine.answer("door?", events, NOW, TZ).evidence == [opened]


def test_later_close_overrides_opening(engine):
    opened = event(id="opened", type="door_open", object="front_door")
    closed = event(id="closed", type="door_closed", object="front_door",
                   ts="2026-11-14T08:01:00+02:00")
    assert engine.answer("door?", [closed, opened], NOW, TZ).evidence == [closed]


def test_simultaneous_door_contradiction_is_not_broken_by_id(engine):
    opened = event(id="a", type="door_open", object="front_door")
    closed = event(id="z", type="door_closed", object="front_door")
    for events in permutations([closed, opened]):
        result = engine.answer("door?", events, NOW, TZ)
        assert "conflict" in result.answer
        assert result.evidence == [opened, closed]


@pytest.mark.parametrize("object", [None, "back_door", "fridge", "bathroom_door"])
def test_door_wrong_object(engine, object):
    result = engine.answer("door?", [event(type="door_closed", object=object)], NOW, TZ)
    assert not result.evidence


def test_named_other_door_not_answered_with_front_door(engine):
    result = engine.answer("Did I close the back door?",
                           [event(type="door_closed", object="front_door")], NOW, TZ)
    assert not result.evidence
    assert "that door" in result.answer


def test_per_intent_max_age():
    engine = RulesAnswerEngine(config=RulesConfig(pills_max_age=timedelta(minutes=30),
                                                door_max_age=timedelta(minutes=30)))
    assert not engine.answer("pills?", [event()], NOW, TZ).evidence
    assert not engine.answer("door?", [event(type="door_open", object="front_door")], NOW, TZ).evidence


def test_max_age_boundary_inclusive():
    engine = RulesAnswerEngine(config=RulesConfig(pills_max_age=timedelta(hours=2)))
    assert engine.answer("pills?", [event()], NOW, TZ).evidence


def test_local_calendar_day_and_rendered_time():
    engine = RulesAnswerEngine()
    now = datetime.fromisoformat("2026-11-14T00:30:00+02:00")
    observation = event(ts="2026-11-13T22:10:00Z")
    result = engine.answer("pills?", [observation], now, TZ)
    assert result.evidence == [observation]
    assert "00:10" in result.answer
    assert not engine.answer("pills?", [observation], now, "UTC").evidence == []  # UTC today Nov13
    previous_day = event(ts="2026-11-13T21:59:59Z")
    assert not engine.answer("pills?", [previous_day], now, TZ).evidence


def test_dst_fold_latest_is_ordered_by_instant():
    tz = ZoneInfo(TZ)
    first = event(id="first", type="door_open", object="front_door",
                  ts=datetime(2026, 10, 25, 3, 50, tzinfo=tz, fold=0))
    second = event(id="second", type="door_closed", object="front_door",
                   ts=datetime(2026, 10, 25, 3, 10, tzinfo=tz, fold=1))
    now = datetime(2026, 10, 25, 4, tzinfo=tz)
    result = RulesAnswerEngine().answer("door?", [second, first], now, TZ)
    assert result.evidence == [second]
    assert "closed at 03:10" in result.answer


@pytest.mark.parametrize(("timestamp", "expected"), [
    ("2026-11-14T00:00:00+02:00", 1), ("2026-11-14T05:59:59+02:00", 1),
    ("2026-11-14T06:00:00+02:00", 0), ("2026-11-14T22:59:59+02:00", 0),
    ("2026-11-14T23:00:00+02:00", 1), ("2026-11-14T23:59:59+02:00", 1),
    ("2026-11-13T23:59:59+02:00", 0), ("2026-11-15T00:00:00+02:00", 0),
])
def test_half_open_night_day_boundaries(timestamp, expected):
    engine = RulesAnswerEngine(clock=lambda: NOW + timedelta(days=1))
    alerts = engine.derive_alerts([event(type="door_open", object="front_door", ts=timestamp)], DAY, TZ)
    assert len(alerts) == expected


@pytest.mark.parametrize("overrides", [
    {"source": "vision"}, {"source": "voice"}, {"object": None},
    {"object": "fridge"}, {"confidence": 0.2}, {"type": "door_closed"},
])
def test_only_reliable_sensor_opening_creates_alert(engine, overrides):
    params = dict(type="door_open", object="front_door", ts="2026-11-14T03:00:00+02:00")
    params.update(overrides)
    assert not engine.derive_alerts([event(**params)], DAY, TZ)


def test_future_alerts_filtered(engine):
    future = event(type="door_open", object="front_door", ts="2026-11-14T23:10:00+02:00")
    assert not engine.derive_alerts([future], DAY, TZ)


def test_stable_alerts_and_replays(engine):
    e = event(type="door_open", object="front_door", ts="2026-11-14T03:10:00+02:00")
    once = engine.derive_alerts([e], DAY, TZ)
    twice = engine.derive_alerts([e, e], DAY, TZ)
    assert once == twice
    assert once[0].event_ids == [e.id]
    assert "03:10" in once[0].message


@pytest.mark.parametrize("day,timestamps", [
    (date(2026, 10, 25), ["2026-10-25T03:10:00+03:00", "2026-10-25T03:10:00+02:00"]),
    (date(2026, 3, 29), ["2026-03-29T02:59:00+02:00", "2026-03-29T04:00:00+03:00"]),
])
def test_dst_night_alerts(engine, day, timestamps):
    events = [event(id=f"e{i}", type="door_open", object="front_door", ts=ts)
              for i, ts in enumerate(timestamps)]
    alerts = engine.derive_alerts(events, day, TZ)
    assert len(alerts) == 2
    assert len({a.id for a in alerts}) == 2


def test_configurable_night_interval_and_disable():
    e = event(type="door_open", object="front_door")
    configured = RulesAnswerEngine(config=RulesConfig(night_start=time(7), night_end=time(9)),
                                   clock=lambda: NOW)
    assert configured.derive_alerts([e], DAY, TZ)
    disabled = RulesAnswerEngine(config=RulesConfig(night_alerts_enabled=False), clock=lambda: NOW)
    assert not disabled.derive_alerts([e], DAY, TZ)
    empty = RulesAnswerEngine(config=RulesConfig(night_start=time(7), night_end=time(7)),
                              clock=lambda: NOW)
    assert not empty.derive_alerts([e], DAY, TZ)


def test_summary_counts_only_day_sensor_observations_and_questions(engine):
    opening = event(id="door", type="door_open", object="front_door",
                    ts="2026-11-14T03:10:00+02:00")
    events = [event(), event(), opening, event(id="vision", source="vision"),
              event(id="wrong", object="fridge"), event(id="future", ts=NOW + timedelta(seconds=1))]
    questions = [QuestionRecord(id=str(i), intent="pills", question="pills?", ts=NOW)
                 for i in range(2)]
    questions += [questions[0], QuestionRecord(id="future", intent="door", question="door?",
                                              ts=NOW + timedelta(seconds=1))]
    alerts = engine.derive_alerts(events, DAY, TZ)
    fake = Alert(id="fake", type="vision", ts=NOW, message="unsafe", event_ids=["vision"])
    text = engine.summarize(events, [*alerts, *alerts, fake], questions, DAY, TZ)
    assert "1 pill box opening(s) and 1 front door opening(s)" in text
    assert "1 night door alert(s)" in text
    assert "2 question(s) were recorded (pills: 2)" in text
    assert engine.summarize(list(reversed(events)), alerts, questions, DAY, TZ) == text


def test_summary_empty_day(engine):
    text = engine.summarize([], [], [], DAY, TZ)
    assert "0 pill box" in text and "0 question(s)" in text


def test_conflicting_same_id_events_are_not_asserted(engine):
    first, conflict = event(), event(object="fridge")
    assert not engine.answer("pills?", [first, conflict], NOW, TZ).evidence


def test_core_never_calls_network_or_optional_adapters(engine, monkeypatch):
    calls = []
    class Exploding:
        def answer(self, question):
            calls.append(question)
            raise AssertionError("Optional dependency called")
    engine.open_adapter = engine.navigation_adapter = Exploding()
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network access"))
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name not in {"httpx", "google", "requests"}
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert engine.answer("pills?", [event()], NOW, TZ).evidence
    engine.answer("door?", [], NOW, TZ)
    engine.derive_alerts([], DAY, TZ)
    engine.summarize([], [], [], DAY, TZ)
    assert calls == []


def test_aware_now_required(engine):
    with pytest.raises(ValueError, match="aware"):
        engine.answer("pills?", [], datetime(2026, 11, 14), TZ)


def test_default_disabled_adapters_and_exception_fallback(engine):
    for question in ["Where am I?", "What is a cat?"]:
        assert engine.answer(question, [], NOW, TZ).evidence == []
    class Broken:
        def answer(self, question):
            raise RuntimeError("secret upstream error")
    engine.open_adapter = Broken()
    assert "secret" not in engine.answer("Hello", [], NOW, TZ).answer


def test_evidence_attachment_replay_preserves_observation(engine):
    attached = event(evidence_url="/artifacts/synthetic.png")
    for events in permutations([event(), attached]):
        result = engine.answer("pills?", events, NOW, TZ)
        assert result.evidence == [attached]
        text = engine.summarize(events, [], [], DAY, TZ)
        assert "1 pill box opening(s)" in text


def test_vision_cannot_override_or_create_sensor_truth(engine):
    closed = event(type="door_closed", object="front_door")
    vision = event(id="vision-open", type="door_open", object="front_door", source="vision",
                   ts="2026-11-14T09:00:00+02:00")
    result = engine.answer("Did I lock the door?", [vision, closed], NOW, TZ)
    assert result.evidence == [closed]
    assert "closed at 08:00" in result.answer
    assert not engine.derive_alerts([vision], DAY, TZ)
