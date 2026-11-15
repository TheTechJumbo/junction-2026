"""Credential-free tests of actual engine/speech/dashboard wiring."""
from fastapi.testclient import TestClient

from backend.run import create_application


def test_assembled_application_serves_private_dashboard_and_no_key_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("API_TOKEN", "assembly-test-token")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    for key in ("DATABASE_URL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_application()) as client:
        page = client.get("/dashboard/")
        assert page.status_code == 200
        assert "Daylight" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "assembly-test-token" not in page.text
        assert client.get("/timeline").status_code == 401
        headers = {"Authorization": "Bearer assembly-test-token"}
        response = client.post("/ask", json={"question": "Did I take my pills?"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["evidence"] == []
        speech = client.post("/speak", json={"text": response.json()["answer"]}, headers=headers)
        assert speech.status_code == 503
        assert speech.json()["error"]["code"] == "speech_unavailable"
        assert speech.headers["cache-control"] == "no-store"
