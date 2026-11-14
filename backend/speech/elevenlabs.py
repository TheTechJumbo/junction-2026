"""Official convert endpoint, MP3 output, no retries or credential logging.

Docs checked 2026-11-14:
https://elevenlabs.io/docs/api-reference/text-to-speech/convert
https://elevenlabs.io/docs/overview/models
"""
import asyncio
import math
import os
import re
import time
from collections import OrderedDict

import httpx

from contracts.interfaces import SpeechUnavailable


def _has_mp3_frame(audio: bytes) -> bool:
    """Check complete Layer III frame structure, not just MIME or ID3 magic.

    This is a structural gate, not a decoder or proof of audible speech. Reject
    free-format/reserved headers and truncated frames. Optional ID3 tags are okay.
    """
    offset = 0
    if audio.startswith(b"ID3"):
        if len(audio) < 10 or audio[3] not in (2, 3, 4) or any(b & 128 for b in audio[6:10]):
            return False
        size = sum(b << shift for b, shift in zip(audio[6:10], (21, 14, 7, 0), strict=True))
        offset = 10 + size + (10 if audio[3] == 4 and audio[5] & 16 else 0)
    frames = 0
    while offset + 4 <= len(audio):
        if len(audio) - offset == 128 and audio[offset : offset + 3] == b"TAG":
            offset += 128
            break
        header = int.from_bytes(audio[offset : offset + 4], "big")
        version, layer = (header >> 19) & 3, (header >> 17) & 3
        bitrate, rate = (header >> 12) & 15, (header >> 10) & 3
        if (
            header >> 21 != 2047
            or version == 1
            or layer != 1
            or bitrate in (0, 15)
            or rate == 3
            or header & 3 == 2
        ):
            return False
        rates = (44100, 48000, 32000)
        sample_rate = rates[rate] // (1 if version == 3 else 2 if version == 2 else 4)
        bitrates = (
            (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
            if version == 3
            else (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
        )
        size = (144000 if version == 3 else 72000) * bitrates[bitrate - 1] // sample_rate
        offset += size + ((header >> 9) & 1)
        frames += 1
    return offset == len(audio) and frames >= 2


class ElevenLabsSpeechService:
    def __init__(
        self, api_key: str | None = None, voice_id: str | None = None,
        model_id: str = "eleven_flash_v2_5", timeout_seconds: float = 0.8,
        client: httpx.AsyncClient | None = None, cache_entries: int = 32,
        cache_ttl_seconds: float = 300, cache_bytes: int = 8 * 1024 * 1024,
        max_audio_bytes: int = 2 * 1024 * 1024,
    ):
        if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0
                or not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds < 0
                or any(not isinstance(value, int) or isinstance(value, bool)
                       for value in (cache_entries, cache_bytes, max_audio_bytes))
                or cache_entries < 0 or cache_bytes < 0 or max_audio_bytes <= 0):
            raise ValueError("Invalid speech deadline or cache limits")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", model_id):
            raise ValueError("Invalid speech model identifier")
        self._closed = False
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None
        self.cache_entries = cache_entries
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_bytes = cache_bytes
        self.max_audio_bytes = max_audio_bytes
        self._cache = OrderedDict()
        self._cached_bytes = 0
        self._inflight = {}
        self._semaphore = asyncio.Semaphore(4)

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
            model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"),
            timeout_seconds=float(os.getenv("SPEECH_TIMEOUT_SECONDS", "0.8")),
        )

    def _cache_get(self, key):
        now = time.monotonic()
        for expired in [k for k, (deadline, _) in self._cache.items() if deadline <= now]:
            self._cached_bytes -= len(self._cache.pop(expired)[1])
        entry = self._cache.get(key)
        if entry:
            self._cache.move_to_end(key)
            return entry[1]
        return None

    def _cache_put(self, key, audio):
        if self.cache_entries == 0 or self.cache_ttl_seconds <= 0 or len(audio) > self.cache_bytes:
            return
        if key in self._cache:
            self._cached_bytes -= len(self._cache.pop(key)[1])
        self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, audio)
        self._cached_bytes += len(audio)
        while len(self._cache) > self.cache_entries or self._cached_bytes > self.cache_bytes:
            self._cached_bytes -= len(self._cache.popitem(last=False)[1][1])

    async def synthesize(self, text: str) -> bytes:
        if self._closed:
            raise SpeechUnavailable("Speech service is closed")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise SpeechUnavailable("Speech text is empty or too long")
        if not self.api_key or not self.api_key.strip() or not self.voice_id:
            raise SpeechUnavailable("Speech is not configured; use phone speech")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.voice_id):
            raise SpeechUnavailable("Speech voice is not configured correctly")
        key = (text, self.voice_id, self.model_id, "mp3_44100_128")
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= 32:
                raise SpeechUnavailable("Speech is busy; use phone speech")
            task = asyncio.create_task(self._generate(text, key))
            self._inflight[key] = task

            def finished(done):
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)
                if not done.cancelled():
                    done.exception()  # consume failure if the requesting client disconnected

            task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.timeout_seconds)
        except TimeoutError:
            raise SpeechUnavailable("Speech timed out; use phone speech") from None

    async def _generate(self, text, key):
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self._semaphore:
                    if self._client is None:
                        self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
                    async with self._client.stream(
                        "POST", f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                        params={"output_format": "mp3_44100_128"},
                        headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
                        json={"text": text, "model_id": self.model_id},
                        timeout=self.timeout_seconds,
                        follow_redirects=False,
                    ) as response:
                        if response.status_code != 200:
                            raise SpeechUnavailable("Speech provider unavailable; use phone speech")
                        content_type = response.headers.get("content-type", "").split(";")[0].lower()
                        if content_type not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                            raise SpeechUnavailable("Speech provider returned invalid audio")
                        length = response.headers.get("content-length")
                        if length is not None and (int(length) < 0 or int(length) > self.max_audio_bytes):
                            raise SpeechUnavailable("Speech audio exceeded size limit")
                        audio = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(audio) + len(chunk) > self.max_audio_bytes:
                                raise SpeechUnavailable("Speech audio exceeded size limit")
                            audio.extend(chunk)
                        result = bytes(audio)
                        if not _has_mp3_frame(result):
                            raise SpeechUnavailable("Speech provider returned invalid audio")
                        if self._closed:
                            raise SpeechUnavailable("Speech service is closed")
                        self._cache_put(key, result)
                        return result
        except (TimeoutError, httpx.HTTPError, ValueError):
            raise SpeechUnavailable("Speech unavailable; use phone speech") from None

    async def aclose(self):
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._cache.clear()
        self._cached_bytes = 0
