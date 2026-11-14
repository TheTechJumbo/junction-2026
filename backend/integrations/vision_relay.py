"""Launch Seb's unchanged relay with authenticated, observation-only event egress.

Example (configure tokens privately, never as URL parameters or CLI arguments)::

    export EVENT_API_URL=http://127.0.0.1:8000/events
    export EVENT_API_TOKEN=<the central API bearer token>
    python -m backend.integrations.vision_relay --relay /path/to/live/relay.py -- --host 127.0.0.1

The wrapper changes only relay.post_events. Recording, frame picking, gateway/SSH,
UI, and cached navigation retain the supplied relay's behavior. Importing this module
or load_relay does not start a gateway, contact a GPU, or send an event.

Photo URLs belong to the relay's origin and access-key system. This bridge publishes
no evidence URL and never retrieves images. Photos remain separately available in
the relay UI; central dashboard photo integration requires a separate approved local
transfer. Relay size_cm stays in relay history; it is not a canonical Event field.
IDs hash the ORIGINAL complete payload plus batch index before these fields are
removed. Exact retries preserve identity, and different job image paths distinguish
otherwise identical observations. Producer-generated IDs remain preferred.
"""
import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from contracts.models import Event, IngestResponse


def validate_destination(url: str) -> str:
    """Allow HTTPS, or HTTP to an explicit loopback address, without URL credentials."""
    if not isinstance(url, str) or not url or any(
            character.isspace() or not character.isprintable() for character in url):
        raise ValueError("Invalid EVENT_API_URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid EVENT_API_URL") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path != "/events"
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("EVENT_API_URL must be an /events endpoint without URL credentials")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("HTTP event destinations must use an explicit loopback IP")
    return url


def canonical_events(events):
    """Strictly adapt the known relay shape, without weakening the central schema."""
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError("Supply between 1 and 100 relay events")
    allowed = set(Event.model_fields) | {"size_cm"}
    result = []
    for index, original in enumerate(events):
        if (not isinstance(original, dict) or set(original) - allowed
                or original.get("source") != "vision"):
            raise ValueError("Relay events must use the known vision schema")
        # Validate producer fields before dropping evidence/metadata. Never turn an invalid
        # producer observation into an apparently valid canonical event by discarding it.
        value = dict(original)
        canonical = json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if "id" not in value:
            value["id"] = "relay_" + hashlib.sha256(f"{canonical}:{index}".encode()).hexdigest()
        value.pop("size_cm", None)
        event = Event.model_validate(value)
        result.append(event.model_copy(update={"evidence_url": None}).model_dump(mode="json"))
    return result


class AuthenticatedEventPoster:
    """Finite transport timeouts, no redirects/proxy inheritance, and redacted failures."""
    def __init__(self, destination, token, *, client=None):
        self.destination = validate_destination(destination)
        if (not isinstance(token, str) or not token or not token.isascii()
                or any(character.isspace() or not character.isprintable() for character in token)):
            raise ValueError("EVENT_API_TOKEN must be a nonempty bearer credential")
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(2, connect=1, pool=1),
            follow_redirects=False, trust_env=False,
        )

    def __call__(self, url, events):
        if url != self.destination:
            raise OSError("Event destination differs from the configured endpoint")
        try:
            canonical = canonical_events(events)
            # Streaming avoids consuming an unbounded success/error body. Success is
            # accepted only after validating the bounded central durability receipt.
            deadline = time.monotonic() + 5
            with self._client.stream("POST", self.destination, json=canonical,
                                     headers={"Authorization": f"Bearer {self._token}"},
                                     follow_redirects=False) as response:
                if response.status_code != 200:
                    raise OSError("Event API rejected the observation batch")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise OSError("Event API receipt deadline exceeded")
                    body.extend(chunk)
                    if len(body) > 1024 * 1024:
                        raise OSError("Event API returned an oversized receipt")
                receipt = IngestResponse.model_validate_json(body)
                if [item.id for item in receipt.events] != [item["id"] for item in canonical]:
                    raise OSError("Event API did not confirm durable acceptance")
                return response.status_code
        except (ValueError, TypeError, httpx.HTTPError):
            # Seb's relay catches OSError and reports posted_to_event_api as failed.
            # Do not expose URLs, credentials, response bodies, or upstream exceptions.
            raise OSError("Event API unavailable or observation batch invalid") from None

    def close(self):
        if self._owns_client:
            self._client.close()


def load_relay(path, *, event_api_url, token, client=None):
    """Load explicitly supplied teammate code and replace only its egress function.

    Returns (relay_module, poster). The caller must close poster when finished.
    """
    path = Path(path).resolve(strict=True)
    if path.suffix != ".py" or not path.is_file():
        raise ValueError("--relay must identify a Python relay file")
    poster = AuthenticatedEventPoster(event_api_url, token, client=client)
    try:
        spec = importlib.util.spec_from_file_location("junction_supplied_vision_relay", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not all(callable(getattr(module, name, None))
                   for name in ("main", "to_events", "post_events", "make_handler")):
            raise ValueError("Supplied file does not expose the expected relay interface")
        module.post_events = poster
        return module, poster
    except BaseException:
        poster.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--relay", required=True, help="Path to Seb's live/relay.py")
    parser.add_argument("relay_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    destination = os.environ.get("EVENT_API_URL", "")
    token = os.environ.get("EVENT_API_TOKEN", "")
    try:
        relay, poster = load_relay(args.relay, event_api_url=destination, token=token)
    except (OSError, ValueError):
        parser.error("Check --relay, EVENT_API_URL and EVENT_API_TOKEN configuration")
    forwarded = args.relay_args
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    previous_argv = sys.argv
    try:
        sys.argv = [args.relay, *forwarded]
        relay.main()
    finally:
        sys.argv = previous_argv
        poster.close()


if __name__ == "__main__":
    main()
