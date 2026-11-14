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
    print("4 ok: lost, outside the crop, untrusted depth -> no position, with a note")
    print("all ok")


if __name__ == "__main__":
    main()
