import asyncio
import json
import time
from dataclasses import dataclass, replace

import httpx
import pytest

from backend.speech import ElevenLabsSpeechService
from contracts.interfaces import SpeechUnavailable

# Synthetic MPEG-1 Layer III frame structure; not a recording or audible fixture.
FRAME = b"\xff\xfb\x90\x00" + bytes(413)
MP3 = FRAME * 2
@dataclass(frozen=True)
class Options:
    api_key: str | None = "synthetic-test-key"
    voice_id: str | None = "synthetic-voice"
    deadline_ms: float = 800
    cache_entries: int = 32
    cache_bytes: int = 8 * 1024 * 1024
    cache_ttl_s: float = 300
    max_audio_bytes: int = 2 * 1024 * 1024


CONFIG = Options()


def configured(config, *, client):
    return ElevenLabsSpeechService(
        api_key=config.api_key, voice_id=config.voice_id, client=client,
        timeout_seconds=config.deadline_ms / 1000,
        cache_entries=config.cache_entries, cache_bytes=config.cache_bytes,
        cache_ttl_seconds=config.cache_ttl_s, max_audio_bytes=config.max_audio_bytes,
    )



def run(coro):
    return asyncio.run(coro)


def response(data=MP3, status=200, content_type="audio/mpeg", **kwargs):
    return httpx.Response(status, content=data, headers={"content-type": content_type}, **kwargs)


def test_request_and_cache():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == (
            "https://api.elevenlabs.io/v1/text-to-speech/synthetic-voice"
            "?output_format=mp3_44100_128"
        )
        assert request.headers["xi-api-key"] == CONFIG.api_key
        assert json.loads(request.content) == {
            "text": "Synthetic sentence.",
            "model_id": "eleven_flash_v2_5",
        }
        return response()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(CONFIG, client=client)
            assert await service.synthesize("Synthetic sentence.") == MP3
            assert await service.synthesize("Synthetic sentence.") == MP3
            await service.aclose()
            assert not client.is_closed
            with pytest.raises(SpeechUnavailable):
                await service.synthesize("Synthetic sentence.")

    run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize(
    "config",
    [
        Options(api_key=None, voice_id=None),
        replace(CONFIG, api_key=None),
        replace(CONFIG, api_key="  "),
        replace(CONFIG, voice_id=None),
    ],
)
def test_no_key_or_voice_never_calls_upstream(config):
    def handler(request):
        pytest.fail("Unconfigured service made upstream call")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(config, client=client)
            with pytest.raises(SpeechUnavailable, match="Speech"):
                await service.synthesize("Synthetic sentence.")

    run(scenario())


@pytest.mark.parametrize("text", ["", " ", "x" * 2001, None])
def test_bad_input(text):
    run(_expect_unavailable(CONFIG, lambda request: pytest.fail("upstream called"), text))


async def _expect_unavailable(config, handler, text="Synthetic sentence."):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = configured(config, client=client)
        with pytest.raises(SpeechUnavailable, match="Speech") as caught:
            await service.synthesize(text)
        assert "synthetic-test-key" not in str(caught.value)


@pytest.mark.parametrize("status", [301, 400, 401, 403, 422, 429, 500, 503])
def test_status_errors_no_retry_or_body_leak(status):
    calls = []

    def handler(request):
        calls.append(request)
        return response(b"synthetic-test-key private error", status=status)

    run(_expect_unavailable(CONFIG, handler))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "data,content_type",
    [
        (b"", "audio/mpeg"),
        (b"{}", "application/json"),
        (b"{}", "audio/mpeg"),
        (b"ID3" + bytes(7), "audio/mpeg"),
        (MP3[:-1], "audio/mpeg"),
        (FRAME, "audio/mpeg"),
        (MP3, "audio/wav"),
        (b"\xff\xfb\xff\x00" * 300, "audio/mpeg"),
    ],
)
def test_invalid_audio(data, content_type):
    run(_expect_unavailable(CONFIG, lambda request: response(data, content_type=content_type)))


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
def test_transport_errors(error):
    def handler(request):
        raise error("synthetic-test-key private error")

    run(_expect_unavailable(CONFIG, handler))


def test_overall_deadline_and_queue_and_recovery():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1)
        return response()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(replace(CONFIG, deadline_ms=30), client=client)
            start = time.monotonic()
            results = await asyncio.gather(
                service.synthesize("first"), service.synthesize("second"), return_exceptions=True
            )
            assert isinstance(results[0], SpeechUnavailable)
            assert time.monotonic() - start < 0.3
            assert all(isinstance(r, (bytes, SpeechUnavailable)) for r in results)
            assert await service.synthesize("recovery") == MP3

    run(scenario())


def test_identical_concurrent_requests_coalesce_and_cancel_releases_lock():
    calls = 0
    started = asyncio.Event()

    async def handler(request):
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(0.02)
        return response()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(CONFIG, client=client)
            assert (
                await asyncio.gather(*(service.synthesize("same") for _ in range(8))) == [MP3] * 8
            )
            assert calls == 1
            started.clear()
            task = asyncio.create_task(service.synthesize("cancel"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await service.synthesize("after cancellation") == MP3

    run(scenario())


def test_lru_entry_byte_limits_and_ttl(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("backend.speech.elevenlabs.time.monotonic", lambda: now[0])
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["text"])
        return response()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(
                replace(CONFIG, cache_entries=2, cache_bytes=len(MP3) * 2), client=client
            )
            for text in ["a", "b", "a", "c", "b"]:
                await service.synthesize(text)
            assert calls == ["a", "b", "c", "b"]
            assert service._cached_bytes <= len(MP3) * 2
            now[0] += 301
            await service.synthesize("b")
            assert calls == ["a", "b", "c", "b", "b"]
            assert len(service._cache) == 1

    run(scenario())


@pytest.mark.parametrize(
    "config",
    [
        replace(CONFIG, cache_entries=0),
        replace(CONFIG, cache_bytes=len(MP3) - 1),
        replace(CONFIG, cache_ttl_s=0),
    ],
)
def test_disabled_or_oversize_cache(config):
    calls = []

    def handler(request):
        calls.append(request)
        return response()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(config, client=client)
            await service.synthesize("same")
            await service.synthesize("same")
            assert service._cached_bytes == 0

    run(scenario())
    assert len(calls) == 2


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay, self.closed = chunks, delay, False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("mode", ["oversize", "slow", "length", "bad_length"])
def test_stream_limits_and_cleanup(mode):
    stream = Chunks([FRAME] * 4, delay=0.03 if mode == "slow" else 0)
    headers = {"content-type": "audio/mpeg"}
    if mode in ("length", "bad_length"):
        headers["content-length"] = "999999999" if mode == "length" else "invalid"

    def handler(request):
        return httpx.Response(200, stream=stream, headers=headers)

    config = replace(CONFIG, max_audio_bytes=1000, deadline_ms=50)
    run(_expect_unavailable(config, handler))
    assert stream.closed


def test_failures_not_cached():
    calls = []

    def handler(request):
        calls.append(request)
        return response(status=503 if len(calls) == 1 else 200)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = configured(CONFIG, client=client)
            with pytest.raises(SpeechUnavailable):
                await service.synthesize("same")
            assert await service.synthesize("same") == MP3

    run(scenario())
    assert len(calls) == 2


def test_id3_tag_and_mpeg2_accepted():
    # Empty ID3v2.4 tag followed by complete frames.
    run(_success(b"ID3\x04\x00\x00" + bytes(4) + MP3))
    # MPEG-2 Layer III, 64 kbps / 22050 Hz -> 208 byte frame.
    frame = b"\xff\xf3\x80\x00" + bytes(204)
    run(_success(frame * 2))


async def _success(audio):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: response(audio))
    ) as client:
        service = configured(CONFIG, client=client)
        assert await service.synthesize("Synthetic sentence.") == audio
