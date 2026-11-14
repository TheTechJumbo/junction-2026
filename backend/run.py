"""Assembled local API and family dashboard entry point."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.staticfiles import StaticFiles

from backend.app import create_app

NAVIGATION_ENV = (
    "VISION_RELAY_URL", "VISION_RELAY_KEY", "VISION_RELAY_ZONE",
    "NAVIGATION_MAX_AGE_SECONDS", "NAVIGATION_MIN_CONFIDENCE",
)


def navigation_from_env(environ=None):
    """Opt in only with a complete explicit policy; construction performs no requests."""
    environ = os.environ if environ is None else environ
    values = {name: environ.get(name, "") for name in NAVIGATION_ENV}
    if not any(value.strip() for value in values.values()):
        return None
    if not all(value.strip() for value in values.values()):
        raise ValueError("Cached navigation requires all five settings: " + ", ".join(NAVIGATION_ENV))

    # Keep optional network dependencies out of the unconfigured application path.
    from backend.answer_engine.adapters import CachedNavigationAdapter, NavigationPolicy

    try:
        url, token, zone = (values[name] for name in NAVIGATION_ENV[:3])
        if any(character.isspace() or not character.isprintable() for character in url):
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.username is not None or parsed.password is not None
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError()
        if (not token.isascii()
                or any(character.isspace() or not character.isprintable() for character in token)
                or not re.fullmatch(r"[a-z0-9_-]{1,32}", zone)):
            raise ValueError()
        policy = NavigationPolicy(max_age_s=float(values["NAVIGATION_MAX_AGE_SECONDS"]),
                                  min_confidence=float(values["NAVIGATION_MIN_CONFIDENCE"]))
        return CachedNavigationAdapter(base_url=url, token=token, zone=zone, policy=policy)
    except (TypeError, ValueError):
        # URL parsers and numeric errors may echo values, including credentials.
        raise ValueError("Invalid cached navigation configuration; check the five settings") from None


def create_application():
    navigation = navigation_from_env()
    if navigation is None:
        app = create_app()
    else:
        from backend.answer_engine import RulesAnswerEngine
        app = create_app(engine=RulesAnswerEngine(navigation_adapter=navigation))

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    dashboard = Path(__file__).resolve().parents[1] / "dashboard"
    app.mount("/dashboard", StaticFiles(directory=dashboard, html=True), name="dashboard")
    return app
