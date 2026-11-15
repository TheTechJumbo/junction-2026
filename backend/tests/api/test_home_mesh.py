"""The home in 3D: /mesh, /hazards and /night serve what the vision stage wrote, behind the token."""
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(api_token="test-token", local_data_dir=tmp_path)
    app = create_app(settings, clock=lambda: datetime(2026, 11, 15, 6, tzinfo=UTC))
    with TestClient(app) as c:
        yield c, tmp_path


def test_nothing_yet_is_404_and_the_token_is_required(client):
    c, _ = client
    for path in ("/mesh", "/hazards", "/night", "/night?day=2026-11-15"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers={"Authorization": "Bearer test-token"}).status_code == 404


def test_files_from_the_vision_stage_are_served(client):
    c, root = client
    (root / "mesh").mkdir()
    (root / "mesh" / "home.ply").write_bytes(b"ply\nformat binary_little_endian 1.0\nend_header\n")
    (root / "mesh" / "hazards.json").write_text(json.dumps({"hazards": [], "up": "-y"}))
    (root / "mesh" / "night-2026-11-15.json").write_text(json.dumps({"day": "2026-11-15", "points": []}))
    auth = {"Authorization": "Bearer test-token"}
    mesh = c.get("/mesh", headers=auth)
    assert mesh.status_code == 200 and mesh.headers["content-type"] == "application/octet-stream"
    assert mesh.headers["cache-control"] == "private, no-store"
    assert c.get("/hazards", headers=auth).json() == {"hazards": [], "up": "-y"}
    assert c.get("/night", headers=auth).json()["day"] == "2026-11-15"  # today in Helsinki
    assert c.get("/night?day=2026-11-14", headers=auth).status_code == 404
    assert c.get("/night?day=../../etc", headers=auth).status_code in (400, 422)  # only a date, never a path
