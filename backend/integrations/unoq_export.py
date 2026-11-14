"""Explicit one-shot transfer of UNO Q scalar observations; never copies images."""
import argparse
import os
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from contracts.models import Event, IngestResponse


class ExportUnavailable(Exception):
    """Safe message suitable for a local operator; never includes tokens or response bodies."""


def validate_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
        raise ValueError("Use an HTTPS origin, or loopback HTTP, without credentials or a path")
    return value.rstrip("/")


def transfer(source_origin, target_origin, source_token, target_token, *, client=None):
    """Retrying the operation is safe: the central API deduplicates producer IDs.

    Validates the entire source export before any write. Batches are atomic individually;
    a later batch failure can leave earlier acknowledged batches, which a retry deduplicates.
    """
    source_origin, target_origin = map(validate_origin, (source_origin, target_origin))
    if not source_token or not source_token.strip() or not target_token or not target_token.strip():
        raise ValueError("Both service tokens must be configured")
    owned = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False)
    try:
        with client.stream("GET", source_origin + "/export/events",
                           headers={"Authorization": "Bearer " + source_token},
                           follow_redirects=False) as response:
            if response.status_code != 200:
                raise ExportUnavailable("UNO Q export was not available")
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > 8 * 1024 * 1024:
                    raise ExportUnavailable("UNO Q export exceeds the transfer limit")
                data.extend(chunk)
        import json
        payload = json.loads(data)
        if not isinstance(payload, dict) or set(payload) != {"events"}:
            raise ExportUnavailable("UNO Q returned an unsupported export")
        if not isinstance(payload["events"], list) or len(payload["events"]) > 10000:
            raise ExportUnavailable("UNO Q returned an unsupported export")
        # Match the source's ISO 8601 parser without relaxing central validation.
        normalized = []
        for item in payload["events"]:
            item = dict(item)
            stamp = datetime.fromisoformat(item["ts"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ExportUnavailable("UNO Q timestamp needs a timezone")
            item["ts"] = stamp.isoformat()
            normalized.append(item)
        events = [Event.model_validate(item) for item in normalized]
        # Fail closed if the source contract changes: never transport local artifact URLs.
        if any(event.evidence_url is not None for event in events):
            raise ExportUnavailable("UNO Q export unexpectedly contains image references")
        for offset in range(0, len(events), 100):
            batch = [event.model_dump(mode="json") for event in events[offset:offset + 100]]
            response = client.post(target_origin + "/events", json=batch,
                                   headers={"Authorization": "Bearer " + target_token},
                                   follow_redirects=False)
            if response.status_code != 200:
                raise ExportUnavailable("Central API did not accept a metadata batch; retry is safe")
            receipt = IngestResponse.model_validate(response.json())
            if {event.id for event in receipt.events} != {item["id"] for item in batch}:
                raise ExportUnavailable("Central API returned an incomplete receipt; retry is safe")
        return len(events)
    except ExportUnavailable:
        raise
    except Exception:
        raise ExportUnavailable("Metadata transfer failed; check service connectivity and configuration") from None
    finally:
        if owned:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="http://127.0.0.1:8091")
    parser.add_argument("--target", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        count = transfer(args.source, args.target, os.getenv("UNOQ_TOKEN"), os.getenv("API_TOKEN"))
    except (ValueError, ExportUnavailable) as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"Transferred {count} scalar observations; no images copied.")


if __name__ == "__main__":
    main()
