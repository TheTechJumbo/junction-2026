#!/usr/bin/env python3
"""Offline checks for items.py: no model, no GPU, a few seconds.

  python eval/test_items.py

1. An item box centred on a spot in the wearer's frame gets the same map position, distance and
   bearing as locate.py gives a named place picked at that spot.
2. The window that maps detector boxes into the model's frame is the crop worker.crop_like makes.
3. Tiles cover the picture; overlapping boxes are thinned; one thing gets one name.
4. Nothing is placed without a fix, outside the crop, or on untrusted depth: the item says why.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import items  # noqa: E402
import locate  # noqa: E402
import recon_change as rc  # noqa: E402


def look_at(eye, target):
    """World -> camera for a camera at `eye` looking at `target`, y down."""
    z = (target - eye) / np.linalg.norm(target - eye)
    x = np.cross(z, np.array([0.0, -1.0, 0.0]))
    x /= np.linalg.norm(x)
    R = np.stack([x, np.cross(z, x), z])
    return np.concatenate([R, (-R @ eye)[:, None]], 1).astype(np.float32)


def room(S=5, H=140, W=182):
    """Cameras in a row looking at a wall 2 units away; depth is the wall as each camera sees it."""
    K = np.array([[150.0, 0, W / 2], [0, 150.0, H / 2], [0, 0, 1]], np.float32)
    E = np.stack([look_at(np.array([0.3 * i - 0.6, 0.05 * i, 0.0]), np.array([0.1 * i - 0.2, 0.0, 2.0])) for i in range(S)])
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    depth = np.zeros((S, H, W), np.float32)
    for i in range(S):
        ray = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u, float)], -1) @ E[i][:, :3]  # world
        eye = -E[i][:, :3].T @ E[i][:, 3]
        depth[i] = (2.0 - eye[2]) / ray[..., 2]  # the wall is the plane z = 2
    conf = np.ones((S, H, W), np.float32) + np.random.default_rng(0).random((S, H, W)).astype(np.float32)
    return rc.Recon(depth=depth, conf=conf, K=np.stack([K] * S), E=E, rgb=np.zeros((S, H, W, 3), np.uint8))


def main():
    r = room()
    base, q = [0, 1, 2, 3], 4
    u, v = 0.3, 0.6
    result, _, valid = locate.locate(r, base, [q], [{"name": "spot", "frame": q, "u": u, "v": v}])
    place = result["waypoints"][0]

    # 1. the same spot as an item: the detector saw a wider picture, of which the model's frame is the middle
    window = (0.125, 0.0, 0.875, 1.0)
    cu, cv = window[0] + u * (window[2] - window[0]), v
    box = [cu - 0.02, cv - 0.03, cu + 0.02, cv + 0.03]
    found, marks = items.place([{"name": "keys", "score": 0.5, "box": box}], r, base, q, valid, window, result["state"])
    item = found[0]
    assert item["position"] is not None, item
    assert np.allclose(item["position"], place["position"], atol=0.02), (item, place)
    assert abs(item["distance_m"] - place["distance_m"]) <= 0.1 and abs(item["bearing_deg"] - place["bearing_deg"]) <= 1, (item, place)
    assert len(marks) == 1
    print("1 ok: item", item["position"], item["distance_m"], item["bearing_deg"], "| place", place["position"],
          place["distance_m"], place["bearing_deg"])

    # 2. the crop window is crop_like's crop
    for shape, like in [((720, 1280), (392, 518)), ((1280, 720), (392, 518)), ((960, 1280), (518, 392)), ((480, 640), (480, 640))]:
        h, w = shape
        target = like[1] / like[0]
        if w / h > target:
            keep = max(1, round(h * target))
            want = ((w - keep) // 2 / w, 0, ((w - keep) // 2 + keep) / w, 1)
        else:
            keep = max(1, round(w / target))
            want = (0, (h - keep) // 2 / h, 1, ((h - keep) // 2 + keep) / h)
        assert np.allclose(items.crop_window(shape, like), want), (shape, like)
    print("2 ok: crop window")

    # 3. tiles, thinning, one name per thing
    for h, w, rows in [(720, 1280, 1), (960, 1280, 1), (720, 1280, 2), (1280, 720, 1), (480, 640, 2)]:
        tiles = items.tile_layout(h, w, rows)
        cover = np.zeros((h, w), bool)
        for x, y, s in tiles:
            assert x >= 0 and y >= 0 and x + s <= w and y + s <= h, (h, w, rows, x, y, s)
            cover[y:y + s, x:x + s] = True
        assert cover.all(), (h, w, rows)
    assert items.tile_layout(720, 1280, 0) == [] and items.tile_layout(640, 640, 1) == []
    assert len(items.tile_layout(720, 1280, 1)) == 2
    boxes = np.array([[0.1, 0.1, 0.2, 0.2], [0.11, 0.1, 0.21, 0.2], [0.5, 0.5, 0.6, 0.6], [0.12, 0.12, 0.16, 0.16]])
    assert items.thin(boxes, [0.5, 0.6, 0.3, 0.2]) == [1, 2]
    raw = {"keys": (boxes[:2], np.array([0.5, 0.6])), "a key": (boxes[2:3], np.array([0.2])),
           "a wallet": (boxes[1:2], np.array([0.7])), "a mobile phone": (boxes[2:3], np.array([0.4]))}
    got = items.merge(raw, ["keys", "wallet", "phone"], limit=0.3)
    assert [(d["name"], d["score"]) for d in got] == [("wallet", 0.7), ("phone", 0.4)], got
    assert [d["name"] for d in items.best_per_name(got + got)] == ["wallet", "phone"]
    assert items.wanted("Keys,  remote   control ,,keys") == ["keys", "remote control"] and items.wanted("") == []
    assert items.slug("remote control") == "item_remote_control" and items.slug("keys") == "item_keys"
    print("3 ok: tiles, thinning, names")

    # 4. no invented positions
    lost, none = items.place([{"name": "keys", "score": 0.5, "box": box}], r, base, q, valid, window, "lost")
    assert lost[0]["position"] is None and lost[0]["distance_m"] is None and "note" in lost[0] and not none
    edge, _ = items.place([{"name": "keys", "score": 0.5, "box": [0.01, 0.4, 0.05, 0.5]}], r, base, q, valid, window, "found")
    assert edge[0]["position"] is None and "note" in edge[0], edge
    untrusted = valid.copy()
    untrusted[q] = False
    bad, _ = items.place([{"name": "keys", "score": 0.5, "box": box}], r, base, q, untrusted, window, "found")
    assert bad[0]["position"] is None and "note" in bad[0], bad
    H, W = r.depth[q].shape
    px, py = int(round(u * (W - 1))), int(round(v * (H - 1)))
    hole = valid.copy()
    hole[q, py - 4:py + 5, px - 4:px + 5] = False  # only the item's own depth is untrusted: the surface around it helps
    helped, _ = items.place([{"name": "keys", "score": 0.5, "box": box}], r, base, q, hole, window, "found")
    assert np.allclose(helped[0]["position"], item["position"], atol=0.02) and "surface" in helped[0]["note"], helped
    bump = rc.Recon(depth=r.depth.copy(), conf=r.conf, K=r.K, E=r.E, rgb=r.rgb)
    bump.depth[q, py - 4:py + 5, px - 4:px + 5] *= 0.6  # ... but not when the item's depth disagrees with that surface
    apart, _ = items.place([{"name": "keys", "score": 0.5, "box": box}], bump, base, q, hole, window, "found")
    assert apart[0]["position"] is None and "note" in apart[0], apart
    print("4 ok: lost, outside the crop, untrusted depth -> no position, with a note; surface around an item helps")

    # 5. looking again at a remembered spot (items.check): projection, blocked, close enough, detected near
    there = locate.map_frame(r, base, valid)
    points = np.random.default_rng(1).normal(size=(5, 3))
    assert np.allclose(locate.from_map(there, locate.to_map(there, points)), points, atol=1e-5)
    names, shape = ["keys", "remote control"], (720, 1280)
    spot = {"name": "keys", "position": item["position"]}

    def check(remembered, seen, recon=r, mask=valid):
        return items.check(remembered, names, seen, recon, q, mask, window, there, shape)

    facts, left = check([spot], found)
    fact = facts[0]
    assert not left and fact["in_view"] and not fact["blocked"] and fact["detected_near"], fact
    assert abs(fact["pixel"][0] - cu) < 0.01 and abs(fact["pixel"][1] - cv) < 0.01, (fact, cu, cv)  # back where the box was
    assert abs(fact["distance_m"] - item["distance_m"]) <= 0.1 and abs(fact["depth_gap_m"]) < 0.05, fact
    assert not fact["close_enough"] and fact["size_px"] < items.CLOSE_PX, fact  # keys from 2 m are a few pixels
    gone = check([spot], [])[0][0]
    assert gone["in_view"] and not gone["blocked"] and not gone["detected_near"], gone
    unplaced = check([spot], [{"name": "keys", "box": box, "position": None}])[0][0]
    assert unplaced["detected_near"], unplaced  # a detection without a position still counts when the spot is in its box
    elsewhere = check([spot], [{"name": "keys", "box": box, "position": [v + 1.0 for v in item["position"]]}])[0][0]
    assert not elsewhere["detected_near"], elsewhere
    other_name = check([spot], [{"name": "remote control", "box": box, "position": item["position"]}])[0][0]
    assert not other_name["detected_near"], other_name
    nearer = rc.Recon(depth=r.depth.copy(), conf=r.conf, K=r.K, E=r.E, rgb=r.rgb)
    nearer.depth[q, py - 6:py + 7, px - 6:px + 7] *= 0.6  # something stands in front of the spot
    hidden = check([spot], [], recon=nearer)[0][0]
    assert hidden["in_view"] and hidden["blocked"] and hidden["depth_gap_m"] < -0.5, hidden
    behind_it = rc.Recon(depth=r.depth.copy(), conf=r.conf, K=r.K, E=r.E, rgb=r.rgb)
    behind_it.depth[q, py - 6:py + 7, px - 6:px + 7] *= 1.5  # the surface is further away: the spot is in plain view
    assert not check([spot], [], recon=behind_it)[0][0]["blocked"]
    assert check([spot], [], mask=untrusted)[0][0]["blocked"]  # no trusted depth there: we cannot say we saw the spot
    eye, forward = locate.camera(r.E[q])
    behind = check([{"name": "keys", "position": locate.to_map(there, eye - forward)[0].tolist()}], [])[0][0]
    assert not behind["in_view"] and behind["pixel"] is None and not behind["blocked"], behind
    aside = check([{"name": "keys", "position": [item["position"][0] + 30.0, item["position"][1], item["position"][2]]}], [])[0][0]
    assert not aside["in_view"], aside
    close = check([{"name": "remote control", "position": locate.to_map(there, eye + 0.4 * forward)[0].tolist()}], [])[0][0]
    assert close["close_enough"] and close["size_px"] >= items.CLOSE_PX, close  # a remote at 40 cm is large
    facts, left = check([{"name": "umbrella", "position": [0, 0, 0]}, {"name": "keys"}, "junk", spot], [])
    assert len(facts) == 1 and len(left) == 3, (facts, left)
    assert check([], found) == ([], [])
    print("5 ok: remembered spot ->", fact)

    # 6. limits per item and second looks (Detector.candidates), with a stand-in for the model
    assert items.per_item("Keys = 0.45, glasses=0.3, junk, =1, phone=x") == {"keys": 0.45, "glasses": 0.3}
    assert items.per_item(None) == {} and items.per_item("") == {}
    assert items.same_thing([0.1, 0.1, 0.2, 0.2], [0.11, 0.1, 0.21, 0.2])  # overlap
    assert items.same_thing([0.1, 0.1, 0.3, 0.3], [0.12, 0.12, 0.16, 0.2]) is False  # over 6 times the area: too loose a box
    assert items.same_thing([0.1, 0.1, 0.2, 0.2], [0.13, 0.13, 0.19, 0.17])  # one key of the bunch
    assert not items.same_thing([0.1, 0.1, 0.2, 0.2], [0.5, 0.5, 0.6, 0.6])
    x, y, side = items.crop_around((720, 1280), [0.5, 0.5, 0.55, 0.52], 4.0, 64)
    assert side == 256 and x == 544 and y == 239, (x, y, side)
    x, y, side = items.crop_around((720, 1280), [0.0, 0.0, 0.01, 0.01], 4.0, 64)
    assert (x, y, side) == (0, 0, 64)
    assert items.crop_around((480, 640), [0.2, 0.2, 0.8, 0.8], 4.0, 64)[2] == 480  # never larger than the picture

    keys_box, glasses_box, phone_box = np.array([[0.30, 0.30, 0.36, 0.36]]), np.array([[0.60, 0.60, 0.70, 0.66]]), np.array([[0.10, 0.70, 0.18, 0.80]])
    whole = {"keys": (keys_box, np.array([0.40])), "eyeglasses": (glasses_box, np.array([0.35])), "a mobile phone": (phone_box, np.array([0.55])),
             "a remote control": (phone_box + 0.004, np.array([0.45]))}  # the phone also looks a bit like a remote

    class StandIn(items.Detector):
        def __init__(self, zoomed):
            self.score, self.limits, self.look_from, self.look_limits, self.last, self.zoomed, self.asked = 0.5, {}, 0.0, {}, {}, zoomed, []
            self.enrolled, self.enrolled_score, self.words, self.judge, self.judge_limits = {}, 0.6, {}, None, {}
            self.also, self.seen, self.grid = [], {}, None

        def detect(self, image_bgr, phrases, tiles=None, floor=items.FLOOR, batch=4):
            self.last = {"passes": 1, "seconds": 0.1, "size": [image_bgr.shape[1], image_bgr.shape[0]]}
            if image_bgr.shape[0] == 720:  # the whole picture
                return {p: whole.get(p, (np.zeros((0, 4)), np.zeros(0))) for p in phrases}
            self.asked.append(list(phrases))  # a crop: the thing sits in its middle, with the zoomed score
            return {p: (np.array([[0.4, 0.4, 0.6, 0.6]]), np.array([self.zoomed])) for p in phrases}

    picture = np.zeros((720, 1280, 3), np.uint8)
    names = ["keys", "glasses", "phone", "remote control"]
    plain = StandIn(0.9)
    got = plain.candidates(picture, names, floor=0.3)
    assert [(d["name"], d["accepted"]) for d in got] == [("phone", True), ("keys", False), ("glasses", False)], got  # the remote box lost to the phone
    assert [d["name"] for d in plain.find(picture, names)] == ["phone"] and not plain.asked and "second_looks" not in plain.last
    assert [d["name"] for d in plain.find(picture, names, score=0.3)] == ["phone", "keys", "glasses"]  # one plain limit, as before
    limits = StandIn(0.9)
    limits.limits = {"glasses": 0.3}
    assert [d["name"] for d in limits.find(picture, names)] == ["phone", "glasses"]
    looks = StandIn(0.6)
    looks.look_from, looks.look_limits = 0.25, {"keys": 0.5, "remote control": 0.5}
    got = looks.candidates(picture, names, floor=0.3)
    assert [(d["name"], d["accepted"]) for d in got] == [("phone", True), ("keys", True), ("glasses", False)], got
    assert got[0]["score"] == 0.55 and got[1]["score"] == 0.4 and got[1]["second_look"] == 0.6, got  # the raw score stays
    assert looks.last["second_looks"] == 1 and looks.last["passes"] == 2, looks.last
    assert looks.asked == [["keys", "a key"]], looks.asked  # the box on the phone gets no second look as a remote
    weak = StandIn(0.4)  # the second look does not agree: nothing more is accepted
    weak.look_from, weak.look_limits = 0.25, {"keys": 0.5}
    assert [d["name"] for d in weak.find(picture, names)] == ["phone"]
    class Opinion:  # a stand-in for the judge: it believes in keys and not in glasses
        model_id = "stand-in"

        def share(self, picture, box, name):
            return {"keys": 0.8, "glasses": 0.05}.get(name, 0.0)

    judged = StandIn(0.2)  # the zoomed detector would say no (0.2), but only the judge is asked
    judged.look_from, judged.judge, judged.judge_limits = 0.25, Opinion(), {"keys": 0.7, "glasses": 0.1}
    got = judged.candidates(picture, names, floor=0.3)
    assert [(d["name"], d["accepted"]) for d in got] == [("phone", True), ("keys", True), ("glasses", False)], got
    assert not judged.asked and judged.last["passes"] == 1 and judged.last["second_looks"] == 2, judged.last
    judged.look_limits = {"keys": 0.5}  # with a look limit as well, both must agree: the zoomed 0.2 is not enough
    assert [d["name"] for d in judged.find(picture, names)] == ["phone"]
    by_name = {d["name"]: d for d in got}
    assert by_name["keys"]["confidence_pct"] == 80 and by_name["glasses"]["confidence_pct"] == 65 and by_name["phone"]["confidence_pct"] == 99, got
    assert items.confidence_pct({"name": "keys", "score": 0.3}) == 55 and items.confidence_pct({"name": "umbrella", "score": 0.7}) == 97
    assert items.confidence_pct({"name": "keys", "score": 0.9, "matched": "enrolled", "only_enrolled": True}) == items.CONFIDENCE_ENROLLED
    shown = StandIn(0.9)  # show-it-once: a picture question that matches the keys box, and one that matches nothing the words found
    shown.enrolled, shown.enrolled_score = {"keys": ["@keys#0"], "wallet": ["@wallet#0"]}, 0.75
    whole["@keys#0"] = (keys_box + 0.002, np.array([0.8]))
    whole["@wallet#0"] = (np.array([[0.80, 0.20, 0.86, 0.26]]), np.array([0.78]))
    got = shown.candidates(picture, names + ["wallet"], floor=0.3)
    by_name = {d["name"]: d for d in got}
    assert by_name["keys"]["matched"] == "enrolled" and by_name["keys"]["score"] == 0.4 and by_name["keys"]["accepted"], by_name["keys"]
    assert by_name["keys"]["confidence_pct"] == 72 and by_name["phone"]["matched"] == "text", got  # the words' score and confidence stay
    assert by_name["wallet"]["matched"] == "enrolled" and by_name["wallet"]["accepted"] and by_name["wallet"]["confidence_pct"] == items.CONFIDENCE_ENROLLED
    assert [d["name"] for d in items.best_per_name([d for d in got if d["accepted"]])] == ["phone", "keys", "wallet"]
    del whole["@keys#0"], whole["@wallet#0"]
    placed, _ = items.place([{"name": "keys", "score": 0.4, "box": box}], r, base, q, valid, window, "found")
    assert placed[0]["confidence_pct"] == 72 and placed[0]["matched"] == "text", placed
    print("6 ok: limits per item, second looks, a second opinion, stated confidence, and the same answers as before when none is set")
    print("all ok")


if __name__ == "__main__":
    main()
