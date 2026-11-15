#!/bin/bash
# One-off, on the login node (compute nodes have no internet): the event API's Python environment for the servers
# job, /scratch/$USER/junction-api/.venv-api. Python 3.12 is the one uv already keeps in scratch (.venv-recon uses it
# too). The packages are the API's runtime dependencies (pyproject.toml) at the versions in requirements-dev.lock,
# installed with --no-deps so nothing else comes in: about 80 MB from PyPI (opencv-python-headless ~50 MB, numpy
# ~16 MB, Pillow and psycopg-binary ~5 MB each). The dev tools (pytest, ruff, jsonschema) are left out.
#   bash cluster/setup_api_env.sh
set -euo pipefail
API_DIR="${SERVERS_API_DIR:-/scratch/$USER/junction-api}"
UV="${UV:-$HOME/.local/bin/uv}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/scratch/$USER/.cache/uv}"
PY=$(ls -d /scratch/$USER/.local/share/uv/python/cpython-3.12.*-linux-x86_64-gnu/bin/python3.12 | tail -1)
mkdir -p "$API_DIR"
cd "$API_DIR"
[ -x .venv-api/bin/python ] || "$UV" venv --python "$PY" .venv-api
"$UV" pip install --python .venv-api/bin/python --no-deps \
    fastapi==0.162.7 starlette==1.10.0 uvicorn==0.54.0 h11==0.16.0 click==8.5.1 \
    pydantic==2.14.3 pydantic_core==2.50.4 annotated-types==0.8.0 typing-inspection==0.4.5 typing_extensions==4.16.0 \
    annotated-doc==0.0.6 opentelemetry-api==1.45.2 anyio==4.16.0 idna==3.20 httpx==0.28.1 httpcore==1.0.9 certifi==2026.10.23 \
    python-multipart==0.0.33 psycopg==3.3.9 psycopg-binary==3.3.9 Pillow==12.3.0 numpy==2.2.6 \
    opencv-python-headless==4.13.0.92
.venv-api/bin/python -c "import fastapi, uvicorn, pydantic, httpx, psycopg, multipart, PIL, numpy, cv2; print('event API packages import')"
[ -d backend ] && API_TOKEN=only-for-this-check LOCAL_DATA_DIR="$(mktemp -d)" PYTHONPATH=. .venv-api/bin/python -c "from backend.run import create_application; create_application(); print('the event API builds')"
