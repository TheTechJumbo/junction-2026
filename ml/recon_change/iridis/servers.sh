#!/bin/bash
# The servers job (iridis/servers.slurm: the event API and the relay on a CPU node). Run on the login node; the
# laptop runs it through tunnel_iridis.sh.
#   bash iridis/servers.sh start     submit the job, unless one is running or queued (it queues its own replacement)
#   bash iridis/servers.sh stop      cancel the queued replacement, then the running job, and wait for it to end
#   bash iridis/servers.sh restart   stop, then start: after new code was deployed
#   bash iridis/servers.sh status    the jobs, and whether both servers answer on their node
#   bash iridis/servers.sh node      the node the running job is on (nothing while none runs)
source /etc/profile
cd /scratch/$USER/junction-recon || exit 1
API_PORT="${SERVERS_API_PORT:-28000}" RELAY_PORT="${SERVERS_RELAY_PORT:-28090}"
NAME="${SERVERS_NAME:-jun-servers}"  # another name: a test job (see tunnel_iridis.sh)

ids() { squeue -u "$USER" -h -n "$NAME" -t "$1" -o %i | tr '\n' ' '; }
node() { squeue -u "$USER" -h -n "$NAME" -t RUNNING -o %N | head -1; }

stop() {
    local queued running
    queued=$(ids PENDING)
    [ -n "${queued// }" ] && scancel $queued && echo "cancelled the queued replacement: $queued"
    running=$(ids RUNNING)
    [ -n "${running// }" ] && scancel $running && echo "cancelled the running job: $running"
    for _ in $(seq 1 30); do [ -z "$(ids RUNNING,PENDING,COMPLETING)" ] && break; sleep 2; done
    queued=$(ids PENDING)  # a replacement queued in the moment between the two cancels
    [ -n "${queued// }" ] && scancel $queued && echo "cancelled a late replacement: $queued"
    return 0
}

start() {
    local there
    there=$(ids RUNNING,PENDING)
    if [ -n "${there// }" ]; then
        echo "already there: $(squeue -u "$USER" -h -n "$NAME" -o '%i %T %N %L' | tr '\n' ';')"
        return 0
    fi
    umask 077  # the job's output file is made with this mask
    echo "submitted: job $(sbatch --parsable --job-name="$NAME" iridis/servers.slurm)"
}

case "${1:-status}" in
    start) start ;;
    stop) stop ;;
    restart) stop && start ;;
    node) node ;;
    status)
        squeue -u "$USER" -h -n "$NAME" -o '%i %T %M elapsed, %L left, %N %r'
        n=$(node)
        if [ -n "$n" ]; then
            # /health needs no key; the relay answers 403 without its key, which is enough to know it is up
            echo "event API on $n:$API_PORT: $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$n:$API_PORT/health")"
            echo "relay on $n:$RELAY_PORT: $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$n:$RELAY_PORT/status")"
        fi ;;
    *) echo "usage: bash iridis/servers.sh start|stop|restart|status|node"; exit 2 ;;
esac
