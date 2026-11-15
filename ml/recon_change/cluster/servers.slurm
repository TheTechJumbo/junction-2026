#!/bin/bash
# The demo's two servers on a CPU node of the cluster, wired to each other as run_demo.sh wires them on a laptop:
#   event API  backend/ (port 28000 here), from /scratch/$USER/junction-api, with its own venv .venv-api
#   relay      live/relay.py through live/relay_cluster.py (port 28090 here), in --local mode: its gateway runs here and
#              uses this folder (queue/, baselines/, recordings/), the one the GPU workers (worker.slurm) watch, and
#              recordings are trained by submitting cluster/video_map.slurm from here (no SSH hop)
# The laptop runs no server: ml/recon_change/tunnel_cluster.sh forwards its ports 8000 and 8090 to these two through the
# login node, and the phone keeps using http://127.0.0.1:8000 and :8090 over adb reverse.
#
#   bash cluster/servers.sh start | stop | restart | status | node      (on the login node, from this folder)
#
# Each job queues its own replacement when it starts (SERVERS_LEFT more after this one, default 2: three days at most),
# so after the walltime the servers come back by themselves within a minute or two, on another node; the tunnel
# follows them. Stopping cancels the replacement first (servers.sh stop).
# State, on scratch so a replacement carries on with it: the event API's database in $API_DIR/.data, the relay's files
# (rooms.json, recordings.json, trained splats, the day log, evidence) in relay-state/beacon-evidence.
# Secrets: API_TOKEN and RELAY_KEY from .servers.env (mode 600, copied from the laptop's .env.demo by
# tunnel_cluster.sh deploy), passed as environment only, never on a command line (other users on a shared node can read
# command lines). Logs in servers-logs/ (mode 700), with the key masked out of the links the relay prints.
#SBATCH --job-name=jun-servers
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=24:00:00
#SBATCH --output=slurm-%x-%j.out
set -uo pipefail
umask 077
cd "$SLURM_SUBMIT_DIR"
ROOT="$PWD"
API_DIR="${SERVERS_API_DIR:-/scratch/$USER/junction-api}"
API_DATA="${SERVERS_API_DATA:-$API_DIR/.data}"
STATE="${SERVERS_STATE:-$ROOT/relay-state}"  # the relay's evidence folder is $STATE/beacon-evidence
API_PORT="${SERVERS_API_PORT:-28000}"
RELAY_PORT="${SERVERS_RELAY_PORT:-28090}"
ZONE="${SERVERS_ZONE:-kitchen}"  # the room the event API asks the relay about
LEFT="${SERVERS_LEFT:-2}"
LOGS="$ROOT/servers-logs"
mkdir -p "$STATE" "$API_DATA" "$LOGS" && chmod 700 "$LOGS"
echo "== $(hostname) $(date +%T) job $SLURM_JOB_ID: event API on port $API_PORT, relay on port $RELAY_PORT"

key() { grep -m1 "^$1=" "$ROOT/.servers.env" 2>/dev/null | cut -d= -f2-; }
API_TOKEN="$(key API_TOKEN)"
RELAY_KEY="$(key RELAY_KEY)"
if [ -z "$API_TOKEN" ] || [ -z "$RELAY_KEY" ]; then
    echo "no API_TOKEN or RELAY_KEY in $ROOT/.servers.env: run tunnel_cluster.sh deploy on the laptop"
    exit 1
fi
for port in "$API_PORT" "$RELAY_PORT"; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        echo "port $port is taken on $(hostname) by someone else: change SERVERS_API_PORT / SERVERS_RELAY_PORT"
        exit 1
    fi
done

# The replacement, queued now so it is first in line when this job ends. Submitted without this job's own Slurm
# variables (a job submitted from inside a job would inherit them).
if [ "$LEFT" -gt 0 ] && [ -z "$(squeue -u "$USER" -h -n "$SLURM_JOB_NAME" -t PENDING -o %i)" ]; then
    # shellcheck disable=SC2046
    next=$(env $(compgen -e | grep -E '^(SLURM|SBATCH|SRUN)_' | sed 's/^/-u /') SERVERS_LEFT=$((LEFT - 1)) \
        sbatch --parsable --job-name="$SLURM_JOB_NAME" --dependency=afterany:"$SLURM_JOB_ID" cluster/servers.slurm)
    echo "replacement queued: job $next (it queues $((LEFT - 1)) more)"
fi

api() {
    cd "$API_DIR" || return 1
    API_TOKEN="$API_TOKEN" LOCAL_DATA_DIR="$API_DATA" PYTHONPATH="$API_DIR" \
    VISION_RELAY_URL="http://127.0.0.1:$RELAY_PORT" VISION_RELAY_KEY="$RELAY_KEY" VISION_RELAY_ZONE="$ZONE" \
    NAVIGATION_MAX_AGE_SECONDS=120 NAVIGATION_MIN_CONFIDENCE=0.5 \
        "$API_DIR/.venv-api/bin/python" -m uvicorn backend.run:create_application --factory \
        --host 0.0.0.0 --port "$API_PORT" --no-access-log
}

relay() {
    cd "$ROOT" || return 1
    TMPDIR="$STATE" RELAY_KEY="$RELAY_KEY" EVENT_API_URL="http://127.0.0.1:$API_PORT/events" EVENT_API_TOKEN="$API_TOKEN" \
    BEACON_REMOTE_ROOT="$ROOT" RELAY_ANONYMISE="${RELAY_ANONYMISE:-0}" \
        .venv-recon/bin/python -u live/relay_cluster.py --local "$ROOT" --quick --train-host local \
        --host 0.0.0.0 --port "$RELAY_PORT"
}

keep() {  # run one server, and start it again if it stops
    local name=$1 code
    while true; do
        echo "$(date +%T) $name: starting (log servers-logs/$name-$SLURM_JOB_ID.log)"
        # the relay prints its links with the key in them (?k=...): the log keeps them without it
        "$name" 2>&1 | sed -u -E 's/([?&]k=)[A-Za-z0-9_-]+/\1KEY/g' >> "$LOGS/$name-$SLURM_JOB_ID.log"
        code=${PIPESTATUS[0]}
        echo "$(date +%T) $name: stopped with $code; starting again in 5 s"
        sleep 5
    done
}

keep api &
keep relay &
wait
