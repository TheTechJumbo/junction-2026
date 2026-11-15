#!/usr/bin/env bash
# Record a room normally, then turn the video into a Gaussian splat on the workers' GPU machine (Iridis; video_map.py
# there, started by live/remote_train.py), and see it on the laptop.
#
#   bash ml/recon_change/video_map.sh glasses [NAME]    the newest video the glasses saved on the phone (Meta AI app)
#   bash ml/recon_change/video_map.sh phone [NAME]      the newest video of the phone's own camera
#   bash ml/recon_change/video_map.sh FILE.mp4 [NAME]   a video already on the laptop
#   STEPS=7000 KEYFRAMES=96 bash ... (the defaults)
#
# Needs: the phone on USB (adb on PATH or in ~/platform-tools) for glasses/phone, the VPN for Iridis, and the
# relay running (run_demo.sh) to show the result. The video and the splats stay out of the repo: on the laptop in the
# system's temporary folder, on the GPU machine in ROOT/trained/video-NAME. Prints the links to open (the key masked).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SRC="${1:?glasses, phone or a video file}"
ADB="$(command -v adb || echo "$HOME/platform-tools/adb.exe")"
TMPD="$(cd "${TMP:-${TEMP:-/tmp}}" && (pwd -W 2>/dev/null || pwd))"
case "$SRC" in
    glasses) DIR="/sdcard/Download/Meta AI" ;;
    phone) DIR="/sdcard/DCIM/Camera" ;;
    *) DIR="" ;;
esac
if [ -n "$DIR" ]; then
    NEWEST="$(MSYS_NO_PATHCONV=1 "$ADB" shell "ls -t '$DIR'/*.mp4 2>/dev/null | head -1" | tr -d '\r')"
    [ -n "$NEWEST" ] || { echo "No video in $DIR on the phone." >&2; exit 1; }
    NAME="${2:-$(basename "$NEWEST" .mp4 | tr -c 'A-Za-z0-9_\n-' '-')}"
    LOCAL="$TMPD/video-map/$NAME"
    mkdir -p "$LOCAL"
    echo "pulling $NEWEST"
    MSYS_NO_PATHCONV=1 "$ADB" pull "$NEWEST" "$LOCAL/video.mp4" > /dev/null
else
    NAME="${2:-$(basename "$SRC" | sed 's/\.[^.]*$//' | tr -c 'A-Za-z0-9_\n-' '-')}"
    LOCAL="$TMPD/video-map/$NAME"
    mkdir -p "$LOCAL"
    cp "$SRC" "$LOCAL/video.mp4"
fi
OUT="$TMPD/beacon-evidence/video-$NAME"
echo "training on ${BEACON_TRAIN_HOST:-iridis} with the code deployed for the workers (iridis/deploy_from_laptop.sh)"
python -u "$HERE/live/remote_train.py" "$LOCAL/video.mp4" "video-$NAME" "$OUT" --steps "${STEPS:-7000}" --keyframes "${KEYFRAMES:-96}" | tail -12
echo "renders (the splat next to the photo): $OUT/renders"
cat "$OUT/summary.json" 2>/dev/null | head -30
echo
echo "See it (key: RELAY_KEY in .env.demo):"
for f in trained untrained; do
    [ -f "$OUT/$f.splat" ] && echo "  http://127.0.0.1:8090/viewer?d=1&src=%2Fevidence%2Fvideo-$NAME%2F$f.splat%3Fk%3D<RELAY_KEY>&k=<RELAY_KEY>"
done
