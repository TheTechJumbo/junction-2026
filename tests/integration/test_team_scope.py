"""Independent HTTP regressions for observation-only answers and target isolation.

Real API, SQLite and rules; no engine mock, network model, speech provider or hardware.
Known failures are deliberately assertions, not expected-failure markers.
"""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.speech import ElevenLabsSpeechService

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def observation(id="patient-box", **changes):
    return dict(id=id, type="pillbox_open", source="sensor", object="pillbox", zone="kitchen",
                ts="2026-11-14T08:00:00+02:00", evidence_url=None, confidence=1) | changes


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(api_token="synthetic-review", local_data_dir=tmp_path),
                     clock=lambda: NOW, speech=ElevenLabsSpeechService())
    with TestClient(app) as value:
        value.headers["Authorization"] = "Bearer synthetic-review"
        yield value


@pytest.mark.parametrize("question", [
    "Did I take my wife's pills?", "Did my husband take his pills?",
    "Did I take pills in the bedroom?", "Did I take pills on 2026-11-13?",
    "Did I take pills last Monday?", "Did I take pills two days ago?",
])
def test_other_person_room_or_day_does_not_reuse_default_observation(client, question):
    assert client.post("/events", json=observation()).status_code == 200
    answer = client.post("/ask", json={"question": question})
    assert answer.status_code == 200
    assert answer.json()["evidence"] == [], answer.json()


@pytest.mark.parametrize("event", [
    observation(zone="bedroom"),
    observation(type="door_closed", object="front_door", zone="bathroom"),
])
def test_wrong_zone_cannot_support_default_target_answer(client, event):
    assert client.post("/events", json=event).status_code == 200
    question = "Did I take my pills?" if event["type"] == "pillbox_open" else "Did I lock the door?"
    result = client.post("/ask", json={"question": question})
    assert result.json()["evidence"] == [], result.json()


def test_wrong_zone_door_cannot_raise_front_door_alert_or_count_in_summary(client):
    wrong = observation(type="door_open", object="front_door", zone="bathroom",
                         ts="2026-11-14T03:10:00+02:00")
    assert client.post("/events", json=wrong).status_code == 200
    timeline = client.get("/timeline", params={"day": "2026-11-14"}).json()
    assert timeline["alerts"] == [], timeline
    assert "0 front door openings" in client.get("/summary").json()["text"]


@pytest.mark.parametrize("event_type", ["pillbox_open", "pills_taken", "medication_taken"])
def test_sensor_or_photo_never_proves_medication_ingestion(client, event_type):
    assert client.post("/events", json=observation(type=event_type)).status_code == 200
    result = client.post("/ask", json={"question": "Did I take my pills?"}).json()
    assert not result["answer"].lower().startswith(("yes", "you took"))
    assert "cannot confirm" in result["answer"]
    assert (len(result["evidence"]) == 1) == (event_type == "pillbox_open")
    assert client.post("/speak", json={"text": result["answer"]}).status_code == 503
    timeline = client.get("/timeline").json()
    assert timeline["questions"] == [{"intent": "pills", "count": 1, "last_ts": NOW.isoformat().replace("+00:00", "Z")}]


def test_latest_state_and_repeat_counts_use_real_api_and_storage(client):
    payload = [observation(id="closed", type="door_closed", object="front_door", zone="entrance"),
               observation(id="opened", type="door_open", object="front_door", zone="entrance",
                           ts="2026-11-14T09:00:00+02:00"),
               observation(id="vision", type="door_closed", object="front_door", zone="entrance",
                           source="vision", ts="2026-11-14T10:00:00+02:00")]
    assert client.post("/events", json=payload).status_code == 200
    for _ in range(2):
        answer = client.post("/ask", json={"question": "Did I lock the door?"}).json()
        assert "opened at 09:00" in answer["answer"]
        assert "cannot confirm" in answer["answer"]
        assert [e["id"] for e in answer["evidence"]] == ["opened"]
    assert client.get("/timeline").json()["questions"][0]["count"] == 2
    assert "2 questions" in client.get("/summary").json()["text"]
