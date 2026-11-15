#!/usr/bin/env bash
# The demo with every server on the cluster: this laptop runs no server and no model, only an SSH tunnel. Its ports 8000
# (event API) and 8090 (relay) lead through the login node to the servers job (cluster/servers.slurm, a CPU node), and
# the phone reaches them at http://127.0.0.1:8000 and :8090 over adb reverse, as before. Needs the VPN.
#
#   bash ml/recon_change/tunnel_cluster.sh            start: the servers job if it is not there, then the tunnel (Ctrl+C
#                                                    ends the tunnel only; the servers keep running on the cluster)
#   bash ml/recon_change/tunnel_cluster.sh stop       stop everything: this tunnel and the servers job
#   bash ml/recon_change/tunnel_cluster.sh status     the servers job, and both servers through the tunnel
#   bash ml/recon_change/tunnel_cluster.sh restart    restart the servers job (after deploy); the tunnel follows
#   bash ml/recon_change/tunnel_cluster.sh deploy     copy the relay's and the event API's code and the two secrets
#
# The tunnel follows the servers: when the job is replaced (walltime, restart) and they come back on another node, it
# reconnects there by itself and runs adb reverse again.
# Settings: TUNNEL_BIND (0.0.0.0: also the laptop's Wi-Fi address, the phone app's fallback; 127.0.0.1: this laptop
# and adb only), TUNNEL_API_PORT / TUNNEL_RELAY_PORT (8000 / 8090), TUNNEL_ADB=0 (no adb reverse),
# BEACON_TRAIN_HOST (SSH alias, cluster), ADB (adb.exe).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
HOST="${BEACON_TRAIN_HOST:-cluster}"
ROOT="${BEACON_REMOTE_ROOT:-/scratch/ase1g23/junction-recon}"
API_DIR="${SERVERS_API_DIR:-/scratch/ase1g23/junction-api}"
NODE_API_PORT="${SERVERS_API_PORT:-28000}" NODE_RELAY_PORT="${SERVERS_RELAY_PORT:-28090}"
NAME="${SERVERS_NAME:-jun-servers}"  # another name: a test servers job, which this then only connects to
BIND="${TUNNEL_BIND:-0.0.0.0}"
API_PORT="${TUNNEL_API_PORT:-8000}" RELAY_PORT="${TUNNEL_RELAY_PORT:-8090}"
ADB="${ADB:-/c/Users/User/platform-tools/adb.exe}"
PIDFILE="${TMPDIR:-/tmp}/beacon-tunnel-$API_PORT.pid"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15)

on_cluster() { ssh "${SSH_OPTS[@]}" "$HOST" "bash -lc \"cd $ROOT && $*\""; }
servers() { on_cluster "SERVERS_NAME=$NAME SERVERS_API_PORT=$NODE_API_PORT SERVERS_RELAY_PORT=$NODE_RELAY_PORT bash cluster/servers.sh $1"; }
node_now() { servers node 2>/dev/null | tr -d '\r' | grep -E '^[a-z][a-z0-9-]*$' | tail -1; }

adb_reverse() {
    [ "${TUNNEL_ADB:-1}" = "0" ] && return
    if [ ! -x "$ADB" ]; then echo "adb: not found at $ADB (set ADB=...)"; return; fi
    # Git Bash would turn tcp:8000 into a path for a Windows program without this
    if MSYS_NO_PATHCONV=1 "$ADB" reverse "tcp:$API_PORT" "tcp:$API_PORT" >/dev/null 2>&1 \
            && MSYS_NO_PATHCONV=1 "$ADB" reverse "tcp:$RELAY_PORT" "tcp:$RELAY_PORT" >/dev/null 2>&1; then
        echo "adb reverse: the phone's 127.0.0.1:$API_PORT and :$RELAY_PORT lead here"
    else
        echo "adb reverse: no phone on USB (plug it in; this runs again at the next reconnect, or run it by hand)"
    fi
}

deploy() {
    cd "$HERE" || exit 1
    local relay_files=(live/relay.py live/relay_cluster.py live/remote_train.py live/*.html track.py day.py route.py
                       cluster/servers.slurm cluster/servers.sh cluster/setup_api_env.sh)
    for f in live/anonymise.py live/care_space.py; do [ -f "$f" ] && relay_files+=("$f"); done
    echo "relay: ${#relay_files[@]} files to $HOST:$ROOT"
    tar -cf - "${relay_files[@]}" | ssh "${SSH_OPTS[@]}" "$HOST" "bash -lc \"cd $ROOT && tar -xf - && sed -i 's/\r\$//' cluster/servers.slurm cluster/servers.sh cluster/setup_api_env.sh &&.venv-recon/bin/python -m py_compile live/relay.py live/relay_cluster.py live/remote_train.py && echo '  compiled'\""
    # gateway.py, recon_change.py and recon_rust.py are the workers' too: deploy_from_laptop.sh sends them (it restarts
    # the workers). Here they are only compared.
    for f in live/gateway.py recon_change.py recon_rust.py; do
        here=$(tr -d '\r' < "$f" | md5sum | cut -c1-32)  # the checkout has CRLF line ends, the cluster LF
        there=$(ssh "${SSH_OPTS[@]}" "$HOST" "tr -d '\r' < $ROOT/$f | md5sum" | cut -c1-32)
        [ "$here" = "$there" ] || echo "  note: $f differs from the one on the cluster (cluster/deploy_from_laptop.sh sends it)"
    done
    cd "$REPO" || exit 1
    local api_files
    api_files=$(git ls-files -co --exclude-standard backend contracts dashboard | grep -vE '(^|/)tests/|__pycache__')
    echo "event API: $(echo "$api_files" | wc -l) files to $HOST:$API_DIR"
    echo "$api_files" | tar -cf - -T - | ssh "${SSH_OPTS[@]}" "$HOST" "bash -lc \"mkdir -p $API_DIR && cd $API_DIR && tar -xf -\""
    # The two secrets, never printed: only API_TOKEN and RELAY_KEY from .env.demo, into a file only this user can read.
    grep -E '^(API_TOKEN|RELAY_KEY)=' "$REPO/.env.demo" | ssh "${SSH_OPTS[@]}" "$HOST" \
        "bash -lc \"umask 077 && cat > $ROOT/.servers.env.new && mv $ROOT/.servers.env.new $ROOT/.servers.env && chmod 600 $ROOT/.servers.env\"" \
        && echo "secrets: API_TOKEN and RELAY_KEY in $ROOT/.servers.env (mode 600)"
    echo "deployed. A running servers job keeps the old code until: bash ml/recon_change/tunnel_cluster.sh restart"
}

status() {
    servers status
    for p in "$API_PORT:/health" "$RELAY_PORT:/status"; do
        echo "through the tunnel, 127.0.0.1:${p%%:*}${p#*:}: $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$p")"
    done
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "tunnel: running (pid $(cat "$PIDFILE"))"; else echo "tunnel: not running"; fi
}

stop_tunnel() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")" && echo "tunnel stopped"
    fi
    rm -f "$PIDFILE"
}

tunnel() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "a tunnel is already running (pid $(cat "$PIDFILE")): bash $0 stop first"; exit 1
    fi
    echo $$ > "$PIDFILE"
    local ssh_pid="" node fails
    trap '[ -n "$ssh_pid" ] && kill "$ssh_pid" 2>/dev/null; rm -f "$PIDFILE"; exit 0' INT TERM
    trap '[ -n "$ssh_pid" ] && kill "$ssh_pid" 2>/dev/null; rm -f "$PIDFILE"' EXIT
    if [ "$NAME" = jun-servers ]; then servers start; fi  # a test job is submitted by hand, with its own state
    while true; do
        node=$(node_now)
        if [ -z "$node" ]; then
            echo "$(date +%T) the servers job is not running yet (queued, or between jobs); looking again in 15 s"
            sleep 15
            continue
        fi
        echo "$(date +%T) tunnel: $BIND:$API_PORT -> $node:$NODE_API_PORT (event API), $BIND:$RELAY_PORT -> $node:$NODE_RELAY_PORT (relay)"
        ssh -N "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
            -L "$BIND:$API_PORT:$node:$NODE_API_PORT" -L "$BIND:$RELAY_PORT:$node:$NODE_RELAY_PORT" "$HOST" &
        ssh_pid=$!
        sleep 4
        adb_reverse
        fails=0
        while kill -0 "$ssh_pid" 2>/dev/null; do
            sleep 15
            if curl -s -o /dev/null --max-time 6 "http://127.0.0.1:$API_PORT/health"; then
                fails=0
                continue
            fi
            fails=$((fails + 1))
            # Twice in a row no answer: has the job moved (walltime, restart)? Then follow it.
            if [ "$fails" -ge 2 ] && [ "$(node_now)" != "$node" ]; then
                echo "$(date +%T) the servers left $node: reconnecting"
                kill "$ssh_pid" 2>/dev/null
                break
            fi
        done
        wait "$ssh_pid" 2>/dev/null
        ssh_pid=""
        sleep 3
    done
}

case "${1:-start}" in
    start) tunnel ;;
    stop) stop_tunnel; [ "$NAME" = jun-servers ] && servers stop ;;
    status) status ;;
    restart) servers restart ;;
    deploy) deploy ;;
    *) echo "usage: bash $0 [start|stop|status|restart|deploy]"; exit 2 ;;
esac
