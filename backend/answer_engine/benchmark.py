"""Offline engine-only latency: python -m backend.answer_engine.benchmark."""
import json
import platform
from datetime import UTC, datetime, timedelta
from statistics import median
from time import perf_counter_ns

from contracts.models import Event

from .rules import RulesAnswerEngine


def benchmark(samples=1000, event_count=1000):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    events = [Event(id=f"synthetic-{i}", type="pillbox_open" if i % 2 else "door_closed",
                    object="pillbox" if i % 2 else "front_door", source="sensor", zone="kitchen",
                    ts=now - timedelta(seconds=i), evidence_url=None, confidence=1)
              for i in range(event_count)]
    engine = RulesAnswerEngine(clock=lambda: now)
    results = {}
    for question in ("Did I take my pills?", "Did I lock the door?"):
        for _ in range(20):
            engine.answer(question, events, now, "Europe/Helsinki")
        elapsed = []
        for _ in range(samples):
            start = perf_counter_ns()
            answer = engine.answer(question, events, now, "Europe/Helsinki")
            elapsed.append((perf_counter_ns() - start) / 1_000_000)
            assert answer.evidence
        ordered = sorted(elapsed)
        results[engine.classify(question)] = {
            "median_ms": round(median(elapsed), 3),
            "p95_ms": round(ordered[int(0.95 * (samples - 1))], 3),
            "max_ms": round(max(elapsed), 3),
        }
    return {"platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "samples_per_question": samples,
            "events_per_question": event_count, "results": results,
            "scope": "engine only; no storage, HTTP, speech or device latency"}


if __name__ == "__main__":
    print(json.dumps(benchmark(), indent=2))
