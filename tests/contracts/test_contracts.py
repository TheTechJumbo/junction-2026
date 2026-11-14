import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from contracts.models import Answer, Event, EventPage, IngestResponse, Summary, Timeline

ROOT = Path(__file__).resolve().parents[2] / "contracts"


def fixture(name):
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text())


def validate_schema(name, value):
    schema = json.loads((ROOT / "schemas" / f"{name}.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_canonical_fixtures_validate_in_python_and_json_schema():
    for event in fixture("events"):
        parsed = Event.model_validate(event)
        validate_schema("Event", event)
        assert parsed.ts.utcoffset() is not None


@pytest.mark.parametrize("change", [
    {"source": "rules"}, {"source": "uno-q"}, {"confidence": -0.1},
    {"confidence": 1.1}, {"confidence": True}, {"confidence": "0.5"},
    {"confidence": float("nan")}, {"confidence": float("inf")},
    {"ts": "2026-11-14T08:00:00"}, {"id": "../bad"},
    {"ts": 1794636000}, {"kind": "sensor"}, {"pos_3d": [0, 0, 0]},
])
def test_reject_invalid_event(change):
    with pytest.raises(ValidationError):
        Event.model_validate(fixture("events")[0] | change)


@pytest.mark.parametrize("name,model", [
    ("answer", Answer), ("timeline", Timeline), ("event-page", EventPage),
    ("ingest", IngestResponse), ("summary", Summary),
])
def test_response_examples(name, model):
    value = fixture(name)
    model.model_validate(value)
    validate_schema(model.__name__, value)


def test_legacy_vision_requires_explicit_adapter():
    for item in fixture("legacy-vision"):
        with pytest.raises(ValidationError):
            Event.model_validate(item)
        assert item["source"] == "vision"
        assert item["object"] is None


def test_synthetic_fixtures_do_not_claim_confirmations():
    events = fixture("events")
    assert all(e["id"].startswith("synthetic-") for e in events)
    assert not {"pills_taken", "door_locked"} & {e["type"] for e in events}
    local = Event.model_validate(events[0]).ts.astimezone(ZoneInfo("Europe/Helsinki"))
    assert (local.hour, local.minute) == (8, 0)


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_contract_day_intervals_account_for_dst(day, hours):
    local_day = datetime.fromisoformat(day).date()
    tz = ZoneInfo("Europe/Helsinki")
    start = datetime.combine(local_day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz)
    assert (end.astimezone(UTC) - start).total_seconds() == hours * 3600
