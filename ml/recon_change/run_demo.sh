#!/usr/bin/env bash
# Start the demo's two servers on this laptop, wired to each other: the team's event API (port 8000) and
# the vision relay (port 8090). See DEMO.md next to this file for the whole walk-through with the glasses.
#
#   bash ml/recon_change/run_demo.sh                 the models run on the GPU cluster (needs the VPN)
#   bash ml/recon_change/run_demo.sh --local PYTHON  the models run on this machine (PYTHON has torch and vggt)
#   bash ml/recon_change/run_demo.sh --cluster [stop|status|restart|deploy]
#                                                    nothing runs here: both servers run on the cluster too (a CPU job,
#                                                    cluster/servers.slurm) and this laptop only keeps an SSH tunnel to
#                                                    them on ports 8000 and 8090 (tunnel_cluster.sh)
#
# The two secrets (the API token and the relay's access key) are made once and kept in .env.demo at the top of
# the repo, which Git ignores. This script never prints them; the relay prints its own link when it starts.
# ZONE=lounge bash ... chooses the room the event API asks about (default: kitchen).
set -euo pipefail
if [ "${1:-}" = "--cluster" ]; then
    exec bash "$(dirname "$0")/tunnel_cluster.sh" "${@:2}"
fi
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
KEYS="$REPO/.env.demo"
if [ ! -f "$KEYS" ]; then
    python -c "import secrets; print('API_TOKEN=' + secrets.token_urlsafe(24)); print('RELAY_KEY=' + secrets.token_urlsafe(12))" > "$KEYS"
    echo "made $KEYS (API_TOKEN for the Android app, RELAY_KEY for the relay's pages)"
fi
API_TOKEN="$(grep '^API_TOKEN=' "$KEYS" | cut -d= -f2-)"
RELAY_KEY="$(grep '^RELAY_KEY=' "$KEYS" | cut -d= -f2-)"

API_PY=.venv/Scripts/python
[ -x "$API_PY" ] || API_PY=.venv/bin/python
if [ ! -x "$API_PY" ]; then
    echo "event API: setting up its Python environment once (a few minutes)"
    python -m venv .venv
    API_PY=.venv/Scripts/python
    [ -x "$API_PY" ] || API_PY=.venv/bin/python
    "$API_PY" -m pip install -q --disable-pip-version-check -r requirements-dev.lock
fi

LOG="${TMPDIR:-/tmp}/beacon-event-api.log"
API_TOKEN="$API_TOKEN" VISION_RELAY_URL=http://127.0.0.1:8090 VISION_RELAY_KEY="$RELAY_KEY" VISION_RELAY_ZONE="${ZONE:-kitchen}" \
    NAVIGATION_MAX_AGE_SECONDS=120 NAVIGATION_MIN_CONFIDENCE=0.5 PYTHONPATH=. \
    "$API_PY" scripts/run_local.py --port 8000 > "$LOG" 2>&1 &
API=$!
trap 'kill "$API" 2>/dev/null || true' EXIT
echo "event API: http://127.0.0.1:8000 (log: $LOG); its token is API_TOKEN in .env.demo"

export EVENT_API_URL=http://127.0.0.1:8000/events EVENT_API_TOKEN="$API_TOKEN"
cd ml/recon_change
if [ "${1:-}" = "--local" ]; then
    bash run_local.sh "${2:?give the Python that has torch and vggt}" "${3:-$HOME/beacon-local}" --host 0.0.0.0 --port 8090 --token "$RELAY_KEY"
else
    # the models on the GPU cluster (VPN): instant fixes here, recordings trained there
    python -u live/relay.py --host 0.0.0.0 --port 8090 --token "$RELAY_KEY" --quick --train-host "${BEACON_TRAIN_HOST-cluster}"
fi
