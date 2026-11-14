"""Internal ports. Implementations must not change these without integration review."""
from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from contracts.models import Alert, Answer, Event, QuestionRecord


class EventReader(Protocol):
    def list_events(self, start: datetime, end: datetime) -> Sequence[Event]:
        """Return [start, end), sorted by (event ts, id), including locally queued writes."""
        ...


class QuestionLog(Protocol):
    def record(self, intent: str, question: str, ts: datetime) -> QuestionRecord: ...

    def list_questions(self, start: datetime, end: datetime) -> Sequence[QuestionRecord]: ...


class AnswerEngine(Protocol):
    def classify(self, question: str) -> str:
        """pills, door, navigation, medical, or open; no network calls."""
        ...

    def answer(
        self, question: str, events: Sequence[Event], now: datetime, timezone: str
    ) -> Answer: ...

    def derive_alerts(
        self, events: Sequence[Event], day: date, timezone: str
    ) -> Sequence[Alert]: ...

    def summarize(
        self, events: Sequence[Event], alerts: Sequence[Alert],
        questions: Sequence[QuestionRecord], day: date, timezone: str
    ) -> str: ...


class SpeechService(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Return audio/mpeg or raise SpeechUnavailable within the configured budget."""
        ...


class SpeechUnavailable(Exception):
    """API maps this to 503 speech_unavailable; Ed's phone performs local TTS."""
