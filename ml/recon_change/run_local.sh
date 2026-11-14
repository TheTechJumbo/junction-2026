#!/usr/bin/env bash
# Run the vision stage on this machine, with no cluster: the worker (the models) and the relay (the pages).
#
#   bash run_local.sh <python that has torch and vggt> [data folder] [more relay options, e.g. --host 0.0.0.0]
#
# The data folder holds the room baselines and the job queue (default: ~/beacon-local). The relay prints the
# link to open. Stop with Ctrl+C: the worker is stopped too.
#
# Settings for a laptop graphics card with about 6 GB (override by exporting them first):
#   RECON_LOCATE_BASELINE  baseline photos used for one position fix (default 5; the cluster uses 12)
#   RECON_FRAMES           baseline,new frames for a change check (default 8,4; the cluster uses 24,12)
#   RECON_ITEMS            items to look for; empty by default, because the item finder needs its own
#                          packages and about 2.5 GB more of the graphics card
set -euo pipefail
cd "$(dirname "$0")"
PY="${1:?give the Python that has torch and vggt, for example /path/to/venv-local/Scripts/python}"
DATA="${2:-$HOME/beacon-local}"
mkdir -p "$DATA"
BEACON_ROOT="$(cd "$DATA" && (pwd -W 2>/dev/null || pwd))"  # a Windows path under Git Bash
export BEACON_ROOT
export RECON_FRAMES="${RECON_FRAMES:-8,4}" RECON_LOCATE_BASELINE="${RECON_LOCATE_BASELINE:-5}" RECON_ITEMS="${RECON_ITEMS-}"
rm -f "$BEACON_ROOT/STOP"
echo "worker: starting, log in $BEACON_ROOT/worker.log (the model takes up to a minute to load)"
"$PY" -u live/worker.py > "$BEACON_ROOT/worker.log" 2>&1 &
WORKER=$!
trap 'touch "$BEACON_ROOT/STOP"; kill "$WORKER" 2>/dev/null || true' EXIT
python -u live/relay.py --local "$BEACON_ROOT" "${@:3}"
