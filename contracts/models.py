"""Wire schemas, not business rules. See API.md for provisional decisions."""
from datetime import date
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Event(Model):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    type: str = Field(min_length=1, max_length=256)
    source: Literal["sensor", "vision", "voice"]
    ts: AwareDatetime
    zone: str = Field(min_length=1, max_length=256)
    object: str | None = Field(max_length=256)
    evidence_url: str | None
    confidence: StrictFloat | StrictInt = Field(ge=0, le=1)

    @field_validator("ts", mode="before")
    @classmethod
    def require_timestamp_string_or_datetime(cls, value):
        from datetime import datetime
        if not isinstance(value, (str, datetime)):
            raise ValueError("ts must be an ISO timestamp, not epoch seconds")
        return value


class AskRequest(Model):
    question: str = Field(min_length=1, max_length=2000)


class Answer(Model):
    answer: str
    evidence: list[Event]


class Alert(Model):
    id: str
    type: str
    ts: AwareDatetime
    message: str
    event_ids: list[str]


class QuestionRecord(Model):
    id: str
    intent: str
    question: str
    ts: AwareDatetime


class QuestionCount(Model):
    intent: str
    count: int = Field(ge=1)
    last_ts: AwareDatetime


class SensorStatus(Model):
    id: str
    last_seen: AwareDatetime | None
    status: Literal["online", "offline", "unknown"]


class Timeline(Model):
    events: list[Event]
    baseline: None = None
    alerts: list[Alert]
    questions: list[QuestionCount]
    sensors: list[SensorStatus]
    day: date
    timezone: str = "Europe/Helsinki"


class EventPage(Model):
    events: list[Event]
    next_cursor: str | None


class IngestResponse(Model):
    events: list[Event]
    durability: Literal["local", "replicated"]


class Summary(Model):
    text: str


class SpeakRequest(Model):
    text: str = Field(min_length=1, max_length=2000)


class EvidenceResponse(Model):
    evidence_url: str


class HeartbeatRequest(Model):
    device_id: str = Field(min_length=1, max_length=128)


class ErrorDetail(Model):
    code: str
    message: str


class ErrorResponse(Model):
    error: ErrorDetail
