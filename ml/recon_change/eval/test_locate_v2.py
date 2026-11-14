#!/usr/bin/env python3
"""Check version 2 of locate.py without a GPU, on a synthetic room with exact depth.

A floor and a back wall, in true metres, world z up. Cameras stand on one side and look down at
the floor, so the average of their own "up" is tilted, as in the walking test.

  up              from the floor it is the true vertical; from the cameras it is tilted; a wall
                  alone or a steep slope falls back to the cameras
  known distance  two places with a known distance between them set the metres; a missing place
                  or an absurd number falls back to the assumed viewing distance, with a note
  state           found needs two baseline frames; a view that one baseline frame confirms is
                  "unsure" instead of "lost", and "lost" again with rescue switched off
  hint, coverage  which way to turn, which directions a baseline covers
  map frame       map_frame() gives items.py the frame locate() used; old result keys are kept

    python eval/test_locate_v2.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import locate  # noqa: E402
from recon_change import Recon  # noqa: E402

H, W, FOCAL, PATCH, DIM = 154, 210, 180.0, 14, 64
K = np.array([[FOCAL, 0, W / 2], [0, FOCAL, H / 2], [0, 0, 1]], np.float32)
Z = np.array([0.0, 0.0, 1.0])
OLD_KEYS = {"state", "confidence", "position", "heading_deg", "waypoints", "track", "map", "match_per_frame", "looks_alike",
            "match_by_depth", "metres_per_unit"}


def camera(pos, target):
    """World -> camera (OpenCV: x right, y down, z forward) for a camera at pos looking at target."""
    fwd = np.asarray(target, float) - pos
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, Z)
    right /= np.linalg.norm(right)
    R = np.stack([right, np.cross(fwd, right), fwd])
    return np.concatenate([R, (-R @ pos)[:, None]], 1).astype(np.float32)


def render(E):
    """Exact z-depth of a floor (z = 0) and a back wall (y = 3)."""
    R, t = E[:, :3].astype(float), E[:, 3].astype(float)
    origin = -R.T @ t
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    rays = np.stack([(u - K[0, 2]) / FOCAL, (v - K[1, 2]) / FOCAL, np.ones_like(u, float)], -1) @ R
    with np.errstate(divide="ignore", invalid="ignore"):
        floor = np.where(rays[..., 2] < 0, -origin[2] / rays[..., 2], np.inf)
        wall = np.where(rays[..., 1] > 0, (3.0 - origin[1]) / rays[..., 1], np.inf)
    return np.minimum(floor, wall).astype(np.float32)


def looks(depth, E, weights, phase):
    """Patch features (h, w, DIM): a smooth function of where each patch's surface is in the world."""
    v, u = np.mgrid[0:H // PATCH, 0:W // PATCH] * PATCH + PATCH // 2
    z = depth[v, u]
    cam = np.stack([(u - K[0, 2]) / FOCAL * z, (v - K[1, 2]) / FOCAL * z, z], -1)
    world = (cam - E[:, 3]) @ E[:, :3]
    return np.cos(world @ weights.T + phase)


def recon(E, rng, room):
    depth = np.stack([render(e) for e in E])
    conf = np.random.default_rng(1).uniform(1, 2, depth.shape).astype(np.float32)  # the same every time
    feat = np.stack([looks(depth[i], E[i], *room) for i in range(len(E))]).astype(np.float16)
    return Recon(depth, conf, np.stack([K] * len(E)), E, None, feat)


def pixel(E, world):
    """Where a world point shows in a camera, as u and v from 0 to 1."""
    cam = E[:, :3].astype(float) @ world + E[:, 3]
    return float((FOCAL * cam[0] / cam[2] + K[0, 2]) / (W - 1)), float((FOCAL * cam[1] / cam[2] + K[1, 2]) / (H - 1))


def main():
    rng = np.random.default_rng(0)
    room = rng.normal(0, 2.0, (DIM, 3)), rng.uniform(0, 2 * np.pi, DIM)
    failed = []

    def check(ok, what):
        print(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            failed.append(what)

    # ------------------------------------------------------------------ up
    target = np.array([0.0, 2.2, 0.0])  # on the floor: the cameras look down about 23 degrees
    E = np.stack([camera(np.array([x, -1.0 + 0.1 * (i % 2), 1.4]), target) for i, x in enumerate(np.linspace(-1.2, 1.2, 6))]
                 + [camera(np.array([0.3, -0.8, 1.4]), target)])
    base, new = list(range(6)), [6]
    r = recon(E, rng, room)
    v2, _, valid = locate.locate(r, base, new)
    frame = r.room
    cams, _, _ = locate.locate(recon(E, rng, room), base, new, up="cameras")
    cam_up = locate.axes(E, base)[0]
    print(f"up from the surface: {locate.angle(frame['up'], Z):.2f} deg off the true vertical ({v2['up_from']}, the surface holds "
          f"{v2['up_surface_share']:.2f} of the points); the cameras' average is {locate.angle(cam_up, Z):.1f} deg off")
    check(v2["up_from"] == "surface" and locate.angle(frame["up"], Z) < 1.0, "up from the floor is the true vertical within 1 degree")
    check(locate.angle(cam_up, Z) > 10 and abs(v2["up_tilt_deg"] - locate.angle(cam_up, Z)) < 1.0,
          "the cameras' average is more than 10 degrees off, and up_tilt_deg reports that difference")
    check(cams["up_from"] == "cameras" and cams["up_tilt_deg"] == 0.0, 'up="cameras" keeps the old up')
    check(abs(frame["north"] @ frame["up"]) < 1e-6 and abs(frame["east"] @ frame["up"]) < 1e-6 and abs(frame["east"] @ frame["north"]) < 1e-6,
          "east, north and up are at right angles")
    wall = np.stack([rng.uniform(-2, 2, 4000), np.full(4000, 3.0), rng.uniform(0, 2.5, 4000)], 1)
    up, how = locate.surface_up(wall, cam_up, 0.03)
    check(how["from"] == "cameras" and np.allclose(up, cam_up), "a wall alone gives no up: the cameras' average is kept")
    for slope, wanted in ((20.0, "surface"), (45.0, "cameras")):
        n = np.array([0.0, -np.sin(np.radians(slope)), np.cos(np.radians(slope))])
        a, b = np.array([1.0, 0, 0]), np.cross(n, [1.0, 0, 0])
        plane = rng.uniform(-2, 2, (4000, 1)) * a + rng.uniform(-2, 2, (4000, 1)) * b + rng.normal(0, 0.005, (4000, 1)) * n
        up, how = locate.surface_up(plane, Z, 0.03)
        check(how["from"] == wanted and (wanted == "cameras" or locate.angle(up, n) < 0.5),
              f"a surface {slope:.0f} degrees off the guess: up from the {wanted}")

    # ------------------------------------------------------------------ known distance
    a_world, b_world = np.array([-0.6, 1.6, 0.0]), np.array([0.7, 2.4, 0.0])  # two spots on the floor
    places = [{"name": "Pill box", "frame": 0, **dict(zip("uv", pixel(E[0], a_world)))},
              {"name": "front door", "frame": 3, **dict(zip("uv", pixel(E[3], b_world)))}]
    true_m = float(np.linalg.norm(a_world - b_world))
    plain = locate.locate(recon(E, rng, room), base, new, places)[0]
    known = locate.locate(recon(E, rng, room), base, new, places, known={"a": "pill box", "b": "front door", "metres": true_m})[0]
    more = locate.locate(recon(E, rng, room), base, new, places, known={"a": "pill box", "b": "front door", "metres": 1.5 * true_m})[0]
    print(f"metres per unit: assumed {plain['metres_per_unit']}, known distance {known['metres_per_unit']} (the synthetic room is in true metres: 1.0)")
    check(plain["metres_from"] == "assumed viewing distance" and "metres_note" not in plain, "without a known distance the viewing distance is assumed")
    check(known["metres_from"] == "known distance" and abs(known["metres_per_unit"] - 1.0) < 0.02, "one known distance gives true metres within 2%")
    eye = locate.camera(E[6])[0]
    true_d = [float(np.hypot(*(p - eye)[:2])) for p in (a_world, b_world)]
    check(all(abs(w["distance_m"] - d) <= 0.1 for w, d in zip(known["waypoints"], true_d)), "and the distance to each place is right to 0.1 m")
    check(abs(more["metres_per_unit"] - 1.5) < 0.03 and all(abs(w["distance_m"] - 1.5 * d) <= 0.15 for w, d in zip(more["waypoints"], true_d)),
          "a known distance half as long again shows half as many metres again")
    missing = locate.locate(recon(E, rng, room), base, new, places, known={"a": "pill box", "b": "window", "metres": 2.0})[0]
    absurd = locate.locate(recon(E, rng, room), base, new, places, known={"a": "pill box", "b": "front door", "metres": 10 * true_m})[0]
    broken = locate.locate(recon(E, rng, room), base, new, places, known={"a": "pill box"})[0]
    check(all(x["metres_from"] == "assumed viewing distance" and x["metres_per_unit"] == plain["metres_per_unit"] and x.get("metres_note")
              for x in (missing, absurd, broken)), "a missing place, an absurd number or a broken file fall back to the assumed distance, with a note")
    check(abs(locate.locate(recon(E, rng, room), base, new, places, typical_m=3.0)[0]["metres_per_unit"] - 2 * plain["metres_per_unit"]) < 0.002,
          "typical_m sets the assumed viewing distance (default 1.5)")

    # ------------------------------------------------------------------ state
    check(locate.state_of(0.6, 0.0) == "found" and locate.state_of(0.35, 0.0) == "unsure" and locate.state_of(0.2, 0.4) == "lost",
          "found and unsure by the two-frame score are as before")
    check(locate.state_of(0.2, 0.7) == "unsure" and locate.state_of(0.2, 0.7, rescue=False) == "lost" and locate.state_of(0.6, 1.0) == "found",
          "one baseline frame agreeing turns lost into unsure, never into found")
    # One baseline frame looks at the left end of the room, three at the right end; the wearer looks at the left end.
    left, right = np.array([-3.0, 2.2, 0.0]), np.array([3.0, 2.2, 0.0])
    E2 = np.stack([camera(np.array([-3.0, -1.0, 1.4]), left)] + [camera(np.array([x, -1.0, 1.4]), right) for x in (2.6, 3.0, 3.4)]
                  + [camera(np.array([-2.8, -0.9, 1.4]), left)])
    one = locate.locate(recon(E2, rng, room), [0, 1, 2, 3], [4])[0]
    strict = locate.locate(recon(E2, rng, room), [0, 1, 2, 3], [4], rescue=False)[0]
    print(f"seen by one baseline frame: {one['state']}, two-frame score {one['confidence']:.2f}, one-frame score {one['match_one_frame']:.2f}; "
          f"with rescue off: {strict['state']}")
    check(one["state"] == "unsure" and one["one_frame_only"] and one["confidence"] < locate.UNSURE and one["match_one_frame"] >= locate.FOUND,
          "a view that only one baseline frame saw is unsure, flagged one_frame_only")
    check(strict["state"] == "lost" and strict["confidence"] == one["confidence"], "and lost with rescue=False, as before")
    check(v2["state"] == "found" and not v2["one_frame_only"] and v2["hint"] is None, "a view the whole baseline saw is found, with no hint")
    depth_only = recon(E2, rng, room)
    depth_only.feat = None
    check(locate.locate(depth_only, [0, 1, 2, 3], [4])[0]["match_one_frame"] is None, "without features there is no one-frame rescue")

    # ------------------------------------------------------------------ hint and coverage
    check(locate.hint(20, 18, 0) is None and locate.hint(44, -38, 3)["say"] == "Turn left" and locate.hint(44, 50, 3)["say"] == "Turn right"
          and locate.hint(40, 5, -30)["say"] == "Look down" and locate.hint(170, 170, 0)["say"] == "Turn around",
          "the hint: nothing within 30 degrees, else turn left, right or around, or look up or down")
    away = np.concatenate([E[:6], camera(np.array([0.0, -1.0, 1.4]), np.array([-6.0, -1.0, 0.6]))[None]])  # the wearer looks 90 degrees to the left
    lost = locate.locate(recon(away, rng, room), base, new)[0]
    print(f"looking away from the baseline: {lost['state']}, view gap {lost['view_gap_deg']} deg, hint {lost['hint']}")
    check(lost["state"] != "found" and lost["view_gap_deg"] > 45 and lost["hint"] and lost["hint"]["say"] == "Turn right" and lost["hint"]["turn_deg"] > 45,
          "looking 90 degrees left of the baseline: not found, and the hint says turn right")
    cover = v2["baseline_views"]
    check(len(cover["gaps"]) == 1 and cover["gaps"][0]["side"] == "behind" and 60 <= cover["covered_deg"] <= 150 and cover["pitch_deg"][1] < 0,
          f"coverage of six frames that look one way: {cover['covered_deg']} of 360 degrees, one gap behind, all looking down")
    ring = np.stack([camera(np.array([0.0, 0.0, 1.4]), np.array([np.sin(a), np.cos(a), 1.4])) for a in np.radians(np.arange(0, 360, 30))])
    around = locate.coverage(ring, list(range(12)), {"up": Z, "north": np.array([0.0, 1.0, 0.0]), "east": np.array([1.0, 0.0, 0.0])})
    check(around["covered_deg"] == 360 and not around["gaps"] and around["headings_deg"][:3] == [0, 30, 60], "twelve frames in a ring cover every direction")

    # ------------------------------------------------------------------ map frame and result keys
    check(locate.map_frame(r, base, valid) is frame and locate.map_frame(r, base, valid, 2.0) is frame,
          "map_frame gives the frame locate() used for this pass (items.py lands on the same map)")
    pts = rng.normal(0, 1, (5, 3))
    check(np.allclose(locate.from_map(frame, locate.to_map(frame, pts)), pts, atol=1e-6), "to_map and from_map still undo each other")
    fresh = locate.map_frame(recon(E, rng, room), base, valid)
    check(np.allclose(fresh["up"], frame["up"], atol=1e-3) and abs(fresh["metres"] - frame["metres"]) < 1e-6,
          "asked for a pass locate() has not seen, map_frame works the same frame out itself")
    check(OLD_KEYS <= set(v2) and set(v2["waypoints"][0] if v2["waypoints"] else known["waypoints"][0]) == {"name", "distance_m", "bearing_deg", "position"},
          "every key of the old result is still there")
    print("FAILED" if failed else "All checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
