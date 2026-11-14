"""Assembled local API and family dashboard entry point."""
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from backend.app import create_app


def create_application():
    app = create_app()
    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    dashboard = Path(__file__).resolve().parents[1] / "dashboard"
    app.mount("/dashboard", StaticFiles(directory=dashboard, html=True), name="dashboard")
    return app
