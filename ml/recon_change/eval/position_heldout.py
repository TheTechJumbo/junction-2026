#!/usr/bin/env python3
"""Held-out position test: how far from the truth does each way of placing a photo put the wearer in the trained
splat? Runs on the GPU cluster (VGGT), next to the recording's frames. Writes one JSON with every frame's answers.

A trained splat's keyframes have known cameras (cameras.npz, in the splat's axes), and a recording frame between two
keyframes has its camera between theirs (care_space.pose_at: a few frames apart at walking speed). The test takes
every STEP-th recording frame as the photo stream the app would send (about 3 a second), and scores the frames that
are neither a trained keyframe nor a room picture (none within GAP frames): those were never seen by any method.
--hard also leaves out every keyframe and room picture within HARD frames of the photo, as a stand-in for a walk
that does not retrace the recording.

Ways of placing a photo:
  today    the worker's full fix (worker.where, as deployed: the room pictures most like the photo + the photo,
           VGGT, locate.py), whose position is in that pass's own map axes, as the relay shows it
  quick    the relay's instant fix: small grey pictures against the room map's pictures; the matched picture's
           room-map camera, its heading turned by the slide
  kfquick  the same against the trained keyframes; the matched keyframe's own camera (splat axes)
  pnp      splat_locate.SplatIndex: SIFT + PnP against the keyframes (CPU)
  vggt     VGGT with the KF_VGGT trained keyframes most like the photo + the photo, aligned on those keyframes'
           known cameras (rotation, the depth ratio for scale, the centres for the shift)

Truth in map axes is the truth in splat axes through the /care/space matrix, the same matrix the caretaker's page
draws the splat with. So an answer in splat axes (kfquick, pnp, vggt) is scored exactly where the page would show
it; an answer in room-map axes (today, quick) also carries the room-map-to-splat fit, reported on its own as "fit".

    python eval/position_heldout.py --root R --frames F --trained T --room room.json --zone-data D --out out.json
"""
import argparse
import io
import json
import math
import os
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


def wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def quick_print(frame, shape=None, width=64):
    """relay.quick_print (copied: the relay is not on the cluster)."""
    if shape is not None:
        h, w = frame.shape[:2]
        want = shape[1] / shape[0]
        if w / h > want:
            keep = max(1, round(h * want))
            frame = frame[:, (w - keep) // 2:(w - keep) // 2 + keep]
        else:
            keep = max(1, round(w / want))
            frame = frame[(h - keep) // 2:(h - keep) // 2 + keep]
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    size = (width, max(8, round(width * grey.shape[0] / grey.shape[1])))
    small = cv2.GaussianBlur(cv2.resize(grey, size, interpolation=cv2.INTER_AREA), (3, 3), 0).astype(np.float32)
    return (small - small.mean()) / (small.std() + 1e-3)


def quick_match(now, keys, shift=0.25):
    """relay.quick_match (copied)."""
    out = []
    width = now.shape[1]
    reach = int(shift * width)
    for frame, key in keys:
        if key.shape != now.shape:
            continue
        scores = []
        for s in range(-reach, reach + 1):
            a = now[:, max(0, s):width + min(0, s)]
            b = key[:, max(0, -s):width - max(0, s)]
            a, b = a - a.mean(), b - b.mean()
            scores.append(float((a * b).sum() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6)))
        i = int(np.argmax(scores))
        slide = float(i - reach)
        if 0 < i < len(scores) - 1:
            down = scores[i - 1] - 2 * scores[i] + scores[i + 1]
            if down < 0:
                slide += 0.5 * (scores[i - 1] - scores[i + 1]) / down
        out.append((scores[i], frame, slide / width))
    return sorted(out, key=lambda o: -o[0])


def photo_like_app(frame, side=896):
    """The app sends a JPEG with its long side at most 896 px."""
    scale = side / max(frame.shape[:2])
    small = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA) \
        if scale < 1 else frame
    return cv2.imdecode(cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 88])[1], cv2.IMREAD_COLOR)


def one_photo_zip(frame, side=518):
    """relay.one_photo: the photo at the model's size as a one-frame bundle."""
    scale = side / max(frame.shape[:2])
    small = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
        z.writestr("000.jpg", cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    return bundle.getvalue()


def heading_of(direction):
    return math.degrees(math.atan2(direction[0], direction[1]))


def chordal_mean(rotations):
    U, _, Vt = np.linalg.svd(np.sum(rotations, axis=0))
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    return R


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="the deployed code (junction-recon), for worker.py and recon_change")
    ap.add_argument("--frames", required=True)
    ap.add_argument("--trained", required=True, help="the trained splat's folder (cameras.npz, views.json, depths.bin, keyframes/)")
    ap.add_argument("--room", required=True, help="the relay's room record for the zone + the /care/space matrix (JSON)")
    ap.add_argument("--zone-data", required=True, help="a BEACON_ROOT with baselines/<zone> (a copy: nothing is written to the live one)")
    ap.add_argument("--zone", default="kitchen")
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", type=int, default=10, help="every STEP-th frame is a photo of the stream (30 fps: 10 = 3 a second)")
    ap.add_argument("--gap", type=int, default=4, help="a scored frame is at least this many frames from any keyframe or room picture")
    ap.add_argument("--hard", type=int, default=0, help="also run with keyframes/pictures within this many frames left out")
    ap.add_argument("--full-every", type=int, default=12, help="stream photos between two full fixes (FULL_EVERY_S = 4 s)")
    ap.add_argument("--methods", default="today,quick,kfquick,pnp,vggt")
    ap.add_argument("--kf-vggt", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    methods = set(args.methods.split(","))

    os.environ.setdefault("RECON_ITEMS", "")
    os.environ.update({"BEACON_ROOT": str(Path(args.zone_data).resolve()), "RECON_PICK": "similar", "RECON_PATCH_SIM": "0.6",
                       "RECON_NEW_VIEW": "0", "RECON_MAP_FRAMES": "48"})
    sys.path.insert(0, str(HERE.parent))  # splat_locate.py, live/care_space.py of this checkout
    sys.path.insert(0, str(HERE.parent / "live"))
    import care_space
    import splat_locate
    sys.path.insert(1, str(Path(args.root)))
    sys.path.insert(1, str(Path(args.root) / "live"))
    import recon_change as rc

    room = json.loads(Path(args.room).read_text())
    M = np.asarray(room["matrix"], float)
    A, shift = M[:3, :3], M[:3, 3]
    scale = float(np.cbrt(abs(np.linalg.det(A))))
    turn = A / scale
    trained = Path(args.trained)
    with np.load(trained / "cameras.npz") as got:
        E_tr, kn = np.asarray(got["E"], float), [int(n) for n in got["keyframes"]]
    room_kn = [int(n) for n in room["keyframe_numbers"]]
    cams = {int(c["frame"]): c for c in room["cameras"]}
    files = {int(p.name.split("_")[0]): p for p in Path(args.frames).glob("*.jpg")}
    last = max(files)

    def truth(n):
        got = care_space.pose_at(E_tr, kn, n)
        if got is None:
            return None
        E = got[0]
        C = -E[:, :3].T @ E[:, 3]
        f = E[2, :3]
        pos = A @ C + shift
        ahead = turn @ f
        return {"centre": C, "forward": f, "position": pos, "heading": heading_of(ahead)}

    def room_truth(n):  # the room map's own cameras, between the two room pictures around frame n
        k = int(np.searchsorted(room_kn, n))
        if k == 0 or k >= len(room_kn):
            return None
        a, b = cams.get(k - 1), cams.get(k)
        if a is None or b is None or room_kn[k] - room_kn[k - 1] > 120:
            return None
        w = (n - room_kn[k - 1]) / (room_kn[k] - room_kn[k - 1])
        pa, pb = np.asarray(a["position"], float), np.asarray(b["position"], float)
        return {"position": pa + w * (pb - pa), "heading": wrap(a["heading_deg"] + w * wrap(b["heading_deg"] - a["heading_deg"]))}

    def to_map_pose(C, f):
        return A @ np.asarray(C, float) + shift, heading_of(turn @ np.asarray(f, float))

    def score(pos, head, t, rt=None):
        pos = np.asarray(pos, float)
        out = {"err_m": round(float(np.linalg.norm(pos[:2] - t["position"][:2])), 4),
               "err3_m": round(float(np.linalg.norm(pos - t["position"])), 4),
               "head_deg": round(abs(wrap(head - t["heading"])), 2), "position": [round(float(v), 3) for v in pos], "heading": round(head, 1)}
        if rt is not None:
            out["vs_room_m"] = round(float(np.linalg.norm(pos[:2] - rt["position"][:2])), 4)
            out["vs_room_deg"] = round(abs(wrap(head - rt["heading"])), 2)
        return out

    stream = [n for n in range(kn[0], kn[-1] + 1, args.step) if n in files]
    if args.limit:
        stream = stream[:args.limit]

    def gap_to(n, numbers):
        return min(abs(n - k) for k in numbers)

    held = {n for n in stream if gap_to(n, kn) >= args.gap and gap_to(n, room_kn) >= args.gap and truth(n) is not None}
    variants = [("easy", 0)] + ([("hard", args.hard)] if args.hard else [])
    print(f"{len(stream)} stream photos, {len(held)} scored; variants {variants}", flush=True)

    model = None
    if methods & {"today", "vggt"}:
        model = rc.load_model(device="cuda")
    worker = None
    if "today" in methods:
        import worker as worker_module
        worker = worker_module
        worker.INFO.update({"eval": True})
        original_share = worker.mapped_share
    pictures = sorted((Path(args.zone_data) / "baselines" / args.zone).glob("*.jpg"))
    pic_shape = cv2.imread(str(pictures[0])).shape[:2]
    pic_keys = [(i, quick_print(cv2.imread(str(p)), pic_shape)) for i, p in enumerate(pictures) if i in cams]
    kf_imgs = [cv2.imread(str(trained / "keyframes" / f"{q:03d}.jpg")) for q in range(len(E_tr))]
    kf_keys = [(q, quick_print(im, im.shape[:2])) for q, im in enumerate(kf_imgs)]
    fov_room = float(room.get("fov_deg") or 52.5)
    views = json.loads((trained / "views.json").read_text())
    K_tr = np.asarray(views["K"], float).reshape(-1, 3, 3)
    fov_kf = math.degrees(2 * math.atan(views["size"][0] / 2 / float(np.median(K_tr[:, 0, 0]))))
    depth_tr = np.fromfile(trained / views["depths"], np.float16).reshape(-1, views["depth_size"][1], views["depth_size"][0]).astype(np.float32)
    index = None
    if "pnp" in methods:
        index = splat_locate.SplatIndex(trained)
        print(f"PnP index: {index.build_seconds} s, {sum(index.lifted)} lifted, {index.triangulated} triangulated", flush=True)
    kf_tokens = None
    if "vggt" in methods:
        import torch
        tok = torch.cat([rc.patch_tokens(model, kf_imgs[i:i + 8]) for i in range(0, len(kf_imgs), 8)]).float()
        centre = tok.reshape(-1, tok.shape[-1]).mean(0)
        kf_tokens = (torch.nn.functional.normalize(tok - centre, dim=-1).half(), centre)

    def vggt_fix(square, banned):
        import torch
        keys, centre = kf_tokens
        new = torch.nn.functional.normalize(rc.patch_tokens(model, [square])[0].float() - centre, dim=-1)
        best = torch.einsum("pc,nqc->pnq", new.to(keys.dtype), keys).amax(-1).float()
        share = (best >= 0.6).float().mean(0).cpu().numpy()
        share[list(banned)] = -1
        top = [int(q) for q in np.argsort(-share)[:args.kf_vggt] if share[q] >= 0]
        t0 = time.time()
        r = rc.reconstruct(model, [kf_imgs[q] for q in top] + [square], long_side=518)
        took = time.time() - t0
        k = len(top)
        Rp, tp = r.E[:k, :, :3].astype(float), r.E[:k, :, 3].astype(float)
        Rs, ts = E_tr[top, :, :3], E_tr[top, :, 3]
        Q = chordal_mean([Rs[i].T @ Rp[i] for i in range(k)])
        ratios = []
        h, w = depth_tr.shape[1:]
        for i, q in enumerate(top):
            dp = cv2.resize(r.depth[i], (w, h), interpolation=cv2.INTER_NEAREST)
            good = (depth_tr[q] > 0) & (dp > 1e-6)
            ratios.append(depth_tr[q][good] / dp[good])
        s = float(np.median(np.concatenate(ratios))) if ratios else float("nan")
        Cp = np.array([-Rp[i].T @ tp[i] for i in range(k)])
        Cs = np.array([-Rs[i].T @ ts[i] for i in range(k)])
        d = (Cs - s * Cp @ Q.T).mean(0)
        resid = float(np.sqrt(((Cs - (s * Cp @ Q.T + d)) ** 2).sum(1).mean()))
        rot_resid = float(np.degrees(np.median([math.acos(np.clip((np.trace(Rs[i].T @ Rp[i] @ Q.T) - 1) / 2, -1, 1)) for i in range(k)])))
        Rq, tq = r.E[k][:, :3].astype(float), r.E[k][:, 3].astype(float)
        Cq = s * Q @ (-Rq.T @ tq) + d
        fq = (Rq @ Q.T)[2]
        return {"centre": Cq, "forward": fq, "keyframes": top, "resid_m": resid * scale, "rot_resid_deg": rot_resid,
                "share_best": float(share.max()), "seconds": round(took, 2)}

    records = []
    for name, reach in variants:
        def banned_kf(n):
            return {q for q, k in enumerate(kn) if abs(k - n) <= reach} if reach else set()

        def banned_pic(n):
            return {i for i, k in enumerate(room_kn) if abs(k - n) <= reach and i != 0} if reach else set()

        recent = {}  # the relay's instant-fix references: {"model": (print, pose), "quick": (print, pose)}
        prior = None
        for s_i, n in enumerate(stream):
            frame = cv2.imread(str(files[n]))
            if frame is None:
                continue
            photo = photo_like_app(frame)
            t = truth(n)
            rt = room_truth(n)
            rec = {"variant": name, "n": n, "scored": n in held, "stream_i": s_i, "t_s": round((n - stream[0]) / 30.0, 3)}
            if t is not None:
                rec["truth"] = {"position": [round(float(v), 3) for v in t["position"]], "heading": round(t["heading"], 1)}
                if rt is not None:
                    rec["fit_m"] = round(float(np.linalg.norm(rt["position"][:2] - t["position"][:2])), 4)
                    rec["fit_deg"] = round(abs(wrap(rt["heading"] - t["heading"])), 2)
            full = s_i % args.full_every == 0
            if "today" in methods and (full or n in held):
                excl = banned_pic(n)

                def patched(model_, zone_, files_, frame_, _excl=excl):
                    share, per, med = original_share(model_, zone_, files_, frame_)
                    per = per.copy()
                    per[list(_excl)] = -1.0
                    return share, per, med

                worker.mapped_share = patched if excl else original_share
                job = Path(args.zone_data) / "jobs" / f"{name}-{n:06d}"
                job.mkdir(parents=True, exist_ok=True)
                (job / "clip.zip").write_bytes(one_photo_zip(photo))
                t0 = time.time()
                try:
                    res = worker.where(model, job, {"op": "locate", "zone": args.zone, "file": "clip.zip", "grow": False})
                    rec["today"] = {"state": res["state"], "confidence": res.get("confidence"), "seconds": round(time.time() - t0, 2)}
                    if t is not None and res.get("position") is not None and res["state"] != "lost":
                        rec["today"].update(score(res["position"], float(res["heading_deg"]), t, rt))
                    elif res.get("position") is not None:
                        rec["today"].update(position=res["position"], heading=res["heading_deg"])
                except Exception as err:  # noqa: BLE001
                    rec["today"] = {"state": "failed", "error": f"{type(err).__name__}: {err}"[:200]}
                for leftover in (job / "out").glob("*"):
                    leftover.unlink()
            if "quick" in methods:  # the relay's instant fix, with its chained references, between full fixes
                now = quick_print(photo, pic_shape)
                refs = [(("picture", i), k) for i, k in pic_keys if i not in banned_pic(n)]
                for kind in ("model", "quick"):
                    if kind in recent and recent[kind][0].shape == now.shape:
                        refs.append(((kind, 0), recent[kind][0]))
                sim, ref, slide = quick_match(now, refs)[0]
                base = cams[ref[1]] if ref[0] == "picture" else recent[ref[0]][1]
                head = wrap(base["heading_deg"] - slide * fov_room)
                rec["quick"] = {"similarity": round(sim, 3), "ref": f"{ref[0]}{ref[1]}", "state": "found" if sim >= 0.7 else "unsure" if sim >= 0.45 else "lost"}
                if t is not None:
                    rec["quick"].update(score(base["position"], head, t, rt))
                if sim >= 0.7:
                    recent["quick"] = (now, {"position": base["position"], "heading_deg": head})
                today = rec.get("today")
                if full and today and today.get("state") == "found" and today.get("position") is not None:
                    recent["model"] = (now, {"position": today["position"], "heading_deg": today["heading"]})
            if "kfquick" in methods:
                now = quick_print(photo, (518, 518))
                bk = banned_kf(n)
                sim, q, slide = quick_match(now, [(q, k) for q, k in kf_keys if q not in bk])[0]
                pos, head = to_map_pose(-E_tr[q][:, :3].T @ E_tr[q][:, 3], E_tr[q][2, :3])
                rec["kfquick"] = {"similarity": round(sim, 3), "keyframe": int(q)}
                if t is not None:
                    rec["kfquick"].update(score(pos, wrap(head - slide * fov_kf), t))
            if "pnp" in methods:
                bk = banned_kf(n)
                for mode, use_prior in (("cold", None), ("track", prior)):
                    f = index.locate(photo, prior=use_prior, exclude=bk)
                    r = {"ok": f["ok"], "why": f.get("why"), "matches": f.get("matches"), "inliers": f.get("inliers"),
                         "seconds": f.get("seconds")}
                    if f["ok"]:
                        m = splat_locate.to_map(f, M)
                        r.update(sigma_m=round(m["sigma_m"], 4), rms_px=f["rms_px"], turn_sd_deg=f["turn_sd_deg"],
                                 pitch=round(m["pitch_deg"], 1), cov=np.round(m["cov"], 6).tolist())
                        if t is not None:
                            r.update(score(m["position"], m["heading_deg"], t))
                        if mode == "track":
                            prior = {"centre": f["centre"], "forward": f["forward"], "n": n}
                    rec["pnp_" + mode] = r
                if prior is not None and n - prior.get("n", n) > 90:
                    prior = None
            if "vggt" in methods and (full or n in held):
                square = cv2.resize(splat_locate.middle_square(photo), (518, 518), interpolation=cv2.INTER_AREA)
                try:
                    v = vggt_fix(square, banned_kf(n))
                    pos, head = to_map_pose(v["centre"], v["forward"])
                    rec["vggt"] = {k: v[k] for k in ("keyframes", "share_best", "seconds")}
                    rec["vggt"].update(resid_m=round(v["resid_m"], 4), rot_resid_deg=round(v["rot_resid_deg"], 2))
                    if t is not None:
                        rec["vggt"].update(score(pos, head, t))
                except Exception as err:  # noqa: BLE001
                    rec["vggt"] = {"error": f"{type(err).__name__}: {err}"[:200]}
            records.append(rec)
            if len(records) % 10 == 0:
                print(f"{name}: {s_i + 1}/{len(stream)} photos", flush=True)
                Path(args.out).write_text(json.dumps({"records": records}, indent=0))

    summary = {}
    for name, _ in variants:
        rows = [r for r in records if r["variant"] == name and r["scored"]]
        out = {"scored": len(rows)}
        fits = [r["fit_m"] for r in rows if "fit_m" in r]
        if fits:
            out["fit"] = {"median_m": round(float(np.median(fits)), 3), "p95_m": round(float(np.percentile(fits, 95)), 3)}
        for key in ("today", "quick", "kfquick", "pnp_cold", "pnp_track", "vggt"):
            got = [r[key] for r in rows if key in r]
            placed = [g for g in got if "err_m" in g]
            if not got:
                continue
            e = np.array([g["err_m"] for g in placed]) if placed else np.zeros(0)
            h = np.array([g["head_deg"] for g in placed]) if placed else np.zeros(0)
            out[key] = {"asked": len(got), "placed": len(placed)}
            if len(e):
                out[key].update(median_m=round(float(np.median(e)), 3), p95_m=round(float(np.percentile(e, 95)), 3),
                                max_m=round(float(e.max()), 3), within_05m=round(float((e <= 0.5).mean()), 3),
                                median_deg=round(float(np.median(h)), 2), p95_deg=round(float(np.percentile(h, 95)), 2))
            vs = [g["vs_room_m"] for g in placed if "vs_room_m" in g]
            if vs:
                out[key].update(vs_room_median_m=round(float(np.median(vs)), 3), vs_room_p95_m=round(float(np.percentile(vs, 95)), 3))
        summary[name] = out
    Path(args.out).write_text(json.dumps({"summary": summary, "records": records, "scale_m_per_unit": scale,
                                          "stream": len(stream), "held": len(held)}, indent=0))
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
