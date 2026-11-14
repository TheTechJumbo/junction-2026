import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.speech import ElevenLabsSpeechService
from contracts.interfaces import SpeechUnavailable

MP3 = (b"\xff\xfb\x90\x00" + bytes(413)) * 2


def test_from_env_preserves_application_settings(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "synthetic-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "synthetic-voice")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    monkeypatch.setenv("SPEECH_TIMEOUT_SECONDS", "0.125")
    speech = ElevenLabsSpeechService.from_env()
    assert speech.api_key == "synthetic-key"
    assert speech.voice_id == "synthetic-voice"
    assert speech.model_id == "eleven_multilingual_v2"
    assert speech.timeout_seconds == .125
    asyncio.run(speech.aclose())


@pytest.mark.parametrize("kwargs", [
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"cache_ttl_seconds": -1}, {"cache_entries": .5}, {"cache_bytes": True},
    {"cache_bytes": -1}, {"max_audio_bytes": 0}, {"model_id": "../private"},
])
def test_invalid_resource_bounds_and_model(kwargs):
    with pytest.raises(ValueError):
        ElevenLabsSpeechService(**kwargs)


def test_queue_deadline_and_shutdown_cancel_all_shared_work():
    started = asyncio.Event()
    streams = []

    class Slow(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            started.set()
            await asyncio.sleep(1)
            yield MP3

        async def aclose(self):
            self.closed = True

    def handler(request):
        stream = Slow()
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=stream)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            speech = ElevenLabsSpeechService(
                "synthetic-key", "synthetic-voice", client=client, timeout_seconds=.03,
            )
            start = time.monotonic()
            results = await asyncio.gather(
                *(speech.synthesize(str(i)) for i in range(8)), return_exceptions=True,
            )
            assert all(isinstance(result, SpeechUnavailable) for result in results)
            assert time.monotonic() - start < .3
            assert len(streams) <= 4  # queueing consumed the same deadline
            await speech.aclose()
            assert all(stream.closed for stream in streams)
            assert not speech._inflight
            assert not speech._cache
            assert not client.is_closed
            with pytest.raises(SpeechUnavailable, match="closed"):
                await speech.synthesize("after close")

    asyncio.run(scenario())


def test_real_app_from_env_speak_contract_and_owned_shutdown(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
        return httpx.Response(503, text="synthetic-key private provider error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    speech = ElevenLabsSpeechService("synthetic-key", "synthetic-voice", client=client)
    monkeypatch.setattr(ElevenLabsSpeechService, "from_env", classmethod(lambda cls: speech))
    app = create_app(Settings(api_token="synthetic-api-token", local_data_dir=tmp_path))
    with TestClient(app) as http:
        assert http.post("/speak", json={"text": "Synthetic sentence"}).status_code == 401
        headers = {"Authorization": "Bearer synthetic-api-token"}
        result = http.post("/speak", json={"text": "Synthetic sentence"}, headers=headers)
        assert result.status_code == 200
        assert result.content == MP3
        assert result.headers["content-type"] == "audio/mpeg"
        assert result.headers["cache-control"] == "no-store"
        result = http.post("/speak", json={"text": "Different sentence"}, headers=headers)
        assert result.status_code == 503
        assert result.json() == {"error": {
            "code": "speech_unavailable", "message": "Speech is temporarily unavailable.",
        }}
        assert "synthetic-key" not in result.text
    assert speech._closed
    assert not client.is_closed  # injected HTTP clients remain caller-owned
    asyncio.run(client.aclose())
