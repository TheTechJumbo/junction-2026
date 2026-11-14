"""Pure, conservative answers from observations. No model or network imports here."""
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Protocol
from zoneinfo import ZoneInfo

from contracts.models import Alert, Answer, Event, QuestionRecord


def _words(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(UTC)


class OptionalAnswer(Protocol):
    def answer(self, question: str) -> str | None: ...


@dataclass(frozen=True)
class RulesConfig:
    pillbox_objects: frozenset[str] = frozenset({"pillbox", "pill_box", "pill box"})
    door_objects: frozenset[str] = frozenset({"front_door", "front door", "door"})
    pillbox_zones: frozenset[str] = frozenset({"kitchen"})
    door_zones: frozenset[str] = frozenset({"entrance"})
    min_sensor_confidence: float = 1.0
    pills_max_age: timedelta = timedelta(hours=24)
    door_max_age: timedelta = timedelta(hours=12)
    night_start: time = time(23)
    night_end: time = time(6)
    night_alerts_enabled: bool = True

    def __post_init__(self):
        if not 0 <= self.min_sensor_confidence <= 1:
            raise ValueError("min_sensor_confidence must be in [0,1]")
        if self.pills_max_age <= timedelta() or self.door_max_age <= timedelta():
            raise ValueError("Observation ages must be positive")
        if self.night_start.tzinfo or self.night_end.tzinfo:
            raise ValueError("Night times must be local wall times")


@dataclass
class RulesAnswerEngine:
    config: RulesConfig = field(default_factory=RulesConfig)
    open_adapter: OptionalAnswer | None = None
    navigation_adapter: OptionalAnswer | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)

    def classify(self, question: str) -> str:
        words = _words(question)
        if re.search(
            r"\b(diagnos\w*|symptoms?|treatments?|dosages?|dose|overdose|prescri\w*|"
            r"aspirin|ibuprofen|paracetamol|insulin|antibiotics?|pain|fever|dementia)\b"
            r"|\b(should|can|may|must) i (?:take|stop|skip|double|increase|reduce)\b"
            r"|\b(how (?:many|much)|when) (?:pills?|medicine|medication)\b", words
        ):
            return "medical"
        if re.search(r"\b(where|directions?|navigate|navigation)\b|\bhow (?:do i |can i )?get\b", words):
            return "navigation"
        if re.search(r"\b(pills?|pillbox|medication|medicines?|meds|tablets?|dose)\b", words):
            return "pills"
        if re.search(r"\b(door|doors|locked|lock|locking|unlocked)\b", words):
            return "door"
        return "open"

    def _events(self, events, day, timezone, now):
        tz = ZoneInfo(timezone)
        cutoff = _utc(now)
        # Same-id identical replays count once. Conflicting same-id observations count neither.
        unique: dict[str, Event] = {}
        conflicts = set()
        for event in events:
            previous = unique.get(event.id)
            if previous is not None:
                previous_data = previous.model_dump(exclude={"evidence_url", "ts"})
                current_data = event.model_dump(exclude={"evidence_url", "ts"})
                if previous_data != current_data or _utc(previous.ts) != _utc(event.ts):
                    conflicts.add(event.id)
                # Evidence attachments are mutable; preserve an available attachment on replay.
                if event.evidence_url is None and previous.evidence_url is not None:
                    event = previous
            unique[event.id] = event
        return sorted(
            (e for e in unique.values() if e.id not in conflicts
             and _utc(e.ts) <= cutoff and e.ts.astimezone(tz).date() == day),
            key=lambda e: (_utc(e.ts), e.id),
        )

    def _sensor(self, event, types, objects):
        zones = self.config.pillbox_zones if "pillbox_open" in types else self.config.door_zones
        return (event.source == "sensor" and event.type in types
                and event.object is not None and _words(event.object) in {_words(o) for o in objects}
                and _words(event.zone) in {_words(zone) for zone in zones}
                and event.confidence >= self.config.min_sensor_confidence)

    def answer(
        self, question: str, events: Sequence[Event], now: datetime, timezone: str
    ) -> Answer:
        now_utc = _utc(now)
        tz = ZoneInfo(timezone)
        intent = self.classify(question)
        if intent == "medical":
            return Answer(answer="I cannot make medical or dosing decisions. "
                          "Please ask your doctor or pharmacist.", evidence=[])
        if intent in {"pills", "door"}:
            if re.search(
                r"\b(yesterday|tomorrow|tonight|last|previous|next|ago|monday|tuesday|"
                r"wednesday|thursday|friday|saturday|sunday|january|february|march|april|"
                r"may|june|july|august|september|october|november|december)\b"
                r"|\b\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?\b", question, re.I
            ):
                return Answer(answer="I can only check today's observations for this question.",
                              evidence=[])
            if re.search(r"\b(wife|husband|mother|father|dad|mom|mum|partner|son|daughter|"
                         r"their|his|her|someone|another|other|he|she)\b", _words(question)):
                return Answer(answer="I cannot identify whose medication or door that question "
                              "refers to from these observations.", evidence=[])
            selected = self._events(events, now.astimezone(tz).date(), timezone, now)
            if intent == "pills":
                mentioned_rooms = set(re.findall(
                    r"\b(kitchen|bedroom|bathroom|garage|office|garden|entrance|living room|"
                    r"dining room|hospital|school)\b", _words(question)))
                if mentioned_rooms - {_words(zone) for zone in self.config.pillbox_zones}:
                    return Answer(answer="I do not have reliable pill box observations for "
                                  "that place.", evidence=[])
                relevant = [e for e in selected if self._sensor(
                    e, {"pillbox_open"}, self.config.pillbox_objects)
                    and now_utc - _utc(e.ts) <= self.config.pills_max_age]
                if not relevant:
                    return Answer(answer="I have no recent pill box opening recorded today. "
                                  "I cannot confirm whether the pills were taken.", evidence=[])
                latest = relevant[-1]
                return Answer(answer=f"The pill box was opened at {latest.ts.astimezone(tz):%H:%M}. "
                              "I cannot confirm that the pills were taken.", evidence=[latest])
            # A named different door cannot be answered from the default front-door sensor.
            if re.search(r"\b(garage|bathroom|bedroom|back|side|patio|cupboard)\b", _words(question)):
                return Answer(answer="I do not have reliable observations for that door.", evidence=[])
            relevant = [e for e in selected if self._sensor(
                e, {"door_open", "door_closed"}, self.config.door_objects)
                and now_utc - _utc(e.ts) <= self.config.door_max_age]
            if not relevant:
                return Answer(answer="I have no recent front door observation recorded today. "
                              "I cannot confirm whether it is locked.", evidence=[])
            latest = relevant[-1]
            simultaneous = [e for e in relevant if _utc(e.ts) == _utc(latest.ts)]
            if len({e.type for e in simultaneous}) > 1:
                return Answer(answer="The latest front door observations conflict. "
                              "I cannot confirm whether it is open, closed or locked.",
                              evidence=simultaneous)
            state = "opened" if latest.type == "door_open" else "closed"
            return Answer(answer=f"The front door was {state} at {latest.ts.astimezone(tz):%H:%M}. "
                          "I cannot confirm whether it is locked.", evidence=[latest])
        adapter = self.navigation_adapter if intent == "navigation" else self.open_adapter
        text = None
        if adapter is not None:
            try:
                text = adapter.answer(question)
            except Exception:
                # Optional dependencies never make an otherwise valid question fail.
                text = None
        fallback = ("I do not have a reliable current location. Please ask someone nearby for help."
                    if intent == "navigation" else
                    "I do not have enough information to answer that question.")
        return Answer(answer=text or fallback, evidence=[])

    def derive_alerts(
        self, events: Sequence[Event], day: date, timezone: str
    ) -> Sequence[Alert]:
        if not self.config.night_alerts_enabled:
            return []
        tz = ZoneInfo(timezone)
        alerts = []
        for event in self._events(events, day, timezone, self.clock()):
            if not self._sensor(event, {"door_open"}, self.config.door_objects):
                continue
            local_time = event.ts.astimezone(tz).time()
            start, end = self.config.night_start, self.config.night_end
            night = (start <= local_time < end if start < end else
                     local_time >= start or local_time < end) if start != end else False
            if night:
                # Stable across replays, input order and process restarts.
                identity = f"night_door_open:{event.id}"
                alerts.append(Alert(id="night-door-" + sha256(identity.encode()).hexdigest()[:24],
                                    type="night_door_open", ts=event.ts,
                                    message=f"The front door opened at "
                                    f"{event.ts.astimezone(tz):%H:%M} during night hours.",
                                    event_ids=[event.id]))
        return alerts

    def summarize(
        self, events: Sequence[Event], alerts: Sequence[Alert],
        questions: Sequence[QuestionRecord], day: date, timezone: str
    ) -> str:
        now = self.clock()
        selected = self._events(events, day, timezone, now)
        pills = sum(self._sensor(e, {"pillbox_open"}, self.config.pillbox_objects) for e in selected)
        doors = sum(self._sensor(e, {"door_open"}, self.config.door_objects) for e in selected)
        # Recompute eligibility so vision, unrelated or fabricated alerts cannot become assertions.
        eligible = {a.id for a in self.derive_alerts(selected, day, timezone)}
        alert_count = len({a.id for a in alerts if a.id in eligible})
        tz = ZoneInfo(timezone)
        qs = {q.id: q for q in questions
              if _utc(q.ts) <= _utc(now) and q.ts.astimezone(tz).date() == day}
        counts = Counter(q.intent for q in qs.values())
        detail = ", ".join(f"{intent}: {count}" for intent, count in sorted(counts.items()))
        text = (f"On {day.isoformat()}, {pills} pill box opening(s) and {doors} front door "
                f"opening(s) were recorded by sensors. {alert_count} night door alert(s). "
                f"{len(qs)} question(s) were recorded")
        text += f" ({detail})." if detail else "."
        return text + " Openings do not confirm medication ingestion or door locking."
