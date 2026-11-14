"""Generate or check machine-readable schemas without network access."""
import argparse
import json
from pathlib import Path

from contracts import models

NAMES = (
    "Event", "AskRequest", "Answer", "Timeline", "EventPage", "IngestResponse",
    "Summary", "SpeakRequest", "EvidenceResponse", "HeartbeatRequest", "SensorStatus", "ErrorResponse",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).parent / "schemas"
    target.mkdir(exist_ok=True)
    for name in NAMES:
        schema = getattr(models, name).model_json_schema(mode="serialization")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        path = target / f"{name}.json"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Schema drift: run make schemas and review {path}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
