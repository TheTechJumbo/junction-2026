# Simulated sensor events in Rust

Send one pill-box or door opening through the same `/events` contract as hardware.
No sensors, Python runtime, async runtime, background service, or image processing
are needed by the binary. Python is only used for integration tests and the existing
local store. This does not implement physical sensing, debouncing, or medication
confirmation: a pill-box opening is not proof that medication was taken.

## Build and run

Install stable Rust, then from the repository root:

```sh
cargo build --release --locked --manifest-path unoq/sensor-sim/Cargo.toml
unoq/sensor-sim/target/release/sensor-sim pillbox --dry-run
```

Start the existing store with `python3 unoq/store.py` and the `UNOQ_TOKEN` and
`UNOQ_DATA` settings described in `../README.md`. In the simulator's terminal,
set `UNOQ_TOKEN` privately to the same bearer token. It is never a CLI argument.

```sh
export UNOQ_BASE_URL=http://127.0.0.1:8091
unoq/sensor-sim/target/release/sensor-sim pillbox
unoq/sensor-sim/target/release/sensor-sim door --zone entrance
```

Press Enter to run either command at the desired demo moment. Each invocation is
one opening, with a fresh UUID prefixed `sim-` and current UTC timestamp. The CLI
prints `SIMULATED` on success; dry-run prints only JSON and sends nothing. The
existing timeline displays these as normal sensor events with `sim-` IDs; it does
not add a simulation badge. Use a demo store/profile, clearly describe the inputs
as simulated, and do not use these events as real household evidence.

`UNOQ_BASE_URL` (or `--base-url`) can point to Bartek's API using its bearer token
in `UNOQ_TOKEN`. No backend change is needed. HTTP is suitable for loopback/USB
forwarding; use HTTPS or an SSH tunnel for other hosts. Redirects are disabled.
No events or images are sent anywhere except the explicitly configured target.

## Contract and delivery

Pill-box payload: `{id:"sim-…",type:"pillbox_open",source:"sensor",ts:"…Z",
zone:"kitchen",object:"pillbox",evidence_url:null,confidence:1}`. Door events use
`type:"door_open",object:"door"`; set `--zone entrance` for the front door.
The ID prefix marks simulation without adding fields rejected by the APIs.

Three attempts by default, capped at five (`--attempts`), with 250 ms exponential
backoff. Each request times out after five seconds (`--timeout-seconds`, max 30).
Only transport failures, 408, 429 and 5xx retry. All retries reuse the exact body,
including ID and timestamp, so an accepted request with a lost response cannot
create duplicates on these idempotent stores. Other non-2xx responses fail.
Success means the API accepted the event, not that a downstream prompt played.
There is no offline queue: failure exits nonzero. A transport failure can mean
uncertain delivery. To manually replay, preserve **both** the ID and timestamp:

```sh
unoq/sensor-sim/target/release/sensor-sim pillbox --dry-run \
  --event-id sim-demo-1 --timestamp 2026-11-14T08:00:00Z
# Remove --dry-run to send that exact event; repeat identically for safe replay.
```

For reminders, run without `--timestamp` immediately after the reminder starts.
The clock on the sending device must be correct: the board's previously observed
clock issue still needs correction before board demos. These commands do not
alter the system clock. No heartbeat or evidence photo is generated.

## Verify

```sh
python3 -m unittest discover -s unoq/tests -v
```

Build first; simulator integration tests explicitly skip if the release binary is
missing. Tests cover actual store/timeline persistence, duplicate replay, conflicts,
authentication, bounded retries, stable retry payloads, redirect refusal, token
redaction, timestamps and invalid arguments. They use loopback and temporary data.

Build natively on the UNO Q Linux side with Rust/Cargo and a C compiler, then copy
the release binary to a local bin directory. The Mac binary cannot run on the board.
Real hardware integration can later replace the manual trigger with a debounced
input feeding the same event construction and transport. JUN-13 stays open until
real sensor inputs are validated end to end. Remove the binary to uninstall; no
service or startup behavior is installed.
