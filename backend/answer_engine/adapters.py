"""Opt-in network boundaries. Never imported or called by the deterministic rules.

Gemini REST format verified against Google's generateContent reference 2026-11-14:
https://aistudio.google.com/api-reference/generate-content/generate-content
HTTPX timeouts are inactivity limits, so asyncio.timeout enforces a total deadline:
https://www.python-httpx.org/advanced/timeouts/
Navigation shape/auth verified from origin/jun-22-hud-waypoints relay.py and locate.py.
No /locate, image request, redirect, retry or GPU operation is performed here.
"""
import asyncio
import json
import math
import re
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from typing import Any
from urllib.parse import urlsplit

from .rules import _words


class _Boundary:
    def __init__(self, timeout_s: float, transport=None):
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 2:
            raise ValueError("Adapter timeout must be finite and in (0,2] seconds")
        self.timeout_s = timeout_s
        self.transport = transport
        # A stalled transport cannot accumulate threads on subsequent requests.
        self._slot = BoundedSemaphore(1)

    def _bounded(self, operation: Callable[[], Coroutine[Any, Any, str | None]]) -> str | None:
        if not self._slot.acquire(blocking=False):
            return None
        result: Queue[str | None] = Queue(maxsize=1)

        async def run():
            async with asyncio.timeout(self.timeout_s):
                return await operation()

        def worker():
            try:
                value = asyncio.run(run())
                result.put_nowait(value)
            except Exception:
                result.put_nowait(None)
            finally:
                self._slot.release()

        Thread(target=worker, daemon=True, name="optional-answer").start()
        try:
            return result.get(timeout=self.timeout_s)
        except Empty:
            return None

    async def _json(self, method, url, *, headers, params=None, body=None):
        # Lazy dependency: core rules require only stdlib + canonical Pydantic models.
        import httpx

        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s,
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, url, headers=headers, params=params, json=body) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        return None
                parsed = json.loads(data)
                return parsed if isinstance(parsed, dict) else None


class GeminiOpenAdapter(_Boundary):
    """Explicit key + model required; question text only, no events/photos/history."""
    def __init__(self, *, api_key: str, model: str, timeout_s: float = 0.8, transport=None):
        super().__init__(timeout_s, transport)
        if not api_key.strip() or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", model):
            raise ValueError("A nonempty API key and simple Gemini model ID are required")
        self._api_key, self.model = api_key, model

    def answer(self, question: str) -> str | None:
        from .rules import RulesAnswerEngine

        # Defend even if used without the engine's router.
        if RulesAnswerEngine().classify(question) != "open":
            return None
        return self._bounded(lambda: self._answer(question[:2000]))

    async def _answer(self, question):
        payload = await self._json(
            "POST", f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            body={"contents": [{"role": "user", "parts": [{"text": question}]}],
                  "systemInstruction": {"parts": [{"text":
                      "Answer briefly and clearly. You have no household observations or location. "
                      "Never claim a person's medicine was taken, a door was locked, or an action "
                      "was completed. If asked about personal events, say you do not know."}]},
                  "generationConfig": {"maxOutputTokens": 160}},
        )
        if not payload or payload.get("promptFeedback", {}).get("blockReason"):
            return None
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return None
        candidate = candidates[0]
        if candidate.get("finishReason") != "STOP":
            return None
        parts = candidate.get("content", {}).get("parts", [])
        text = " ".join(p["text"] for p in parts if isinstance(p, dict)
                        and not p.get("thought") and isinstance(p.get("text"), str)).strip()
        return text[:2000] or None


@dataclass(frozen=True)
class NavigationPolicy:
    # No deployment defaults: confidence/freshness require an explicit local decision (D7).
    max_age_s: float
    min_confidence: float
    waypoint_names: Mapping[str, str] = field(default_factory=lambda: {
        "bathroom": "bathroom", "pill box": "pill box", "pillbox": "pill box",
        "pills": "pill box", "front door": "front door", "door": "front door",
    })

    def __post_init__(self):
        if not math.isfinite(self.max_age_s) or self.max_age_s <= 0:
            raise ValueError("max_age_s must be finite and positive")
        if not math.isfinite(self.min_confidence) or not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be finite and in [0,1]")


class CachedNavigationAdapter(_Boundary):
    def __init__(self, *, base_url: str, zone: str, token: str,
                 policy: NavigationPolicy, timeout_s: float = 0.4, transport=None):
        super().__init__(timeout_s, transport)
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("Navigation URL must be an origin without credentials/path/query")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Nonlocal navigation needs HTTPS")
        if not zone.strip() or not token.strip():
            raise ValueError("Navigation requires a zone and token")
        self.base_url, self.zone, self._token = base_url.rstrip("/"), zone, token
        self.policy = policy

    def answer(self, question: str) -> str | None:
        words = _words(question)
        if re.search(r"\b(garage|bathroom door|bedroom|back door|side door|patio)\b", words):
            return None
        names = [(alias, name) for alias, name in self.policy.waypoint_names.items()
                 if re.search(r"(?<!\w)" + re.escape(_words(alias)) + r"(?!\w)", words)]
        # The most specific name wins ('front door' before 'door').
        if len({name for _, name in names}) > 1:
            return None
        target = max(names, key=lambda item: len(item[0]))[1] if names else None
        if target is None and not re.search(r"\bwhere am i\b", words):
            return None
        return self._bounded(lambda: self._answer(target))

    async def _answer(self, target):
        fix = await self._json("GET", self.base_url + "/where", headers={"X-Key": self._token},
                               params={"zone": self.zone})
        if not fix or fix.get("state") != "found" or fix.get("zone") != self.zone:
            return None
        age, confidence = fix.get("age_s"), fix.get("confidence")
        if (not _number(age) or not 0 <= age <= self.policy.max_age_s
                or not _number(confidence) or not self.policy.min_confidence <= confidence <= 1):
            return None
        if target is None:
            return f"The latest location estimate places you in {self.zone}."
        waypoints = fix.get("waypoints", [])
        if not isinstance(waypoints, list):
            return None
        matches = [w for w in waypoints if isinstance(w, dict) and w.get("name") == target]
        if len(matches) != 1:
            return None
        point = matches[0]
        distance, bearing = point.get("distance_m"), point.get("bearing_deg")
        if not _number(distance) or distance < 0 or not _number(bearing) or not -180 <= bearing <= 180:
            return None
        side = ("ahead" if abs(bearing) <= 20 else "behind you" if abs(bearing) >= 150 else
                "to your right" if bearing > 0 else "to your left")
        return f"The {target} is estimated to be about {distance:.1f} metres {side}."


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
