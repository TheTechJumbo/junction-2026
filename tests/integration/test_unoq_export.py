import json

import httpx
import pytest

from backend.integrations.unoq_export import ExportUnavailable, transfer


def observation(index=0, **changes):
    return dict(id=f"board-{index}", type="pillbox_open", source="sensor",
                ts="2026-11-14T08:00:00+02:00", zone="kitchen", object="pillbox",
                evidence_url=None, confidence=1) | changes


def test_transfer_authenticates_both_services_batches_and_never_fetches_images():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            assert request.url.path == "/export/events"
            assert request.headers["authorization"] == "Bearer board-secret"
            return httpx.Response(200, json={"events": [observation(i) for i in range(101)]})
        assert request.url.path == "/events"
        assert request.headers["authorization"] == "Bearer api-secret"
        assert all(item["evidence_url"] is None for item in json.loads(request.content))
        return httpx.Response(200, json={"events": json.loads(request.content), "durability": "local"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert transfer("http://localhost:8091", "http://localhost:8000",
                        "board-secret", "api-secret", client=client) == 101
    assert [len(json.loads(r.content)) for r in seen[1:]] == [100, 1]


@pytest.mark.parametrize("payload", [
    {"events": [observation(), observation(1, evidence_url="/artifacts/private")]},
    {"events": [observation(), observation(1, confidence=True)]},
    {"events": "bad"}, {"private_images": [], "events": []},
])
def test_validates_all_source_records_before_any_write(payload):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExportUnavailable):
            transfer("http://localhost:8091", "http://localhost:8000", "a", "b", client=client)


@pytest.mark.parametrize("origin", ["http://venue.local", "https://user:secret@test", "https://test/?key=x"])
def test_rejects_unsafe_origins(origin):
    with pytest.raises(ValueError):
        transfer(origin, "http://localhost:8000", "a", "b")


def test_redirects_and_upstream_errors_are_not_followed_or_exposed():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://external.invalid/secret"},
                              text="internal secret")
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ExportUnavailable, match="not available") as error:
            transfer("http://localhost:8091", "http://localhost:8000", "a", "b", client=client)
        assert "secret" not in str(error.value)
