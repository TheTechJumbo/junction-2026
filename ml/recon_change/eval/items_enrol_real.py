#!/usr/bin/env python3
"""Score a real clip of one of the wearer's own things: show it once, then count how often it is
found again. Made for the clip of Seb's keys, phone and wallet; it has not been run on one yet.

  python eval/items_enrol_real.py --clip keys.mp4 --item keys --out out/real-keys
      [--frames 40]                 frames taken from the clip, spread evenly (sharpest of each stretch)
      [--enrol a.jpg b.jpg]         pictures to enrol from, instead of the first sighting in the clip
      [--box u0 v0 u1 v1]           where the thing is in the first frame (0..1), if the words do not find it
      [--absent]                    the thing is NOT in this clip: every answer is then a false one

Enrolment is from the first sighting: the first frame in which the words find the item at today's
limit (or --box). Every later frame is then asked three ways: the words at today's limit, the words
with the settings in force (limits per item, second look, judge), and the enrolled picture.

There is no truth for a home clip, so a frame "with an answer" is not yet a frame "found": the
script writes a sheet of every answer (words green, enrolled pink) for a person to check, and
frames.json with every box. With --absent it needs no check: answers are false answers.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import items  # noqa: E402
import recon_change as rc  # noqa: E402


def shrink(picture):
    scale = items.BIG_SIDE / max(picture.shape[:2])
    return picture if scale >= 1 else cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def cut(picture, box, margin=0.2):
    H, W = picture.shape[:2]
    w, h = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    return picture[int(max(box[1] * H - margin * h, 0)):int(box[3] * H + margin * h), int(max(box[0] * W - margin * w, 0)):int(box[2] * W + margin * w)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True, help="a video, or a folder of frames")
    ap.add_argument("--item", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--enrol", nargs="*")
    ap.add_argument("--box", type=float, nargs=4)
    ap.add_argument("--absent", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = " ".join(args.item.lower().split())
    frames = [shrink(f) for f in rc.sample_frames(args.clip, args.frames) if f is not None]
    detector = items.load()

    first = 0
    if args.enrol:
        shown = [shrink(cv2.imread(f)) for f in args.enrol]
    elif args.box:
        shown = [cut(frames[0], args.box)]
    else:
        shown = None
        for first, frame in enumerate(frames):
            seen = detector.find(frame, [name], score=items.SCORE)
            if seen:
                shown = [cut(frame, seen[0]["box"])]
                break
        if shown is None:
            raise SystemExit(f"The words never found '{name}' at {items.SCORE} in {len(frames)} frames: give --box or --enrol.")
    used = detector.enrol(name, shown)
    for i, picture in enumerate(shown):
        cv2.imwrite(str(out / f"enrolled_{i}.jpg"), picture)
    print(f"{len(frames)} frames; enrolled '{name}' from {used} picture(s)" + ("" if args.enrol or args.box else f", first sighting in frame {first}"), flush=True)

    later = frames if args.enrol else frames[first + 1:]
    rows, cells = [], []
    count = {"frames": len(later), "words today": 0, "words with settings": 0, "enrolled": 0, "either": 0}
    for n, frame in enumerate(later):
        every = detector.candidates(frame, [name], floor=0.25)
        words = [d for d in every if d["matched"] == "text"]
        mine = [d for d in every if d["matched"] == "enrolled"]
        today = any(d["score"] >= items.SCORE for d in words)
        settings = any(d["accepted"] for d in words)
        enrolled = any(d["accepted"] for d in mine)
        for key, hit in (("words today", today), ("words with settings", settings), ("enrolled", enrolled), ("either", settings or enrolled)):
            count[key] += hit
        rows.append({"frame": n, "words": words[:3], "enrolled": mine[:3]})
        H, W = frame.shape[:2]
        for d, colour in [(d, (60, 220, 60)) for d in words[:1]] + [(d, (200, 90, 255)) for d in mine[:1]]:
            b = d["box"]
            cv2.rectangle(frame, (int(b[0] * W), int(b[1] * H)), (int(b[2] * W), int(b[3] * H)), colour, 3 if d["accepted"] else 1)
            cv2.putText(frame, f"{d['matched']} {d['score']:.2f}", (int(b[0] * W), max(14, int(b[1] * H) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        cells.append(cv2.resize(frame, (320, int(320 * later[0].shape[0] / later[0].shape[1]))))
    (out / "frames.json").write_text(json.dumps({"item": name, "count": count, "settings": detector.settings([name]), "frames": rows}))
    if cells:
        cells += [np.zeros_like(cells[0])] * (-len(cells) % 5)
        cv2.imwrite(str(out / "sheet.jpg"), np.concatenate([np.concatenate(cells[i:i + 5], 1) for i in range(0, len(cells), 5)], 0),
                    [cv2.IMWRITE_JPEG_QUALITY, 80])
    what = "false answers (the thing is not in the clip)" if args.absent else "frames with an answer (check them on sheet.jpg: thick box = accepted)"
    print(f"{what}, of {count['frames']} frames:")
    for key in ("words today", "words with settings", "enrolled", "either"):
        print(f"  {key}: {count[key]}")
    print(f"written: {out / 'sheet.jpg'}, {out / 'frames.json'}")


if __name__ == "__main__":
    main()
