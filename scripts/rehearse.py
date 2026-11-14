"""Seed a loopback demo with clearly synthetic observations and repeated questions.

API_TOKEN must be set. This does not claim medication ingestion or lock confirmation.
"""
import argparse
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timezone", default="Europe/Helsinki")
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("demo replay only supports a local disposable server")
    token = os.environ.get("API_TOKEN")
    if not token:
        parser.error("set API_TOKEN to the local server's token")
    now = datetime.now(ZoneInfo(args.timezone))
    night = now.replace(hour=3, minute=10, second=0, microsecond=0)
    if night > now:
        night -= timedelta(days=1)
    # A fresh prefix is explicit: replay creates a new rehearsal, not a producer retry.
    prefix = f"synthetic-rehearsal-{now.strftime('%Y%m%d%H%M%S')}"
    events = []
    for suffix, kind, at, zone, obj, source, confidence in [
        ("night", "door_open", night, "entrance", "front_door", "sensor", 1),
        ("pills", "pillbox_open", now - timedelta(minutes=15), "kitchen", "pillbox", "sensor", 1),
        ("repeat", "pillbox_open", now - timedelta(minutes=5), "kitchen", "pillbox", "sensor", 1),
        ("vision", "object_gone", now - timedelta(minutes=3), "kitchen", None, "vision", .6),
    ]:
        events.append(dict(id=f"{prefix}-{suffix}", type=kind, ts=at.isoformat(), source=source,
                           zone=zone, object=obj, evidence_url=None, confidence=confidence))
    with httpx.Client(base_url=args.base_url, headers={"Authorization": f"Bearer {token}"}, timeout=5) as client:
        response = client.post("/events", json=events)
        response.raise_for_status()
        for question in ["Did I already take my pills?", "Did I already take my pills?", "Did I lock the door?"]:
            response = client.post("/ask", json={"question": question})
            response.raise_for_status()
            print(f"{question}\n  {response.json()['answer']}")
    print(f"Synthetic rehearsal ready. Dashboard day: {now.date()}; night event day: {night.date()}.")


if __name__ == "__main__":
    main()
