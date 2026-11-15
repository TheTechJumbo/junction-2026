"""Deliberately attach one selected local JPEG/PNG to one existing local API event.

No directory scan, metadata creation, teammate service, or remote image retrieval occurs.
API_TOKEN is read from the environment by the CLI, never from a command-line argument.
"""
import argparse
import io
import ipaddress
import os
import re
import stat
import time
import warnings
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from contracts.models import EventPage, EvidenceResponse

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_RECEIPT_BYTES = 1024 * 1024


class AttachmentUnavailable(Exception):
    """Sanitized operator message, never a filename, credential or upstream response."""


def validate_target(target):
    try:
        if not isinstance(target, str) or any(c.isspace() or not c.isprintable() for c in target):
            raise ValueError()
        parsed = urlsplit(target)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Target must be a loopback literal IP origin without credentials or a path") from None
    return target.rstrip("/")


def read_image(path):
    """Open exactly the selected regular file, reject symlinks and bound the read."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise AttachmentUnavailable("Select a regular local image file")
            if not 0 < info.st_size <= MAX_IMAGE_BYTES:
                raise AttachmentUnavailable("Image must contain between 1 byte and 5 MiB")
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise AttachmentUnavailable("Image must contain between 1 byte and 5 MiB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise AttachmentUnavailable("Image exceeds the 16 megapixel decoded limit")
                media_type = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image.format)
                if media_type is None:
                    raise AttachmentUnavailable("Select a valid JPEG or PNG image")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        return data, media_type
    except AttachmentUnavailable:
        raise
    except (OSError, ValueError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AttachmentUnavailable("Selected local image could not be read or decoded") from None


def _response_json(client, method, url, token, deadline, *, read_timeout=2, **kwargs):
    if time.monotonic() >= deadline:
        raise AttachmentUnavailable("Local attachment operation exceeded its deadline")
    with client.stream(method, url, headers={"Authorization": "Bearer " + token,
                                           **kwargs.pop("headers", {})},
                       follow_redirects=False, timeout=httpx.Timeout(2, connect=1, read=read_timeout),
                       **kwargs) as response:
        if response.status_code != 200:
            raise AttachmentUnavailable("Local API rejected the request; verify authentication and event ID")
        data = bytearray()
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise AttachmentUnavailable("Local attachment operation exceeded its deadline")
            if len(data) + len(chunk) > MAX_RECEIPT_BYTES:
                raise AttachmentUnavailable("Local API response exceeded the receipt limit")
            data.extend(chunk)
    return bytes(data)


def _verify_event(client, target, event_id, token, deadline):
    cursor = None
    for _ in range(1000):
        params = {"limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        page = EventPage.model_validate_json(_response_json(
            client, "GET", target + "/events", token, deadline, params=params))
        if any(event.id == event_id for event in page.events):
            return
        if not page.events:
            raise AttachmentUnavailable("No matching event was found; import its metadata first")
        if page.next_cursor is None or page.next_cursor == cursor:
            raise AttachmentUnavailable("Local API did not provide a usable event cursor")
        cursor = page.next_cursor
    raise AttachmentUnavailable("Event lookup exceeded its limit; no image was uploaded")


def _verify_attachment(client, target, event_id, expected_url, token, deadline):
    """Replay entries are snapshots: scan through the end to compare the latest one."""
    cursor, latest = None, None
    for _ in range(1000):
        params = {"limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        page = EventPage.model_validate_json(_response_json(
            client, "GET", target + "/events", token, deadline, params=params))
        for event in page.events:
            if event.id == event_id:
                latest = event
        if not page.events:
            if latest is not None and latest.evidence_url == expected_url:
                return
            raise AttachmentUnavailable("Attachment receipt does not match the latest event; inspect before retrying")
        if page.next_cursor is None or page.next_cursor == cursor:
            raise AttachmentUnavailable("Attachment readback did not provide a usable event cursor")
        cursor = page.next_cursor
    raise AttachmentUnavailable("Attachment readback exceeded its limit; inspect before retrying")


def attach(target, event_id, image_path, token, *, client=None):
    """Validate a selected file and existing event, then upload once to the local API.

    A lost/malformed receipt does not prove that the server rejected the image. No
    automatic upload retry occurs; inspect the event before deliberately retrying.
    """
    target = validate_target(target)
    if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
        raise ValueError("Event ID must match the canonical event contract")
    if (not isinstance(token, str) or not token or not token.isascii()
            or any(c.isspace() or not c.isprintable() for c in token)):
        raise ValueError("API_TOKEN must contain a valid bearer credential")
    data, media_type = read_image(image_path)
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(2, connect=1),
                                   follow_redirects=False, trust_env=False)
    deadline = time.monotonic() + 10
    try:
        _verify_event(client, target, event_id, token, deadline)
        # Encode the path component even though the canonical ID validator is restrictive.
        receipt = EvidenceResponse.model_validate_json(_response_json(
            client, "POST", target + "/events/" + quote(event_id, safe="") + "/evidence",
            token, time.monotonic() + 15, read_timeout=12,
            content=data, headers={"Content-Type": media_type}))
        if not re.fullmatch(r"/evidence/[0-9a-f]{32}", receipt.evidence_url):
            raise AttachmentUnavailable("API did not return a valid local evidence receipt; inspect the event before retrying")
        _verify_attachment(client, target, event_id, receipt.evidence_url, token,
                           time.monotonic() + 10)
        return receipt.evidence_url
    except AttachmentUnavailable:
        raise
    except Exception:
        raise AttachmentUnavailable("Local attachment could not be verified; inspect the event before retrying") from None
    finally:
        if owned:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Loopback literal IP origin of the central API")
    parser.add_argument("--event-id", required=True, help="Existing canonical event ID")
    parser.add_argument("--image", required=True, help="Explicit local regular JPEG/PNG file")
    args = parser.parse_args(argv)
    try:
        attach(args.target, args.event_id, args.image, os.environ.get("API_TOKEN"))
    except (ValueError, AttachmentUnavailable) as exc:
        parser.exit(1, str(exc) + "\n")
    print("Local evidence attached to the existing event.")


if __name__ == "__main__":
    main()
