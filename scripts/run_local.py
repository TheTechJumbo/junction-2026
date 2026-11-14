"""Run the local API, optionally reading a private KEY=value file without a shell."""
import argparse
import os
import re
from pathlib import Path

import uvicorn


def load_private_env(path):
    """Simple one-line values; no shell execution, substitution, or interpolation."""
    path = Path(path)
    if not path.exists():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid configuration assignment at line {number}")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed configuration value at line {number}")
            value = value[1:-1]
        # Explicit process environment wins, including intentionally empty test values.
        os.environ.setdefault(key, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    try:
        load_private_env(args.env_file)
    except (OSError, ValueError):
        parser.error("Unable to load the private config; use one KEY=value assignment per line")
    uvicorn.run("backend.run:create_application", factory=True, host=args.host, port=args.port,
                access_log=False)


if __name__ == "__main__":
    main()
