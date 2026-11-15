import json

import httpx
import pytest

from backend.integrations.vision_relay import (
    AuthenticatedEventPoster,
    canonical_events,
    validate_destination,
)


def relay_event(**changes):
    return {"type": "object_appeared", "source": "vision", "ts": "2026-11-14T08:00:00Z",
            "zone": "kitchen", "object": None, "evidence_url": "/evidence/job-a/now.jpg",
            "confidence": .6, "size_cm": [10, 12, 14], **changes}


def test_wrapper_preserves_identity_before_removing_noncentral_images():
    original = [relay_event(), relay_event()]
    events = canonical_events(original)
    assert canonical_events(original) == events
    assert events[0]["id"] != events[1]["id"]
    assert events[0]["id"] != canonical_events([relay_event(evidence_url="/evidence/job-b/now.jpg")])[0]["id"]
    assert events[0]["evidence_url"] is None
    assert "size_cm" not in events[0]
    assert original[0]["evidence_url"] == "/evidence/job-a/now.jpg"
    assert canonical_events([relay_event(id="producer-id")])[0]["id"] == "producer-id"


@pytest.mark.parametrize("events", [[], {}, [relay_event(source="sensor")],
                                     [relay_event(confidence=True)], [relay_event(extra="bad")],
                                     [relay_event(evidence_url=123)], [relay_event()] * 101])
def test_wrapper_does_not_relax_schema(events):
    with pytest.raises(ValueError):
        canonical_events(events)


@pytest.mark.parametrize("url", ["http://192.168.1.1/events", "http://localhost/events",
                                "https://host/events?token=bad", "https://u:p@host/events",
                                "https://host/events#fragment", "https://host/events/",
                                "https://host:0/events", "file:///events", "not-a-url"])
def test_destination_validation(url):
    with pytest.raises(ValueError):
        validate_destination(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/events", "http://[::1]:8000/events",
                                "https://example.invalid/events"])
def test_supported_destinations(url):
    assert validate_destination(url) == url


def test_transport_auth_receipt_and_no_redirects():
    calls = []
    def serve(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-only"
        payload = json.loads(request.content)
        return httpx.Response(200, json={"events": payload, "durability": "local"})
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        poster = AuthenticatedEventPoster("http://127.0.0.1/events", "synthetic-only", client=client)
        assert poster(poster.destination, [relay_event()]) == 200
        assert len(calls) == 1
        with pytest.raises(OSError, match="differs"):
            poster("https://elsewhere.invalid/events", [relay_event()])
        assert len(calls) == 1


@pytest.mark.parametrize("response", [httpx.Response(307, headers={"Location": "https://evil.invalid"}),
                                    httpx.Response(401, text="sensitive-upstream-error"),
                                    httpx.Response(200, json={"events": []}),
                                    httpx.Response(200, content=b"x" * (1024*1024+1))])
def test_transport_rejects_nonacceptance_and_never_follows_redirect(response):
    calls = []
    def serve(request):
        calls.append(request)
        return response
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        poster = AuthenticatedEventPoster("http://127.0.0.1/events", "synthetic-only", client=client)
        with pytest.raises(OSError) as error:
            poster(poster.destination, [relay_event()])
        assert "sensitive" not in str(error.value)
        assert "synthetic-only" not in str(error.value)
        assert len(calls) == 1


def test_transport_timeout_is_compatible_with_relay_failure_handler():
    def fail(request):
        raise httpx.ReadTimeout("sensitive details")
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        poster = AuthenticatedEventPoster("http://127.0.0.1/events", "synthetic-only", client=client)
        with pytest.raises(OSError, match="unavailable") as error:
            poster(poster.destination, [relay_event()])
        assert "sensitive" not in str(error.value)
