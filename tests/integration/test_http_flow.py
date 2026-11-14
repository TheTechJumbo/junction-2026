"""Black-box acceptance checks against an explicitly configured local test server.

Run with JUNCTION_TEST_BASE_URL and JUNCTION_TEST_API_TOKEN, never a production URL.
The caller owns starting/stopping the application and a disposable data directory.
"""
import io
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from PIL import Image

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    base = os.environ.get("JUNCTION_TEST_BASE_URL")
    token = os.environ.get("JUNCTION_TEST_API_TOKEN")
    if not base or not token:
        pytest.skip("set JUNCTION_TEST_BASE_URL and JUNCTION_TEST_API_TOKEN")
    if urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("acceptance tests only permit a disposable loopback test server")
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=5) as c:
        yield c


def event(kind="pillbox_open", source="sensor", ts=None):
    return {
        "id": f"synthetic-acceptance-{uuid.uuid4().hex}", "type": kind, "source": source,
        "ts": (ts or datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "zone": "entrance" if kind.startswith("door") else "kitchen",
        "object": "front_door" if kind.startswith("door") else "pillbox",
        "evidence_url": None, "confidence": 1,
    }


def test_authenticated_ingest_answer_timeline_and_question_counts(client):
    item = event()
    accepted = client.post("/events", json=item)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["events"][0]["id"] == item["id"]
    assert accepted.json()["durability"] in {"local", "replicated"}
    answer = client.post("/ask", json={"question": "Did I already take my pills?"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert "open" in body["answer"].lower()
    assert not body["answer"].lower().startswith("yes")
    assert item["id"] in {e["id"] for e in body["evidence"]}
    day = datetime.now(ZoneInfo("Europe/Helsinki")).date().isoformat()
    timeline = client.get("/timeline", params={"day": day})
    assert timeline.status_code == 200, timeline.text
    assert item["id"] in {e["id"] for e in timeline.json()["events"]}
    assert any(q["intent"] == "pills" and q["count"] >= 1 for q in timeline.json()["questions"])
    summary = client.get("/summary", params={"day": day})
    assert summary.status_code == 200 and summary.json()["text"]


def test_duplicate_conflict_and_atomic_batch(client):
    original = event()
    assert client.post("/events", json=original).status_code == 200
    assert client.post("/events", json=original).status_code == 200
    assert client.post("/events", json=original | {"zone": "conflict"}).status_code == 409
    new = event()
    batch = client.post("/events", json=[new, original | {"zone": "conflict"}])
    assert batch.status_code == 409
    listing = client.get("/events", params={"since": new["ts"], "limit": 100}).json()
    assert new["id"] not in {e["id"] for e in listing["events"]}


def test_vision_cannot_create_night_door_alert(client):
    tz = ZoneInfo("Europe/Helsinki")
    night = (datetime.now(tz) - timedelta(days=1)).replace(hour=3, minute=10, second=0)
    vision, sensor = event("door_open", "vision", night), event("door_open", ts=night)
    assert client.post("/events", json=[vision, sensor]).status_code == 200
    result = client.get("/timeline", params={"day": night.date().isoformat()}).json()
    alert_ids = {eid for a in result["alerts"] for eid in a["event_ids"]}
    assert sensor["id"] in alert_ids
    assert vision["id"] not in alert_ids


def test_evidence_attachment_is_authenticated_and_retry_safe(client):
    item = event()
    assert client.post("/events", json=item).status_code == 200
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="JPEG")
    attached = client.post(f"/events/{item['id']}/evidence", content=buffer.getvalue(),
                           headers={"Content-Type": "image/jpeg"})
    assert attached.status_code == 200, attached.text
    path = attached.json()["evidence_url"]
    assert path.startswith("/") and not path.startswith("//")
    image = client.get(path)
    assert image.status_code == 200
    Image.open(io.BytesIO(image.content)).verify()
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    assert client.post("/events", json=item).status_code == 200


def test_unauthenticated_calls_and_malformed_input(client):
    assert client.get("/timeline", headers={"Authorization": ""}).status_code == 401
    for bad in [event() | {"source": "rules"}, event() | {"confidence": True}, []]:
        result = client.post("/events", json=bad)
        assert result.status_code == 400
        assert set(result.json()) == {"error"}
