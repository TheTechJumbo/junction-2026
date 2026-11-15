#!/usr/bin/env python3
"""People are not part of the room.

A person standing in view, or the wearer's own hand and arm, must not be taken for something that
appeared, for a change at a watched spot, for an item, or for furniture in the 3D view, and must
not end up in a baseline. This finds them with the detector that is already loaded for the items
(items.py, OWLv2): "a person", "a hand", "an arm" are three more phrases in a pass that is run
anyway, so in a position fix it costs next to nothing; a change check needs one pass per frame.

What comes out, per picture:
  people   [{"box": [u0, v0, u1, v1], "score", "kind": "person" | "hand" | "arm"}], best first
  mask     which pixels to leave out. A box is a coarse mask: a standing person fills about half of
           their box. The detector also scores every 16-pixel patch of its input for each phrase,
           and the patches that say "person" trace the body much more closely, at no extra cost.
           The mask is those patches inside the boxes, grown a little; where the patches say
           nothing, the box itself.

How it is used (live/worker.py):
  position fix   people and people_in_view in the result; an item box inside a person box is not
                 a sighting; a watched spot whose best look is covered by a person is "not_seen".
  change check   the masks blank the depth of those pixels (depth 0 means "no depth here" to
                 recon_change, watch and splat alike), so a person neither appears nor makes
                 things behind them "gone", and is not drawn into the 3D view.
  baseline       the frames that show a person are reported, and their boxes kept next to the
                 pictures so that a later check need not look again.

RECON_PEOPLE=0 turns all of it off. Limits are from eval/PEOPLE_RESULTS.md.
"""
import json
import os
from pathlib import Path

import cv2
import numpy as np

ON = os.environ.get("RECON_PEOPLE", "1") != "0"
# A box counts as a person from this score on; hands and arms have their own, lower, limit: they
# are what the wearer's own body looks like from the glasses, and they score lower than whole people.
SCORE = float(os.environ.get("RECON_PEOPLE_SCORE", "0.3"))
SCORE_PART = float(os.environ.get("RECON_PEOPLE_PART_SCORE", "0.3"))
PATCH = float(os.environ.get("RECON_PEOPLE_PATCH", "0.15"))  # a patch says "person" from this score on
KINDS = {"person": ["a person"], "hand": ["a hand"], "arm": ["an arm"]}
GROW = 0.02  # masks are grown by this share of the picture's long side: outlines are never exact
INSIDE = 0.8  # an item box with this share of its area in a person box is on the person
COVERS = 0.25  # a watched spot with this share of its box under a person is covered
SAVED = "people.json"  # next to a zone's baseline pictures: the people found in them


def phrases():
    return [p for group in KINDS.values() for p in group]


def attach(detector):
    """Ask the item detector for people in every pass it makes anyway."""
    detector.also = phrases() if ON else []
    return bool(detector.also)


def overlap(box, other):
    """Intersection area of two boxes, as a share of the first box's area."""
    w = min(box[2], other[2]) - max(box[0], other[0])
    h = min(box[3], other[3]) - max(box[1], other[1])
    area = (box[2] - box[0]) * (box[3] - box[1])
    return max(w, 0.0) * max(h, 0.0) / area if area > 0 else 0.0


def from_raw(raw, limit=None, part=None):
    """The detector's raw boxes per phrase -> the people list. A hand or an arm inside a person
    box that is already listed adds nothing and is left out."""
    limit, part = SCORE if limit is None else limit, SCORE_PART if part is None else part
    found = []
    for kind, group in KINDS.items():
        for phrase in group:
            boxes, scores = raw.get(phrase, ((), ()))
            for box, score in zip(boxes, scores):
                if score >= (limit if kind == "person" else part):
                    found.append({"box": [round(float(v), 4) for v in box], "score": round(float(score), 3), "kind": kind})
    found.sort(key=lambda p: (p["kind"] != "person", -p["score"]))
    kept = []
    for p in found:
        if not any(overlap(p["box"], k["box"]) >= INSIDE for k in kept):
            kept.append(p)
    return sorted(kept, key=lambda p: -p["score"])


def patch_mask(grid, shape, limit=None):
    """Which pixels of an (H, W) picture the detector's patches call a person, or None without a grid.
    grid: (phrases, scores (patches, phrases)) of the whole-picture pass, patches in rows over the
    square the picture was padded to."""
    if not grid:
        return None
    asked, scores = grid
    columns = [asked.index(p) for p in phrases() if p in asked]
    if not columns:
        return None
    H, W = shape[:2]
    side = int(round(np.sqrt(scores.shape[0])))
    says = (scores[:, columns].max(1) >= (PATCH if limit is None else limit)).reshape(side, side).astype(np.uint8)
    square = cv2.resize(says, (max(H, W), max(H, W)), interpolation=cv2.INTER_NEAREST)  # the picture sits top left in it
    return square[:H, :W] > 0


def mask_of(found, shape, grid=None, boxes_only=False):
    """The pixels to leave out, (H, W) bool: inside each box, the patches that say "person" (the box
    itself where they say next to nothing), grown by GROW."""
    H, W = shape[:2]
    mask = np.zeros((H, W), bool)
    patches = None if boxes_only else patch_mask(grid, shape)
    for p in found:
        u0, v0, u1, v1 = p["box"]
        x0, y0, x1, y1 = int(max(u0, 0) * W), int(max(v0, 0) * H), int(np.ceil(min(u1, 1) * W)), int(np.ceil(min(v1, 1) * H))
        if x1 <= x0 or y1 <= y0:
            continue
        inside = patches[y0:y1, x0:x1] if patches is not None else None
        if inside is not None and inside.mean() >= 0.15:
            mask[y0:y1, x0:x1] |= inside
        else:
            mask[y0:y1, x0:x1] = True
    if mask.any():
        reach = max(1, int(round(GROW * max(H, W))))
        mask = cv2.dilate(mask.astype(np.uint8), np.ones((2 * reach + 1, 2 * reach + 1), np.uint8)) > 0
    return mask


def seen(detector, shape):
    """People in the picture the detector has just searched for items (Detector.candidates):
    {"people": [...], "mask": (H, W) bool}, or None if it was not asked for people."""
    kept = getattr(detector, "seen", None)
    if not kept or not getattr(detector, "also", None):
        return None
    found = from_raw(kept["raw"])
    return {"people": found, "mask": mask_of(found, shape, kept.get("grid"))}


def look(detector, picture):
    """People in one picture, with a pass of its own over the whole picture (no tiles: people are large)."""
    raw = detector.detect(picture, phrases(), tiles=0)
    found = from_raw(raw)
    return {"people": found, "mask": mask_of(found, picture.shape, getattr(detector, "grid", None))}


def on_person(box, found):
    """Is an item's box inside a person's, a hand's or an arm's box?"""
    return any(overlap(box, p["box"]) >= INSIDE for p in found)


def into_frame(found, window):
    """Boxes given in 0..1 of the picture the detector saw -> 0..1 of the centre crop the model saw
    (window = items.crop_window). Boxes that end up outside it are dropped, the rest clipped."""
    x0, y0, x1, y1 = window
    out = []
    for p in found:
        u0, v0, u1, v1 = p["box"]
        box = [(u0 - x0) / (x1 - x0), (v0 - y0) / (y1 - y0), (u1 - x0) / (x1 - x0), (v1 - y0) / (y1 - y0)]
        box = [float(np.clip(v, 0, 1)) for v in box]
        if box[2] - box[0] > 0.005 and box[3] - box[1] > 0.005:
            out.append({**p, "box": [round(v, 4) for v in box]})
    return out


def blank(recon, masks):
    """Leave people out of a reconstruction: their pixels get depth 0, which recon_change, watch and
    splat all read as "no depth here". masks: {frame index: (h, w) bool mask, any size}.
    Returns how many frames had something blanked."""
    H, W = recon.depth.shape[1:]
    done = 0
    for q, mask in masks.items():
        if mask is None or not mask.any():
            continue
        fitted = cv2.resize(mask.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
        recon.depth[q][fitted] = 0
        done += 1
    return done


def uncover_watch(answers, people_in, covers=COVERS):
    """A watched spot that reads "changed" while a person covers it was not seen at all.
    answers: watch.check's answers; people_in(frame index) -> the people list of that frame, boxes
    in 0..1 of the frame the model saw. Changes the answers in place and returns how many."""
    changed = 0
    for answer in answers:
        if answer.get("state") != "changed" or answer.get("frame") is None or not answer.get("box_new"):
            continue
        found = people_in(int(answer["frame"])) or []
        if any(overlap(answer["box_new"], p["box"]) >= covers for p in found):
            answer.update(state="not_seen", why="a person is in the way", score_covered=answer.get("score"), score=None)
            changed += 1
    return changed


def save(zone, by_frame):
    """Keep the people found in a zone's baseline pictures next to them: {picture name: people list}."""
    Path(zone, SAVED).write_text(json.dumps(by_frame))


def saved(zone):
    """The people found in a zone's baseline pictures when it was recorded, or None if never looked."""
    try:
        listed = json.loads(Path(zone, SAVED).read_text())
        return listed if isinstance(listed, dict) else None
    except (OSError, ValueError):
        return None
