"""Authenticated local API. Run `uvicorn backend.app:create_app --factory --host 127.0.0.1`."""
import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import sqlite3
import time
import warnings
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.middleware.cors import CORSMiddleware

from backend.storage import (
    CursorExpired,
    IdentityConflict,
    InvalidCursor,
    MissingEvent,
    SQLiteStore,
)
from contracts.interfaces import SpeechUnavailable
from contracts.models import (
    Answer,
    AskRequest,
    Event,
    EventPage,
    EvidenceResponse,
    HeartbeatRequest,
    IngestResponse,
    QuestionCount,
    SensorStatus,
    SpeakRequest,
    Summary,
    Timeline,
)

logger = logging.getLogger(__name__)
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024


@dataclass
class Settings:
    api_token: str = field(default="", repr=False)
    local_data_dir: str | Path = ".data"
    database_url: str | None = None
    timezone: str = "Europe/Helsinki"
    configured_devices: tuple[str, ...] = ()
    sensor_offline_seconds: float = 30
    evidence_mode: str = "local_only"
    evidence_retention_seconds: float = 86400
    replication_interval_seconds: float = 1
    stream_poll_seconds: float = .1
    stream_keepalive_seconds: float = 15
    allowed_origins: tuple[str, ...] = ()
    # Optional device-specific bearer credentials. Do not configure real credentials in code.
    device_tokens: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls):
        return cls(
            api_token=os.environ.get("API_TOKEN", ""),
            local_data_dir=os.environ.get("LOCAL_DATA_DIR", ".data"),
            database_url=os.environ.get("DATABASE_URL") or None,
            timezone=os.environ.get("APP_TIMEZONE", "Europe/Helsinki"),
            configured_devices=tuple(filter(None, os.environ.get("SENSOR_IDS", "").split(","))),
            sensor_offline_seconds=float(os.environ.get("SENSOR_OFFLINE_SECONDS", "30")),
            evidence_mode=os.environ.get("EVIDENCE_MODE", "local_only"),
            allowed_origins=tuple(filter(None, os.environ.get("ALLOWED_ORIGINS", "").split(","))),
        )


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def error_response(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def day_bounds(day, timezone):
    zone = ZoneInfo(timezone)
    return (datetime.combine(day, day_time.min, zone).astimezone(UTC),
            datetime.combine(day + timedelta(days=1), day_time.min, zone).astimezone(UTC))


def parse_events(payload):
    batch = payload if isinstance(payload, list) else [payload]
    if not batch or len(batch) > 100:
        raise APIError(400, "invalid_input", "Supply between 1 and 100 events.")
    result = []
    for index, item in enumerate(batch):
        if not isinstance(item, dict):
            raise APIError(400, "invalid_input", "Each event must be an object.")
        item = item.copy()
        metadata = {}
        if item.get("source") == "vision" and "id" not in item:
            canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
            item["id"] = "legacy_" + hashlib.sha256(f"{canonical}:{index}".encode()).hexdigest()
            if "size_cm" in item:
                metadata["size_cm"] = item.pop("size_cm")
            item.setdefault("object", None)
        result.append((Event.model_validate(item), metadata))
    return result


async def bounded_body(request, maximum):
    chunks, length = [], 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > maximum:
            raise APIError(413, "too_large", "Request exceeds the upload limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def verify_image(data, claimed_type):
    if len(data) > MAX_EVIDENCE_BYTES:
        raise APIError(413, "too_large", "Evidence exceeds 5 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                detected = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(img.format)
                if detected is None or detected != claimed_type:
                    raise APIError(415, "unsupported_media", "Supply a valid JPEG or PNG.")
                img.verify()
            # verify() alone does not fully decode JPEG pixel data.
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                # Normalize orientation and retain pixels only, never EXIF/GPS/comments.
                pixels = ImageOps.exif_transpose(img).convert("RGB")
                clean = Image.new("RGB", pixels.size)
                clean.paste(pixels)
                output = io.BytesIO()
                clean.save(output, "JPEG" if detected == "image/jpeg" else "PNG")
                normalized = output.getvalue()
                if len(normalized) > MAX_EVIDENCE_BYTES:
                    raise APIError(413, "too_large", "Normalized evidence exceeds 5 MiB.")
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise APIError(415, "unsupported_media", "Supply a valid JPEG or PNG.") from exc
    return normalized, detected


def create_app(settings=None, *, clock=None, engine=None, speech=None, store=None):
    settings = settings or Settings.from_env()
    if not settings.api_token:
        raise ValueError("API_TOKEN must be configured before starting the API")
    zone = ZoneInfo(settings.timezone)
    now = clock or (lambda: datetime.now(UTC))
    if engine is None:
        from backend.answer_engine import RulesAnswerEngine
        engine = RulesAnswerEngine(clock=now)
    owns_speech = speech is None
    if speech is None:
        from backend.speech import ElevenLabsSpeechService
        speech = ElevenLabsSpeechService.from_env()
    owns_store = store is None
    store = store or SQLiteStore(Path(settings.local_data_dir) / "events.sqlite3",
                                 settings.database_url)

    async def maintenance(stop):
        while not stop.is_set():
            try:
                await run_in_threadpool(store.expire_evidence, now())
                await run_in_threadpool(store.replicate_once)
            except Exception:
                # Never log upstream error text (it may contain credentials or producer data).
                logger.warning("Storage maintenance unavailable; durable local queue retained")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.replication_interval_seconds)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(maintenance(stop))
        try:
            yield
        finally:
            stop.set()
            await task
            if owns_speech and hasattr(speech, "aclose"):
                await speech.aclose()
            if owns_store:
                await run_in_threadpool(store.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store = settings, store
    app.state.engine, app.state.speech = engine, speech
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type", "Last-Event-ID"])

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return error_response(400, "invalid_input", "Request does not match the API contract.")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, "not_found" if exc.status_code == 404
                              else "invalid_input", "Resource unavailable or invalid request.")

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return error_response(503, "durability_unavailable", "Local durable storage is unavailable.")

    @app.exception_handler(IdentityConflict)
    async def conflict(request, exc):
        return error_response(409, "identity_conflict", "Event ID already has different producer data.")

    @app.exception_handler(MissingEvent)
    async def missing(request, exc):
        return error_response(404, "not_found", "Resource not found or expired.")

    @app.exception_handler(InvalidCursor)
    async def invalid_cursor(request, exc):
        return error_response(400, "invalid_cursor", "Invalid replay cursor.")

    @app.exception_handler(CursorExpired)
    async def expired_cursor(request, exc):
        return error_response(410, "cursor_expired", "Reload the timeline and start a new stream.")

    def authenticate(request: Request):
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token:
            if hmac.compare_digest(token.encode(), settings.api_token.encode()):
                return None
            for device_id, credential in settings.device_tokens.items():
                if hmac.compare_digest(token.encode(), credential.encode()):
                    return device_id
        raise APIError(401, "unauthorized", "A valid bearer token is required.")

    auth = [Depends(authenticate)]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/events", response_model=IngestResponse, dependencies=auth)
    async def ingest(request: Request):
        body = await bounded_body(request, 1024 * 1024)
        try:
            payload = json.loads(body)
            items = parse_events(payload)
        except (ValueError, TypeError) as exc:
            raise APIError(400, "invalid_input", "Request does not match the event contract.") from exc
        events = await run_in_threadpool(store.ingest, items)
        # Replication may finish later; this conservative receipt promises only local durability.
        return IngestResponse(events=events, durability="local")

    def page(cursor, since, limit):
        if cursor is not None and since is not None:
            raise APIError(400, "invalid_input", "Use cursor or since, not both.")
        if not 1 <= limit <= 100:
            raise APIError(400, "invalid_input", "Limit must be between 1 and 100.")
        if since is not None:
            try:
                since = datetime.fromisoformat(since.replace("Z", "+00:00"))
                if since.utcoffset() is None:
                    raise ValueError()
            except ValueError as exc:
                raise APIError(400, "invalid_input", "since requires a timezone-aware timestamp.") from exc
        store.expire_evidence(now())
        return store.replay(cursor, since, limit)

    @app.get("/events", response_model=EventPage, dependencies=auth)
    def events(cursor: str | None = None, since: str | None = None, limit: int = 100):
        entries, next_cursor = page(cursor, since, limit)
        return EventPage(events=[event for _, event in entries], next_cursor=next_cursor)

    @app.get("/events/stream", dependencies=auth)
    async def stream(request: Request, cursor: str | None = None):
        header = request.headers.get("last-event-id")
        if header and cursor and header != cursor:
            raise APIError(400, "invalid_input", "Replay cursors disagree.")
        cursor = header or cursor
        # Validate before response headers; errors after streaming begins cannot change status.
        initial, next_cursor = await run_in_threadpool(page, cursor, None, 100)

        async def generate():
            entries, current = initial, next_cursor
            last_keepalive = time.monotonic()
            while True:
                for sequence, event in entries:
                    yield f"id: {sequence}\nevent: event\ndata: {event.model_dump_json()}\n\n"
                if await request.is_disconnected():
                    return
                if time.monotonic() - last_keepalive >= settings.stream_keepalive_seconds:
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()
                await asyncio.sleep(settings.stream_poll_seconds)
                entries, current = await run_in_threadpool(page, current, None, 100)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/events/{event_id}/evidence", response_model=EvidenceResponse, dependencies=auth)
    async def evidence(event_id: str, request: Request):
        if settings.evidence_mode != "local_only":
            raise APIError(503, "evidence_unavailable", "Evidence storage is local-only.")
        await run_in_threadpool(store.get, event_id)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        body = await bounded_body(request, MAX_EVIDENCE_BYTES + 65536)
        if content_type == "multipart/form-data":
            # The bounded entire body fits in RAM; raw uploads must never spool to disk.
            class MemoryMultipartParser(MultiPartParser):
                spool_max_size = MAX_EVIDENCE_BYTES + 65537

            async def chunks():
                yield body
            try:
                form = await MemoryMultipartParser(
                    request.headers, chunks(), max_files=1, max_fields=0,
                    max_part_size=MAX_EVIDENCE_BYTES,
                ).parse()
                try:
                    upload = form.get("file")
                    if upload is None or not hasattr(upload, "read") or len(form) != 1:
                        raise APIError(400, "invalid_input", "Use one multipart field named file.")
                    data = await upload.read(MAX_EVIDENCE_BYTES + 1)
                    media_type = upload.content_type
                finally:
                    await form.close()
            except MultiPartException as exc:
                raise APIError(400, "invalid_input", "Invalid multipart upload.") from exc
        elif content_type in ("image/jpeg", "image/png"):
            data, media_type = body, content_type
        else:
            raise APIError(415, "unsupported_media", "Supply JPEG, PNG or a multipart file.")
        data, media_type = await run_in_threadpool(verify_image, data, media_type)
        url = await run_in_threadpool(store.attach_evidence, event_id, data, media_type,
                                     now() + timedelta(seconds=settings.evidence_retention_seconds))
        return EvidenceResponse(evidence_url=url)

    @app.get("/evidence/{evidence_id}", dependencies=auth)
    def download(evidence_id: str):
        data, media_type = store.get_evidence(evidence_id, now())
        return Response(data, media_type=media_type,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    def daily(day):
        selected = day or now().astimezone(zone).date()
        start, end = day_bounds(selected, settings.timezone)
        store.expire_evidence(now())
        events = store.list_events(start, end)
        questions = store.list_questions(start, end)
        alerts = list(engine.derive_alerts(events, selected, settings.timezone))
        return selected, events, questions, alerts

    @app.get("/timeline", response_model=Timeline, dependencies=auth)
    def timeline(day: date | None = None):
        selected, events, questions, alerts = daily(day)
        grouped = defaultdict(list)
        for question in questions:
            grouped[question.intent].append(question.ts)
        counts = [QuestionCount(intent=intent, count=len(times), last_ts=max(times))
                  for intent, times in sorted(grouped.items())]
        return Timeline(events=events, alerts=alerts, questions=counts, day=selected,
                        timezone=settings.timezone,
                        sensors=store.sensors(settings.configured_devices, now(),
                                              settings.sensor_offline_seconds))

    @app.get("/summary", response_model=Summary, dependencies=auth)
    def summary(day: date | None = None):
        selected, events, questions, alerts = daily(day)
        return Summary(text=engine.summarize(events, alerts, questions, selected, settings.timezone))

    @app.post("/ask", response_model=Answer, dependencies=auth)
    def ask(body: AskRequest):
        timestamp = now()
        intent = engine.classify(body.question)
        start, end = day_bounds(timestamp.astimezone(zone).date(), settings.timezone)
        store.expire_evidence(timestamp)
        events = store.list_events(start, end)
        answer = engine.answer(body.question, events, timestamp, settings.timezone)
        store.record(intent, body.question, timestamp)
        return answer

    @app.post("/speak", dependencies=auth)
    async def speak(body: SpeakRequest):
        try:
            audio = await speech.synthesize(body.text)
            if not isinstance(audio, bytes) or not audio:
                raise SpeechUnavailable()
        except SpeechUnavailable as exc:
            raise APIError(503, "speech_unavailable", "Speech is temporarily unavailable.") from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @app.post("/heartbeat", response_model=SensorStatus)
    def heartbeat(body: HeartbeatRequest, device_id=Depends(authenticate)):
        if device_id is not None and device_id != body.device_id:
            raise APIError(401, "unauthorized", "Device credential does not match the heartbeat.")
        return store.heartbeat(body.device_id, now())

    return app
