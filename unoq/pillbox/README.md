# Standalone pill-box delivery

The sketch keeps the existing 7 cm threshold, three-reading debounce and initial open/closed behavior. Bridge exposes an in-memory FIFO of up to 32 openings. Linux polls at 100 ms intervals and acknowledges an opening only after its event is committed to SQLite with WAL and synchronous FULL. The session ID stays on the MCU until MCU reset. Each event uses that session and sequence number, so reader restarts and lost acknowledgments preserve producer identity.

The service waits for network clock synchronization on each Linux boot. It reconstructs observation time from current UTC minus the opening's MCU age. After synchronization, Wi-Fi loss does not prevent local persistence. The sender retries with bounded backoff and the original body, ID and timestamp. HTTP errors retain the event and appear in the journal. Successful delivery requires a matching canonical event ID and a durable API receipt. Credentials live in owner-only configuration outside Git.

The MCU FIFO is RAM-only. More than 32 unacknowledged openings produces a visible overflow count. An MCU power loss before Linux has persisted an opening can lose it. SQLite preserves already recorded events across reader and board restarts.

## Installation

Install requirements in a dedicated board virtual environment at `/home/arduino/junction-pillbox-env`. Copy this directory to `/home/arduino/junction/unoq/pillbox`. Create `~/.config/junction/pillbox.env` with mode 0600 containing `PILLBOX_API_URL` and `API_TOKEN`. Optional `PILLBOX_QUEUE` changes the SQLite path, defaulting to `~/.local/share/junction/pillbox.sqlite`.

Copy `junction-pillbox.service` into `~/.config/systemd/user/`, run `systemctl --user daemon-reload`, then `systemctl --user enable --now junction-pillbox`. Linger must be enabled for the user. Check `journalctl --user -u junction-pillbox` for startup, clock, Bridge, API/authentication and timing messages.

Compile `arduino/arduino_binary_sketch` with UNO Q core 0.54.1 and installed RouterBridge dependencies. Select `wait_linux_boot=no` for autonomous sketch startup. Flashing and live MCU validation are recorded separately below.

The Mac demo backend can be started from this checkout using its configured private environment file and `scripts/run_local.py --host 0.0.0.0 --port 8000`. The current board target is `http://10.0.41.83:8000`. Update the private board environment and restart its service when the backend address changes. The Mac must remain powered and connected for central API delivery.

## Verification so far

- Junction2026 profile saved on the board with autoconnect enabled. Board address observed as 10.0.33.248.
- NTP synchronized automatically after Wi-Fi connection and corrected UTC. The RTC previously reported 1970, so boot-time synchronization is necessary.
- Mac backend launched with a new private demo token and isolated local data under the user's configuration directory. Existing provider credentials were absent.
- Ten synthetic events in the validation zone were accepted over Wi-Fi. Delivery latency median 185.9 ms, maximum 1456.9 ms. These measure HTTP delivery using a test queue, including request completion, not physical sensor response time.
- Actual Wi-Fi disconnect, queued synthetic event, queue reopen and Wi-Fi reconnect passed with the original event ID.
- The installed service retained a queued synthetic event across a reader restart during a Wi-Fi outage. After reconnect it delivered the event, and the API contained exactly one row for its ID. One reconnect attempt failed to obtain a DHCP lease. A retry restored the connection. The saved profile now retries autoconnect indefinitely.
- A synthetic opening persisted on the board passed through the installed sender to the real Mac API, completed an active reminder in the relay, and appeared in the day timeline. Dashboard and day-view pages returned HTTP 200. This used an isolated temporary relay and exercised its real sensor integration.
- Board and Mac UTC epoch seconds matched during a paired check. The installed service is enabled and active, and Bridge reports no queue overflow.
- Queue identity/restart, authentication failure, malformed receipt and successful delivery unit tests passed.
- `make check` passed 470 tests, lint and schema checks.

The stock uploader encountered an OpenOCD flash-write timeout. Direct reset/halt flashing succeeded, but the MCU core differed from the installed 0.54.1 toolchain and the sketch did not start. A 2 MiB backup was saved on the board at `/home/arduino/junction-mcu-backup.bin` before updating the MCU core. The matching 0.54.1 core and automatic-start sketch were then flashed successfully. Bridge `pillbox_peek` responds and the Linux reader restarts successfully. The backup captures the MCU at the diagnostic stage, including earlier sketch-flash attempts, and is not a verified working original sketch image.

Physical opening, physical sensor-to-API timing, visible dashboard update, physical-event reminder completion and external-power/USB-free boot await physical testing. No actual sensor opening was observed at this acceptance checkpoint. A Linux reboot command requires interactive administrator authentication on this board, so automatic startup after a full board reboot remains unverified. JUN-13 remains In Progress.

## Rollback

Stop and disable `junction-pillbox` to pause delivery. Keep its SQLite database for later retry. Restore the original sensor sketch from the base Git commit if needed, preserving the currently wired pins. Disable the dedicated Wi-Fi profile only if desired. No Wi-Fi password or API token is stored in the repository.

## Private demo configuration

The Mac demo environment is `/Users/ayman/.config/junction-demo/backend.env`, mode 0600. It contains a newly generated shared API token and an isolated local-data directory. The phone must use the same API token when connecting to this demo backend. The board's matching token is in `/home/arduino/.config/junction/pillbox.env`, mode 0600. Provider credentials are not configured in this demo environment.

Source changes are prepared in branch `codex/jun13-standalone`. No firmware, database, credentials or recordings are committed.
