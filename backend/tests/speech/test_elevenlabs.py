import asyncio
import json
import time

import httpx
import pytest

from backend.speech import ElevenLabsSpeechService
from contracts.interfaces import SpeechUnavailable

# Complete synthetic MPEG1 Layer III frame; no claim of human-audited speech.
MP3 = b'\xff\xfb\x90\x64' + bytes(413)


def run(coro):
    return asyncio.run(coro)


def service(handler, **kwargs):
    return ElevenLabsSpeechService(
        api_key="synthetic-key", voice_id="synthetic-voice",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs,
    )


def test_request_shape_success_and_cache():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "api.elevenlabs.io"
        assert request.headers["xi-api-key"] == "synthetic-key"
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert json.loads(request.content) == {"text": "Hello", "model_id": "eleven_flash_v2_5"}
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    async def exercise():
        svc = service(handler)
        assert await svc.synthesize("Hello") == MP3
        assert await svc.synthesize("Hello") == MP3
        await svc._client.aclose()
    run(exercise())
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_upstream_errors_are_safe_no_retries(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive upstream payload", headers={"location":"https://example.com"})

    async def exercise():
        svc = service(handler)
        with pytest.raises(SpeechUnavailable) as err:
            await svc.synthesize("Hello")
        assert "sensitive" not in str(err.value)
        await svc._client.aclose()
    run(exercise())
    assert len(calls) == 1


@pytest.mark.parametrize("body,mime", [(b"", "audio/mpeg"), (b"ID3fake", "audio/mpeg"),
                                      (b"<html>error</html>", "audio/mpeg"),
                                      (MP3, "application/json"), (MP3[:10], "audio/mpeg")])
def test_invalid_audio(body, mime):
    async def exercise():
        svc = service(lambda _: httpx.Response(200, content=body, headers={"content-type":mime}))
        with pytest.raises(SpeechUnavailable):
            await svc.synthesize("Hello")
        await svc._client.aclose()
    run(exercise())


def test_missing_config_does_not_call_network():
    async def exercise():
        svc = ElevenLabsSpeechService()
        with pytest.raises(SpeechUnavailable, match="not configured"):
            await svc.synthesize("Hello")
    run(exercise())


def test_deadline_includes_slow_response():
    async def slow(_):
        await asyncio.sleep(1)
        return httpx.Response(200, content=MP3, headers={"content-type":"audio/mpeg"})

    async def exercise():
        svc = service(slow, timeout_seconds=.03)
        start = time.monotonic()
        with pytest.raises(SpeechUnavailable):
            await svc.synthesize("Hello")
        assert time.monotonic() - start < .2
        await svc.aclose()
        await svc._client.aclose()
    run(exercise())


def test_concurrent_same_text_coalesces():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(.01)
        return httpx.Response(200, content=MP3, headers={"content-type":"audio/mpeg"})

    async def exercise():
        svc = service(handler)
        assert await asyncio.gather(*(svc.synthesize("Hello") for _ in range(8))) == [MP3] * 8
        await svc._client.aclose()
    run(exercise())
    assert len(calls) == 1


def test_cache_eviction_and_size_cap():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=MP3, headers={"content-type":"audio/mpeg"})

    async def exercise():
        svc = service(handler, cache_entries=1)
        for text in ["A", "B", "A"]:
            await svc.synthesize(text)
        assert len(svc._cache) == 1
        await svc._client.aclose()
        small = service(handler, max_audio_bytes=100)
        with pytest.raises(SpeechUnavailable, match="size"):
            await small.synthesize("Hello")
        await small._client.aclose()
    run(exercise())
    assert len(calls) == 4


def test_cache_expiry(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("backend.speech.elevenlabs.time.monotonic", lambda: clock[0])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=MP3, headers={"content-type":"audio/mpeg"})

    async def exercise():
        svc = service(handler, cache_ttl_seconds=1)
        await svc.synthesize("Hello")
        clock[0] += 2
        await svc.synthesize("Hello")
        await svc._client.aclose()
    run(exercise())
    assert len(calls) == 2
