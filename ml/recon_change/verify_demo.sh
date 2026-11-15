#!/usr/bin/env bash
# Check every stage of the demo end to end against the servers that run_demo.sh started (best with --local):
# the event API and the relay, a synthetic room's baseline, named places, position fixes with the arrow and the
# 3D map, "Where is the pill box?", a pill box opening and "Did I take my pills?", the cheap gate, a change check,
# the item finder, and what the dashboard shows. Prints PASS, FAIL or SKIPPED per stage with its seconds and the
# peak GPU memory of its model job; exits 1 if anything FAILED. Safe to run again (its own zone, verify-synthetic).
#
#   bash ml/recon_change/verify_demo.sh                    from the top of the repo, in Git Bash
#   bash ml/recon_change/verify_demo.sh --skip-pill-event  keep today's pill answer as it is
#
# The two secrets are read from .env.demo (made by run_demo.sh) and never printed.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
KEYS="$REPO/.env.demo"
if [ ! -f "$KEYS" ]; then
    echo "No $KEYS: start the servers first with bash ml/recon_change/run_demo.sh --local PYTHON" >&2
    exit 1
fi
RELAY_KEY="$(grep '^RELAY_KEY=' "$KEYS" | cut -d= -f2-)"
API_TOKEN="$(grep '^API_TOKEN=' "$KEYS" | cut -d= -f2-)"
export RELAY_KEY API_TOKEN
exec python -u "$REPO/ml/recon_change/verify_demo.py" "$@"
