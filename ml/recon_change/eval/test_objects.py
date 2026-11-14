#!/usr/bin/env python3
"""Offline checks for objects.py: no model, no GPU, a second or two. Detections are made up.

  python eval/test_objects.py

1. Views are fused: the same thing seen in several photos is one object, two things of one name
   in one photo stay two, two names for one thing are one object, one weak box does not count.
2. An unchanged room gives no event, whatever the detector misses when it has no clear look.
3. Missing only after several clear looks from different standpoints; one look never flips it.
4. A move is one event, not a loss plus a new object; a new thing is "appeared".
5. confidence_pct rises with agreeing looks, falls with misses and with time, and an event below
   the limit is held back as "unconfirmed".
6. where_is with plurals, other words, look-alike words, and things that are not in the map.
7. The map survives json; the worker-side helpers agree with locate.py on a made-up room.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import objects  # noqa: E402

T0 = 1_800_000_000.0
SHELF, TABLE, SINK = [1.0, 2.0, 0.9], [-1.5, 0.5, 0.7], [2.5, -1.0, 0.9]


def sight(name, at, view, score=0.55, size=0.12, jitter=0.04, rng=random.Random(1), **more):
    return {"name": name, "score": score, "position": [v + rng.uniform(-jitter, jitter) for v in at], "size_m": size, "view": view,
            "box": [0.4, 0.4, 0.5, 0.5], **more}


def inventory():
    """Keys on the shelf (5 photos), a cup on the table (4), a second cup by the sink (4, and in the
    same photos as the first), the table cup called "glass" in two more photos, one weak phone box."""
    s = [sight("keys", SHELF, v, 0.6, 0.08) for v in range(5)]
    s += [sight("cup", TABLE, v, 0.5) for v in range(4)]
    s += [sight("cup", SINK, v, 0.5) for v in range(4)]
    s += [sight("glass", TABLE, v, 0.3) for v in (5, 6)]
    s += [sight("phone", [0.0, -2.0, 0.5], 3, 0.22)]
    s += [{"name": "wallet", "score": 0.9, "position": None, "size_m": None, "view": 0}]  # not placed: ignored
    return objects.fuse(s, now=T0)


def fix(k, checked=()):
    """Fix number k: each from another standpoint."""
    return {"state": "found", "position": [0.6 * k, -3.0, 0.0], "heading_deg": 10 * k, "checked": list(checked)}


def clear(obj_id, gap=None):
    return {"id": obj_id, "in_view": True, "blocked": False, "close_enough": True, "pixel": [0.5, 0.5], "depth_gap_m": gap, "distance_m": 2.0}


def by_label(room_map, label, states=("present", "moved", "missing")):
    return [o for o in room_map["objects"] if o["label"] == label and o["state"] in states]


def run(room_map, steps):
    """steps: [(detections, checked)] -> (map, every event reported on the way)."""
    told = []
    for k, (detections, checked) in enumerate(steps):
        room_map, new = objects.update(room_map, detections, fix(k, checked), now=T0 + 60 + 5 * k)
        told += new
    return room_map, told


def test_fusion():
    m = inventory()
    cups, keys = by_label(m, "cup"), by_label(m, "keys")
    assert len(keys) == 1 and keys[0]["views"] == 5, keys
    assert len(cups) == 2, [(o["label"], o["views"], o["position"]) for o in m["objects"]]
    table_cup = min(cups, key=lambda o: abs(o["position"][0] - TABLE[0]))
    assert table_cup["views"] == 6 and "glass" in table_cup["names"], table_cup  # two names, one thing
    assert not by_label(m, "glass") and not by_label(m, "phone")
    assert [o["state"] for o in m["objects"] if o["label"] == "phone"] == ["unconfirmed"]
    assert keys[0]["confidence_pct"] > by_label(m, "cup")[1]["confidence_pct"] >= objects.SHOW_PCT
    # two boxes of one name in ONE photo are two things, even close together
    pair = objects.fuse([sight("cup", [0, 0, 0], v, 0.5, jitter=0) for v in range(3)] + [sight("cup", [0.2, 0, 0], v, 0.5, jitter=0) for v in range(3)], now=T0)
    assert len(by_label(pair, "cup")) == 2
    # the same two places seen in different photos cannot be told apart at 20 cm: one object
    one = objects.fuse([sight("cup", [0, 0, 0], v, 0.5, jitter=0) for v in range(3)] + [sight("cup", [0.2, 0, 0], v + 3, 0.5, jitter=0) for v in range(3)], now=T0)
    assert len(by_label(one, "cup")) == 1
    print("ok fusion:", [(o["label"], o["views"], o["confidence_pct"], o["state"]) for o in m["objects"]])


def test_unchanged():
    m = inventory()
    keys, cup = by_label(m, "keys")[0], by_label(m, "cup")[0]
    before = {o["id"]: o["state"] for o in m["objects"]}
    steps = []
    for k in range(8):
        seen = [sight("keys", SHELF, 0, 0.4, 0.08, jitter=0.1)] if k % 2 == 0 else []  # found in every second look
        seen += [sight("cup", TABLE, 0, 0.3, jitter=0.1)] if k % 3 == 0 else []
        looked = [{**clear(keys["id"]), "close_enough": False}, {**clear(cup["id"]), "blocked": True}]  # never a clear look when missed
        steps.append((seen, looked))
    m2, told = run(m, steps)
    assert not told and not objects.diary(m2, unconfirmed=True), objects.diary(m2, unconfirmed=True)
    assert {o["id"]: o["state"] for o in m2["objects"]} == before
    # a lost fix changes nothing at all
    m3, told = objects.update(m, [sight("book", [5, 5, 1], 0, 0.9)], {"state": "lost"}, now=T0 + 99)
    assert not told and len(m3["objects"]) == len(m["objects"])
    print("ok unchanged room: no event in 8 fixes")


def test_missing():
    m = inventory()
    keys = by_label(m, "keys")[0]
    start = keys["confidence_pct"]
    # the same standpoint five times: one look, counted once
    same = m
    for k in range(5):
        same, told = objects.update(same, [], {**fix(0), "checked": [clear(keys["id"], 0.3)]}, now=T0 + 60 + k)
        assert not told
    again = by_label(same, "keys")[0]
    assert again["state"] == "present" and again["missed"] == 1 and objects.GONE_PCT < again["confidence_pct"] < start, again
    # three standpoints
    states, pcts, told = [], [], []
    for k in range(3):
        m, new = objects.update(m, [], {**fix(k), "checked": [clear(keys["id"], 0.3)]}, now=T0 + 60 + 5 * k)
        obj = next(o for o in m["objects"] if o["id"] == keys["id"])
        states.append(obj["state"])
        pcts.append(obj["confidence_pct"])
        told += new
    assert states[0] == "present" and states[-1] == "missing", (states, pcts)
    assert [e["kind"] for e in told] == ["object_gone"] and told[0]["confidence_pct"] >= objects.REPORT_PCT, told
    assert "keys were no longer" in told[0]["text"] and "you" not in told[0]["text"].lower()
    reply = objects.where_is(m, "where are my keys")
    assert reply["state"] == "missing" and not reply["found"] and "not there" in reply["answer"]
    # seen again at the old place: one look is not enough, three standpoints are ("back" is reported)
    for k in (5, 6, 7):
        m, new = objects.update(m, [sight("keys", SHELF, 0, 0.5, 0.08)], fix(k), now=T0 + 200 + k)
        told += new
    assert by_label(m, "keys")[0]["state"] == "present" and told[-1]["kind"] == "object_appeared" and told[-1]["returned"], told[-1]
    # with the depth still showing a surface at the place, three looks are not enough
    calm = inventory()
    for k in range(3):
        calm, new = objects.update(calm, [], {**fix(k), "checked": [clear(keys["id"], 0.0)]}, now=T0 + 60 + 5 * k)
    assert by_label(calm, "keys")[0]["state"] == "present"
    print("ok missing after several looks:", states, pcts)


def test_move_and_new():
    m = inventory()
    keys = by_label(m, "keys")[0]
    new_place = [-1.2, 0.9, 0.75]
    empty = {"agree": 0, "free": 4, "seen": 6}
    steps = [([sight("keys", new_place, 0, 0.5, 0.08, baseline=empty)], [clear(keys["id"], 0.2)]) for _ in range(4)]
    m2, told = run(m, steps)
    kinds = [e["kind"] for e in objects.diary(m2)]
    assert kinds == ["object_moved"], (kinds, [(e["kind"], e["status"], e["confidence_pct"]) for e in m2["events"]])
    event = objects.diary(m2)[0]
    assert objects.math.dist(event["from"], SHELF) < 0.15 and objects.math.dist(event["to"], new_place) < 0.15, event
    moved = by_label(m2, "keys")
    assert len(moved) == 1 and moved[0]["id"] == keys["id"] and moved[0]["state"] == "moved"
    places = [{"name": "shelf", "position": SHELF}, {"name": "table", "position": TABLE}]
    text = objects.describe(event, places, clock=lambda t: "11:05")
    assert text == "The keys were moved from near the shelf to near the table at 11:05.", text
    assert objects.math.dist(objects.where_is(m2, "What did I do with my keys?")["position"], new_place) < 0.15
    # a thing of a name the room did not have, where the baseline saw empty space: appeared
    steps = [([sight("book", [0.0, 1.0, 0.8], 0, 0.5, 0.25, baseline=empty)], []) for _ in range(3)]
    m3, told = run(m, steps)
    assert [e["kind"] for e in objects.diary(m3)] == ["object_appeared"] and len(by_label(m3, "book")) == 1, m3["events"]
    # the same sightings from one standpoint only: nothing
    m4 = m
    for k in range(4):
        m4, told = objects.update(m4, [sight("book", [0.0, 1.0, 0.8], 0, 0.5, 0.25, baseline=empty)], fix(0), now=T0 + 60 + k)
    assert not objects.diary(m4, unconfirmed=True) and not by_label(m4, "book")
    # a new thing where the baseline already showed a surface (the inventory may just have missed it): held back, not reported
    there = {"agree": 5, "free": 0, "seen": 6}
    m5, told = run(m, [([sight("book", [0.0, 1.0, 0.8], 0, 0.5, 0.25, baseline=there)], []) for _ in range(3)])
    waiting = objects.diary(m5, unconfirmed=True)
    assert not told and [(e["kind"], e["status"]) for e in waiting] == [("object_appeared", "unconfirmed")], waiting
    print("ok move is one event:", event["text"], event["confidence_pct"], "| held back:", waiting[0]["confidence_pct"])


def test_confidence():
    # rises as more photos agree
    pcts = [objects.fuse([sight("cup", TABLE, v, 0.55) for v in range(n)], now=T0)["objects"][0]["confidence_pct"] for n in (1, 2, 3, 5)]
    assert pcts == sorted(pcts) and pcts[0] < objects.SHOW_PCT <= pcts[2], pcts
    # but agreement cannot make a weak name sure: twenty photos of a 0.3 box stay below the line
    weak = objects.fuse([sight("cup", TABLE, v, 0.3) for v in range(20)], now=T0)["objects"][0]
    assert weak["state"] == "unconfirmed" and weak["views"] == 20 and weak["confidence_pct"] < 50, weak
    m = inventory()
    cup = by_label(m, "cup")[0]
    # falls with time, and is never below "do not know" for that reason alone
    later = [objects.confidence_pct(cup, T0 + h * 3600) for h in (0, 12, 24, 72, 720)]
    assert later == sorted(later, reverse=True) and later[0] > later[2] and later[-1] >= 50, later
    # one clear look without it lowers it but does not flip it; an agreeing look brings it back
    m1, _ = objects.update(m, [], {**fix(0), "checked": [clear(cup["id"], 0.3)]}, now=T0 + 10)
    one = next(o for o in m1["objects"] if o["id"] == cup["id"])
    assert one["state"] == "present" and objects.GONE_PCT < one["confidence_pct"] < cup["confidence_pct"]
    m2, _ = objects.update(m1, [sight("cup", cup["position"], 0, 0.45, jitter=0)], fix(1), now=T0 + 20)
    two = next(o for o in m2["objects"] if o["id"] == cup["id"])
    assert two["confidence_pct"] > one["confidence_pct"] and two["missed"] == 0
    # a "gone" about an object the map was not sure of is held back
    weak = objects.fuse([sight("vase", SINK, v, 0.47) for v in range(3)], now=T0)
    vase = weak["objects"][0]
    assert vase["state"] == "present" and vase["confidence_pct"] < 75, vase
    told = []
    for k in range(4):
        weak, new = objects.update(weak, [], {**fix(k), "checked": [clear(vase["id"], 0.3)]}, now=T0 + 10 * k)
        told += new
    held = objects.diary(weak, unconfirmed=True)
    assert not told and [(e["kind"], e["status"]) for e in held] == [("object_gone", "unconfirmed")] and held[0]["confidence_pct"] < objects.REPORT_PCT, held
    assert all("confidence_pct" in o for o in weak["objects"]) and all("confidence_pct" in e for e in weak["events"])
    print("ok confidence:", pcts, later, "one miss:", one["confidence_pct"], "held-back gone:", held[0]["confidence_pct"])


def test_where_is():
    m = inventory()
    spot = by_label(m, "keys")[0]["position"]
    for question in ("Where are my keys?", "where is the key", "Have you seen my KEYS anywhere"):
        reply = objects.where_is(m, question)
        assert reply["found"] and reply["label"] == "keys" and reply["position"] == spot and reply["confidence_pct"] >= objects.SHOW_PCT, reply
    for question in ("where is my mug", "where are the cups?", "Where did I leave my coffee cup"):
        assert objects.where_is(m, question)["label"] == "cup", objects.where_is(m, question)
    # the table cup was also called "glass": asking for the glass finds it; glasses are another thing
    assert objects.where_is(m, "where is my glass")["object"]["label"] == "cup"
    specs = objects.where_is(m, "where are my glasses")
    assert specs["known"] and specs["label"] == "glasses" and not specs["found"] and specs["object"] is None, specs
    assert objects.where_is(m, "where are my specs")["label"] == "glasses"
    assert objects.where_is(m, "where is the tv remote")["label"] == "remote control"
    assert objects.where_is(m, "where is the telly")["label"] == "television"
    assert objects.where_is(m, "where is my pill box")["label"] == "pill box"
    assert objects.where_is(m, "where are my phones")["label"] == "phone" and not objects.where_is(m, "where is my phone")["found"]  # only an unconfirmed box
    unknown = objects.where_is(m, "where is my unicorn")
    assert not unknown["known"] and not unknown["found"]
    near = objects.where_is(m, "keys?", fix={"state": "found", "position": [1.0, 0.0, 0.0], "heading_deg": 0},
                            places=[{"name": "shelf", "position": SHELF}], clock=lambda t: "11:05")
    assert near["near"] == "shelf" and abs(near["distance_m"] - 2.0) < 0.15 and abs(near["bearing_deg"]) <= 5, near
    assert near["answer"].startswith("Your keys were last seen near the shelf at 11:05, about 2.0 metres ahead"), near["answer"]
    print("ok where_is:", near["answer"])


def test_json_and_worker_side():
    import numpy as np

    import items
    import locate
    import recon_change as rc
    from test_items import look_at  # the made-up room of test_items.py

    m = inventory()
    again = json.loads(json.dumps(m))
    assert again == m
    slim = objects.for_worker(m)
    assert len(json.dumps(slim)) < len(json.dumps(m)) and {o["id"] for o in slim["objects"]} == {o["id"] for o in m["objects"]}
    m2, told = run(again, [([sight("keys", SHELF, 0, 0.5, 0.08)], [])])
    json.dumps(m2), json.dumps(told), json.dumps(objects.events(m, m2)), json.dumps(objects.labels(m2))

    # five cameras in a row looking at a wall 2 units away, with a box standing 0.4 in front of it
    S, H, W = 5, 140, 182
    K = np.array([[150.0, 0, W / 2], [0, 150.0, H / 2], [0, 0, 1]], np.float32)
    E = np.stack([look_at(np.array([0.3 * i - 0.6, 0.0, 0.0]), np.array([0.0, 0.0, 2.0])) for i in range(S)])
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    depth = np.zeros((S, H, W), np.float32)
    for i in range(S):
        ray = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u, float)], -1) @ E[i][:, :3]
        eye = -E[i][:, :3].T @ E[i][:, 3]
        wall = (2.0 - eye[2]) / ray[..., 2]
        front = (1.6 - eye[2]) / ray[..., 2]
        hit = eye + ray * front[..., None]
        on_box = (np.abs(hit[..., 0]) < 0.25) & (np.abs(hit[..., 1]) < 0.25) & (i < 4)  # the last camera sees the room without the box
        depth[i] = np.where(on_box, front, wall)
    r = rc.Recon(depth=depth, conf=np.ones_like(depth), K=np.stack([K] * S), E=E, rgb=np.zeros((S, H, W, 3), np.uint8))
    valid = np.ones(depth.shape, bool)
    base = [0, 1, 2, 3]
    room = locate.map_frame(r, base, valid)
    window = (0.0, 0.0, 1.0, 1.0)
    box = [0.5 - 0.1, 0.5 - 0.13, 0.5 + 0.1, 0.5 + 0.13]  # the box as camera 2 sees it (straight ahead)
    found = [[{"name": "box", "score": 0.6, "box": box}] if i == 2 else [] for i in base]
    for i in (1, 3):  # where the box centre lands in the cameras next to it
        cam = E[i][:, :3] @ np.array([0.0, 0.0, 1.6]) + E[i][:, 3]
        cu, cv = (K[0, 0] * cam[0] / cam[2] + K[0, 2]) / (W - 1), (K[1, 1] * cam[1] / cam[2] + K[1, 2]) / (H - 1)
        found[i] = [{"name": "box", "score": 0.5, "box": [cu - 0.1, cv - 0.13, cu + 0.1, cv + 0.13]}]
    room_map = objects.inventory(found, r, base=base, valid=valid, now=T0, room=room, frames=base, picture_shape=(H, W, 3))
    things = by_label(room_map, "box")
    assert len(things) == 1 and things[0]["views"] == 3, room_map["objects"]
    world = locate.from_map(room, things[0]["position"])[0]
    assert np.linalg.norm(world - [0.0, 0.0, 1.6]) < 0.05, world
    assert 0.3 < things[0]["size_m"] / room["metres"] < 0.6, things[0]["size_m"] / room["metres"]  # the box drawn is about 0.4 units
    # camera 4 does not see the box: the place is in view, not blocked, and the depth there is farther
    seen = objects.observe([], room_map, r, base, 4, valid, window, "found", (H, W, 3), room=room)
    look = seen["objects_checked"][0]
    assert look["in_view"] and not look["blocked"] and look["close_enough"] and look["depth_gap_m"] > 0.2, look
    # camera 3 sees it: a box there is placed at the object, and the baseline frames agree a surface is there
    seen = objects.observe(found[3], room_map, r, base, 3, valid, window, "found", (H, W, 3), room=room)
    assert np.linalg.norm(np.array(seen["objects_seen"][0]["position"]) - things[0]["position"]) < 0.1, seen
    assert seen["objects_seen"][0]["baseline"]["agree"] >= 2, seen["objects_seen"][0]
    # the same detection placed where the baseline frames saw empty space (its depth made nearer): free views, none agreeing
    r2 = rc.Recon(depth=depth.copy(), conf=r.conf, K=r.K, E=r.E, rgb=r.rgb)
    r2.depth[4] = 1.0
    hover = objects.lift([{"name": "box", "score": 0.6, "box": [0.4, 0.37, 0.6, 0.63]}], r2, 4, valid, window, room, base=base)
    assert hover[0]["baseline"]["free"] >= 2 and hover[0]["baseline"]["agree"] == 0, hover[0]
    balls = objects.marks(room_map, room)
    assert len(balls) == 1 and np.allclose(balls[0][0], world) and balls[0][2] == objects.COLOURS["present"]
    print("ok json and worker side:", things[0]["position"], look)


if __name__ == "__main__":
    test_fusion()
    test_unchanged()
    test_missing()
    test_move_and_new()
    test_confidence()
    test_where_is()
    test_json_and_worker_side()
    print("all ok")
