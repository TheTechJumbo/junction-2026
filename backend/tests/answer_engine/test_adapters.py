import asyncio
import json
from datetime import UTC, datetime
from threading import Event as ThreadEvent
from time import monotonic, sleep

import httpx
import pytest

from backend.answer_engine import RulesAnswerEngine
from backend.answer_engine.adapters import (
    CachedNavigationAdapter,
    GeminiOpenAdapter,
    NavigationPolicy,
)


def navigation(handler, **kwargs):
    return CachedNavigationAdapter(base_url="http://127.0.0.1:8090", zone="kitchen", token="private-key",
                                   policy=NavigationPolicy(max_age_s=5, min_confidence=0.8),
                                   transport=httpx.MockTransport(handler), **kwargs)


def fix(**kwargs):
    return dict(state="found", zone="kitchen", confidence=0.9, age_s=1,
                waypoints=[dict(name="bathroom", distance_m=2.3, bearing_deg=45)], **kwargs)


def gemini(handler, **kwargs):
    return GeminiOpenAdapter(api_key="private-key", model="configured-test-model",
                              transport=httpx.MockTransport(handler), **kwargs)


def generated(text="A short answer.", reason="STOP"):
    return dict(candidates=[dict(finishReason=reason, content=dict(parts=[dict(text=text)]))])


def test_navigation_only_get_where_with_header_auth():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=fix())
    adapter = navigation(handler)
    text = adapter.answer("Where is the bathroom?")
    assert text == "The bathroom is estimated to be about 2.3 metres to your right."
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET" and request.url.path == "/where"
    assert dict(request.url.params) == {"zone": "kitchen"}
    assert request.headers["X-Key"] == "private-key"
    assert "private-key" not in str(request.url)


@pytest.mark.parametrize("patch", [
    {"state": "lost"}, {"state": "unsure"}, {"state": "unknown"},
    {"age_s": 5.001}, {"age_s": -1}, {"age_s": "1"}, {"age_s": True},
    {"age_s": float("nan")}, {"confidence": 0.79}, {"confidence": 1.01},
    {"confidence": float("inf")}, {"confidence": True}, {"confidence": "0.9"},
    {"zone": "bedroom"}, {"waypoints": []}, {"waypoints": {}},
    {"waypoints": [dict(name="bathroom", distance_m=-1, bearing_deg=45)]},
    {"waypoints": [dict(name="bathroom", distance_m=1, bearing_deg=float("nan"))]},
    {"waypoints": [dict(name="bathroom", distance_m=1, bearing_deg=181)]},
    {"waypoints": [dict(name="bathroom", distance_m=True, bearing_deg=10)]},
    {"waypoints": [dict(name="bathroom", distance_m=1, bearing_deg=1)] * 2},
])
def test_navigation_unreliable_fix_is_uncertain(patch):
    payload = {**fix(), **patch}
    # bytes permit deliberate NaN/Inf malformed upstream values for finite-value checks.
    adapter = navigation(lambda r: httpx.Response(200, content=json.dumps(payload).encode()))
    assert adapter.answer("Where is the bathroom?") is None


@pytest.mark.parametrize(("bearing", "side"), [(0, "ahead"), (-45, "to your left"),
                                                (90, "to your right"), (-180, "behind you")])
def test_navigation_direction_boundaries(bearing, side):
    payload = {**fix(), "waypoints": [dict(name="bathroom", distance_m=2.3, bearing_deg=bearing)]}
    assert navigation(lambda r: httpx.Response(200, json=payload)).answer("where bathroom").endswith(
        side + ".")


def test_current_location_and_boundary_values():
    payload = {**fix(), "age_s": 5, "confidence": 0.8}
    text = navigation(lambda r: httpx.Response(200, json=payload)).answer("Where am I?")
    assert text == "The latest location estimate places you in kitchen."


@pytest.mark.parametrize("question", ["Where is the back door?", "Where is the garage?",
                                      "Where are the bathroom and pill box?", "Where is my cat?"])
def test_unknown_or_ambiguous_navigation_does_not_request(question):
    def fail(request):
        pytest.fail("No upstream needed")
    assert navigation(fail).answer(question) is None


@pytest.mark.parametrize("status", [302, 401, 404, 429, 500])
def test_adapter_http_failure(status):
    def handler(request):
        return httpx.Response(status, headers={"location": "https://other.test/secret"})
    assert navigation(handler).answer("Where am I?") is None
    assert gemini(handler).answer("Tell me a joke") is None


def test_gemini_request_is_text_only_and_explicit_model():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=generated())
    adapter = gemini(handler)
    assert adapter.answer("Tell me a joke") == "A short answer."
    request = seen[0]
    assert request.method == "POST"
    assert request.url == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "configured-test-model:generateContent")
    assert request.headers["x-goog-api-key"] == "private-key"
    assert "private-key" not in repr(adapter)
    payload = json.loads(request.content)
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "Tell me a joke"}]}]
    assert "tools" not in payload
    assert payload["generationConfig"]["maxOutputTokens"] == 160
    assert "household observations" in payload["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize("payload", [
    {}, {"candidates": []}, {"candidates": None}, {"candidates": [None]},
    {"promptFeedback": {"blockReason": "SAFETY"}}, generated(reason="SAFETY"),
    generated(reason="MAX_TOKENS"), generated(text=""),
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "secret", "thought": True}]}}]},
])
def test_gemini_blocked_empty_malformed_or_truncated_falls_back(payload):
    assert gemini(lambda r: httpx.Response(200, json=payload)).answer("Hi") is None


@pytest.mark.parametrize("question", ["Did I take pills?", "Did I lock the door?", "Where am I?"])
def test_gemini_rejects_core_questions(question):
    seen = []
    adapter = gemini(lambda r: seen.append(r))
    assert adapter.answer(question) is None
    assert not seen


@pytest.mark.parametrize("body", [b"not json", b"[]", b"x" * 65537])
def test_bad_or_oversize_response(body):
    def response(request):
        return httpx.Response(200, content=body)
    assert gemini(response).answer("Hi") is None
    assert navigation(response).answer("Where am I?") is None


def test_optional_deadline_and_async_context():
    canceled = ThreadEvent()
    async def slow(request):
        try:
            await asyncio.sleep(1)
            return httpx.Response(200, json=generated())
        finally:
            canceled.set()
    adapter = gemini(slow, timeout_s=0.03)
    async def use_from_event_loop():
        start = monotonic()
        assert adapter.answer("Hi") is None
        return monotonic() - start
    assert asyncio.run(use_from_event_loop()) < 0.2
    assert canceled.wait(timeout=0.2)


def test_stalled_dependency_does_not_accumulate_workers():
    blocked, release = ThreadEvent(), ThreadEvent()
    def deliberately_bad_sync_transport(request):
        blocked.set()
        release.wait(timeout=1)
        return httpx.Response(200, json=generated())
    adapter = gemini(deliberately_bad_sync_transport, timeout_s=0.02)
    try:
        assert adapter.answer("Hi") is None
        assert blocked.is_set()
        start = monotonic()
        for _ in range(20):
            assert adapter.answer("Hi") is None
        assert monotonic() - start < 0.1
    finally:
        release.set()


def test_core_unaffected_while_optional_adapter_busy():
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=generated())
    engine = RulesAnswerEngine(open_adapter=gemini(slow, timeout_s=0.02))
    now = datetime.now(UTC)
    engine.answer("Hi", [], now, "Europe/Helsinki")
    start = monotonic()
    result = engine.answer("Did I take my pills?", [], now, "Europe/Helsinki")
    assert "cannot confirm" in result.answer
    assert monotonic() - start < 0.1
    sleep(0.03)  # allow the optional cancellation cleanup to complete


@pytest.mark.parametrize("kwargs", [{"max_age_s": 0, "min_confidence": 0.8},
                                    {"max_age_s": float("inf"), "min_confidence": 0.8},
                                    {"max_age_s": 5, "min_confidence": -1}])
def test_navigation_policy_validation(kwargs):
    with pytest.raises(ValueError):
        NavigationPolicy(**kwargs)


@pytest.mark.parametrize("url", ["https://user:pass@example.com", "https://example.com/?token=x",
                                 "https://example.com/where", "http://example.com", "file:///etc/passwd"])
def test_navigation_origin_validation(url):
    with pytest.raises(ValueError):
        CachedNavigationAdapter(base_url=url, zone="kitchen", token="key",
                                 policy=NavigationPolicy(max_age_s=5, min_confidence=0.8))


@pytest.mark.parametrize("timeout", [-1, 0, 2.1, float("nan"), float("inf")])
def test_deadline_validation(timeout):
    with pytest.raises(ValueError):
        GeminiOpenAdapter(api_key="key", model="model", timeout_s=timeout)
