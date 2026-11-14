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
#   RECON_BASELINE_KEEP    baseline pictures kept from a recording (default 16): the room map (/viewer?live=ROOM)
#                          uses them all, in one pass
#   RECON_PICK             "similar" (default here): a fix uses the pictures that show most of what the phone sees
#   RECON_PATCH_SIM, RECON_NEW_VIEW   a photo whose patches are less than RECON_NEW_VIEW (0.5) mapped, at cosine
#                          similarity RECON_PATCH_SIM (0.6), shows a part of the room not sampled yet: it is added
#                          to the room's pictures and the room map is built again (measured 14 Nov: same room
#                          0.85 to 1.00 mapped, another room 0.00 to 0.02, an unsampled close-up 0.20)
#   RECON_ITEMS            items to look for; empty by default, because the item finder needs its own
#                          packages and weights (about 640 MB, see DEMO.md)
#   RECON_ITEMS_ENV        a folder with the item finder's packages (pkgs/) and weights (hf/hub/), made as in
#                          iridis/setup_items.sh: when it is there, the default items are looked for
#   RECON_ITEMS_DEVICE     where the item finder runs (default here: cpu; on a 6 GB card next to VGGT it was
#                          measured at the card's limit and once failed with a CUDA error)
set -euo pipefail
cd "$(dirname "$0")"
PY="${1:?give the Python that has torch and vggt, for example /path/to/venv-local/Scripts/python}"
DATA="${2:-$HOME/beacon-local}"
mkdir -p "$DATA"
BEACON_ROOT="$(cd "$DATA" && (pwd -W 2>/dev/null || pwd))"  # a Windows path under Git Bash
export BEACON_ROOT
export RECON_FRAMES="${RECON_FRAMES:-8,4}" RECON_LOCATE_BASELINE="${RECON_LOCATE_BASELINE:-5}"
export RECON_BASELINE_KEEP="${RECON_BASELINE_KEEP:-16}" RECON_PICK="${RECON_PICK:-similar}"
export RECON_PATCH_SIM="${RECON_PATCH_SIM:-0.6}" RECON_NEW_VIEW="${RECON_NEW_VIEW:-0.5}"
WORKER_PATH="${PYTHONPATH:-}"  # only the worker gets the item finder's packages, not the relay
if [ -n "${RECON_ITEMS_ENV:-}" ] && [ -d "$RECON_ITEMS_ENV/pkgs" ]; then
    ITEMS_ENV="$(cd "$RECON_ITEMS_ENV" && (pwd -W 2>/dev/null || pwd))"
    SEP=":"; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=";";; esac  # a Windows Python splits on ;
    WORKER_PATH="$ITEMS_ENV/pkgs${WORKER_PATH:+$SEP$WORKER_PATH}"
    export RECON_ITEMS_CACHE="${RECON_ITEMS_CACHE:-$ITEMS_ENV/hf/hub}" RECON_ITEMS_DEVICE="${RECON_ITEMS_DEVICE:-cpu}"
    [ -n "${RECON_ITEMS+set}" ] || export RECON_ITEMS="keys, glasses, wallet, phone, remote control"
    echo "item finder: on ($RECON_ITEMS) on the ${RECON_ITEMS_DEVICE}, from $ITEMS_ENV"
else
    export RECON_ITEMS="${RECON_ITEMS-}"
fi
rm -f "$BEACON_ROOT/STOP"
echo "worker: starting, log in $BEACON_ROOT/worker.log (the model takes up to a minute to load)"
PYTHONPATH="$WORKER_PATH" "$PY" -u live/worker.py > "$BEACON_ROOT/worker.log" 2>&1 &
WORKER=$!
trap 'touch "$BEACON_ROOT/STOP"; kill "$WORKER" 2>/dev/null || true' EXIT
python -u live/relay.py --local "$BEACON_ROOT" --quick "${@:3}"
