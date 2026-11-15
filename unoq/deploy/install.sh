#!/bin/sh
# Run on UNO Q from the directory containing store.py. Reuses existing token.
set -eu
install -d -m 700 /home/arduino/.config/junction /home/arduino/.local/share/junction
if [ ! -f /home/arduino/.config/junction/store.env ]; then
    (umask 077; python3 -c 'import secrets; print("UNOQ_TOKEN=" + secrets.token_urlsafe(32)); print("UNOQ_DATA=/home/arduino/.local/share/junction")' > /home/arduino/.config/junction/store.env)
fi
install -d -m 700 /home/arduino/.config/systemd/user
install -m 644 deploy/junction-store.service /home/arduino/.config/systemd/user/junction-store.service
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload
systemctl --user enable --now junction-store
