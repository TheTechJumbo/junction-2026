#!/usr/bin/env python3
"""Where the wearer is in a trained splat, from one photo, on the laptop's CPU (no model, no GPU).

A trained splat (video_map.py) keeps its keyframes next to it: each one's photo (518 x 518, the middle square of
the recording frame), its camera (K, world-to-camera E) and its trusted depth (views.json, depths.bin), all in the
splat's own axes. So a point seen in a keyframe photo has a known 3D position in the splat. A new photo is placed
by matching its SIFT features with the keyframes that look most like it (and those near the last pose), lifting
the matched keyframe points to 3D with their depth, and solving for the camera that sees those 3D points where the
photo shows them (PnP inside RANSAC, then least squares on the inliers). The answer is a camera in the splat's own
axes: it needs neither the room map nor the fit between the room map and the splat (care_space.py), so where the
page draws the splat, the wearer is drawn in the right place.

How sure: the inliers' reprojection residuals give the pose's covariance (sigma^2 (J^T J)^-1). The residuals
miss the depth's own errors, so the spread is multiplied by SIGMA_GAIN, measured on held-out frames of a
recording (eval/POSITION_RESULTS.md) so that the stated 95% radius holds about 95% of the real errors.

    index = SplatIndex(folder)            # once per trained splat: SIFT of the 96 keyframes, triangulation (~30 s)
    fix = index.locate(photo_bgr, prior)  # 0.3-0.7 s on a laptop CPU; fix["ok"], fix["centre"], fix["R"], fix["sigma"]

PoseFilter turns the stream of fixes (in map axes, metres) into one steady pose: constant velocity, no move
faster than a person walks, a fix that disagrees by more than its uncertainty allows is held back until a second
one agrees with it. Live is what the relay keeps (an index per splat, a filter per zone, the live camera's focal
length found from the first photos).

On held-out frames of the venue recording (eval/POSITION_RESULTS.md), as the pages show it: median 6 cm, 95th
percentile 0.67 m, against 0.42 m and 4.1 m for the room map's fixes on the caretaker's page before.
"""
import json
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

SIDE = 518  # the keyframes' size (video_map.SIDE)
PRINT_W = 48  # small grey pictures for picking the keyframes to match (as the relay's instant fix, a little smaller)
PRINT_SHIFT = 0.2  # slid sideways up to this share of the width
TOP_PRINTS = 2  # keyframes picked by how alike the small pictures are
TOP_VOTES = 5  # keyframes picked by how many of the photo's features they share (one search over all keyframes)
NEAR_PRIOR = 3  # and the keyframes nearest the last pose, when there is one
N_FEATURES = 4000  # per keyframe
N_QUERY = 2500  # per photo (matching is most of the time; 4000 placed no more)
N_VOTE = 1500  # the photo's strongest features vote for keyframes
RATIO = 0.82  # Lowe's ratio test on RootSIFT distances
# Reprojection error of an inlier, in pixels of the 518 px square. Neighbouring keyframes' depths agree to about
# 1.5% of the depth (VGGT), which is 8 pixels: a tighter limit throws out good points.
RANSAC_PX = 10.0
MIN_INLIERS = 18
MIN_SHARE = 0.12  # inliers / matches
EDGE_REL = 0.1  # a keypoint whose 3 x 3 depth neighbourhood spreads more than this (relative) is on a depth edge
# Keyframe features without a trusted depth are triangulated with the next TRI_REACH keyframes (see triangulate)
TRIANGULATE, TRI_REACH, TRI_RATIO, TRI_PX, TRI_DEG = True, 4, 0.8, 6.0, 1.0
# The residuals' covariance misses the depth's errors: the stated spread is this many times the covariance's
# (measured on held-out recording frames, eval/POSITION_RESULTS.md).
SIGMA_GAIN = 8.0
SIGMA_FLOOR = 0.01  # splat units: never claim better than this


def middle_square(frame):
    """The middle square of a picture (the keyframes are the middle square of a portrait frame, worker.square_up)."""
    h, w = frame.shape[:2]
    if h > w:
        top = (h - w) // 2
        return frame[top:top + w]
    left = (w - h) // 2
    return frame[:, left:left + h]


def prepare(frame):
    """A photo as the keyframes are: middle square, SIDE x SIDE, grey."""
    square = cv2.resize(middle_square(frame), (SIDE, SIDE), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(square, cv2.COLOR_BGR2GRAY) if square.ndim == 3 else square


def small_print(grey):
    small = cv2.GaussianBlur(cv2.resize(grey, (PRINT_W, PRINT_W), interpolation=cv2.INTER_AREA), (3, 3), 0).astype(np.float32)
    return (small - small.mean()) / (small.std() + 1e-3)


def print_scores(now, prints):
    """Best cosine similarity of `now` with each print, slid sideways up to PRINT_SHIFT of the width. (N,)"""
    reach = int(PRINT_SHIFT * PRINT_W)
    best = np.full(len(prints), -1.0)
    for s in range(-reach, reach + 1, 2):
        a = now[:, max(0, s):PRINT_W + min(0, s)]
        b = prints[:, :, max(0, -s):PRINT_W - max(0, s)]
        a = a - a.mean()
        b = b - b.mean(axis=(1, 2), keepdims=True)
        score = (b * a).sum(axis=(1, 2)) / (np.linalg.norm(a) * np.linalg.norm(b.reshape(len(b), -1), axis=1) + 1e-6)
        best = np.maximum(best, score)
    return best


def root_sift(desc):
    if desc is None:
        return np.zeros((0, 128), np.float32)
    desc = desc / (np.abs(desc).sum(1, keepdims=True) + 1e-7)
    return np.sqrt(desc).astype(np.float32)


def rodrigues(w):
    return cv2.Rodrigues(np.asarray(w, np.float64).reshape(3, 1))[0]


def project(K, R, t, X):
    cam = X @ R.T + t
    z = cam[:, 2:3]
    return (cam[:, :2] / np.where(np.abs(z) < 1e-9, 1e-9, z)) * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]], cam[:, 2]


def pose_covariance(K, R, C, X, x):
    """Covariance of (small turn of the world, camera centre) from the reprojection residuals: sigma^2 (J^T J)^-1.
    Returns (6 x 6 covariance, residual rms in pixels)."""
    def residual(p):
        Rp = rodrigues(p[:3]) @ R
        Cp = C + p[3:]
        return (project(K, Rp, -Rp @ Cp, X)[0] - x).ravel()

    r0 = residual(np.zeros(6))
    n = len(x)
    scale = max(1e-6, float(np.median(np.linalg.norm(X - C, axis=1))))
    J = np.zeros((len(r0), 6))
    for k in range(6):
        eps = 1e-4 if k < 3 else 1e-4 * scale
        d = np.zeros(6)
        d[k] = eps
        J[:, k] = (residual(d) - residual(-d)) / (2 * eps)
    dof = max(1, 2 * n - 6)
    s2 = float(r0 @ r0) / dof
    try:
        cov = s2 * np.linalg.inv(J.T @ J)
    except np.linalg.LinAlgError:
        cov = np.full((6, 6), np.inf)
    return cov, float(np.sqrt((r0 ** 2).reshape(-1, 2).sum(1).mean()))


class SplatIndex:
    """A trained splat's keyframes, ready to place photos in (see the module's docstring). folder: the trained
    splat's folder with views.json and depths.bin, and the keyframe photos as views.json names them (kf000.jpg ...,
    as the relay keeps them) or as keyframes/000.jpg (video_map.py's own output)."""

    def __init__(self, folder, exclude=None):
        folder = Path(folder)
        v = json.loads((folder / "views.json").read_text())
        w, h = v["depth_size"]
        self.size = tuple(v["size"])
        self.depth = np.fromfile(folder / v["depths"], np.float16).reshape(-1, h, w).astype(np.float32)
        self.K = np.asarray(v["K"], float).reshape(-1, 3, 3)
        self.E = np.asarray(v["E"], float).reshape(-1, 3, 4)
        S = len(self.E)
        self.centres = np.array([-e[:, :3].T @ e[:, 3] for e in self.E])
        self.forward = self.E[:, 2, :3].copy()
        self.median_depth = float(v.get("median_depth") or np.median(self.depth[self.depth > 0]))
        spread = np.ptp(self.centres, axis=0)
        self.low = self.centres.min(0) - 0.5 * spread - 3 * self.median_depth
        self.high = self.centres.max(0) + 0.5 * spread + 3 * self.median_depth
        self.Kq = np.median(self.K, axis=0)  # a photo from the same camera, cut the same way
        self.sift = cv2.SIFT_create(nfeatures=N_FEATURES, contrastThreshold=0.01)
        self.sift_query = cv2.SIFT_create(nfeatures=N_QUERY, contrastThreshold=0.01)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.prints = np.zeros((S, PRINT_W, PRINT_W), np.float32)
        # per keyframe: every feature (matching uses all, so the ratio test sees the look-alikes), and the 3D point of
        # those with a trusted depth (NaN for the others: matched, then dropped)
        self.points, self.desc, self.kpts = [], [], []
        self.usable = np.zeros(S, bool)
        files = v.get("files") or [f"kf{q:03d}.jpg" for q in range(S)]
        t0 = time.time()
        for q in range(S):
            path = folder / files[q]
            if not path.exists():
                path = folder / "keyframes" / f"{q:03d}.jpg"
            picture = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if picture is None:
                self.points.append(np.zeros((0, 3)))
                self.desc.append(np.zeros((0, 128), np.float32))
                self.kpts.append(np.zeros((0, 2)))
                continue
            if picture.shape[:2] != (self.size[1], self.size[0]):
                picture = cv2.resize(picture, self.size, interpolation=cv2.INTER_AREA)
            self.prints[q] = small_print(picture)
            kps, desc = self.sift.detectAndCompute(picture, None)
            pts = np.array([k.pt for k in kps], float).reshape(-1, 2)
            xyz, ok = self.lift(q, pts)
            xyz[~ok] = np.nan
            self.points.append(xyz)
            self.desc.append(root_sift(desc))
            self.kpts.append(pts)
        self.lifted = [int(np.isfinite(p[:, 0]).sum()) for p in self.points]
        self.triangulated = self.triangulate() if TRIANGULATE else 0
        self.usable = np.array([np.isfinite(p[:, 0]).sum() >= MIN_INLIERS for p in self.points])
        # one search over every keyframe's features, to pick the keyframes that share the most with a photo
        self.owner = np.concatenate([np.full(len(d), q) for q, d in enumerate(self.desc)]) if S else np.zeros(0, int)
        self.flann = cv2.flann_Index(np.concatenate(self.desc).astype(np.float32), {"algorithm": 1, "trees": 4})

        self.exclude = set(exclude or ())
        self.build_seconds = round(time.time() - t0, 2)

    def triangulate(self):
        """3D points for keyframe features that have no trusted depth: the exported depth is trusted only where two
        other keyframes confirm it, which leaves out most of the corners SIFT finds (in the venue, 8 of 234 matches
        between two neighbouring keyframes had a depth). A feature matched in a neighbouring keyframe is triangulated
        with the two keyframes' own cameras (in the splat's axes), kept when it lands in front of both, within
        TRI_PX of both features and seen at least TRI_DEG apart. Returns how many features got a point."""
        S = len(self.E)
        P = [self.K[q] @ self.E[q] for q in range(S)]
        found = [dict() for _ in range(S)]  # feature -> [3D points]
        for q in range(S):
            for p in range(q + 1, min(S, q + TRI_REACH + 1)):
                if len(self.desc[q]) < 8 or len(self.desc[p]) < 8:
                    continue
                pairs = [a for a, b in (m for m in self.matcher.knnMatch(self.desc[q], self.desc[p], k=2) if len(m) == 2)
                         if a.distance < TRI_RATIO * b.distance]
                if len(pairs) < 8:
                    continue
                iq = np.array([a.queryIdx for a in pairs])
                ip = np.array([a.trainIdx for a in pairs])
                xq, xp = self.kpts[q][iq], self.kpts[p][ip]
                X = cv2.triangulatePoints(P[q], P[p], xq.T, xp.T)
                X = (X[:3] / np.where(np.abs(X[3]) < 1e-12, 1e-12, X[3])).T
                good = np.ones(len(X), bool)
                for k, x in ((q, xq), (p, xp)):
                    uv, z = project(self.K[k], self.E[k][:, :3], self.E[k][:, 3], X)
                    good &= (z > 0) & (np.linalg.norm(uv - x, axis=1) < TRI_PX)
                a, b = X - self.centres[q], X - self.centres[p]
                cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-12)
                good &= cos < math.cos(math.radians(TRI_DEG))
                for i, j, x in zip(iq[good], ip[good], X[good]):
                    found[q].setdefault(int(i), []).append(x)
                    found[p].setdefault(int(j), []).append(x)
        added = 0
        for q in range(S):
            for i, xs in found[q].items():
                if np.isfinite(self.points[q][i, 0]):
                    continue
                self.points[q][i] = np.median(np.array(xs), axis=0)
                added += 1
        return added

    def lift(self, q, pts):
        """Keyframe q's pixels (n, 2) as points in the splat's axes, and which have a trusted depth off any edge."""
        depth = self.depth[q]
        h, w = depth.shape
        W, H = self.size
        j = np.clip(np.rint(pts[:, 0] * w / W).astype(int), 1, w - 2)
        i = np.clip(np.rint(pts[:, 1] * h / H).astype(int), 1, h - 2)
        block = np.stack([depth[i + di, j + dj] for di in (-1, 0, 1) for dj in (-1, 0, 1)], 1)
        z = depth[i, j]
        ok = (block > 0).all(1) & (z > 0)
        spread = (block.max(1) - block.min(1)) / np.maximum(z, 1e-6)
        ok &= spread < EDGE_REL
        K, E = self.K[q], self.E[q]
        cam = np.stack([(pts[:, 0] - K[0, 2]) / K[0, 0] * z, (pts[:, 1] - K[1, 2]) / K[1, 1] * z, z], 1)
        world = (cam - E[:, 3]) @ E[:, :3]
        return world, ok

    def candidates(self, grey, desc, prior=None, exclude=()):
        """The keyframes to match a photo with: those sharing the most features with it (each of the photo's
        features votes for the keyframe of its nearest feature anywhere, when that one is clearly nearest), the
        ones whose small pictures look most alike, and with a prior the ones near it looking the same way."""
        S = len(self.E)
        banned = set(exclude) | self.exclude
        allowed = np.array([q not in banned and self.usable[q] for q in range(S)])
        scores = print_scores(small_print(grey), self.prints)
        scores[~allowed] = -2
        idx, dist = self.flann.knnSearch(desc[:N_VOTE], 3, params={"checks": 32})
        owner = self.owner[idx]
        # a vote when the nearest feature is clearly nearer than the nearest in ANOTHER keyframe (the same spot seen in
        # two neighbouring keyframes must not cancel itself out)
        other = np.where(owner[:, 1] != owner[:, 0], dist[:, 1], dist[:, 2])
        clear = dist[:, 0] < (RATIO ** 2) * other  # flann gives squared distances
        votes = np.bincount(owner[clear, 0], minlength=S).astype(float)
        votes[~allowed] = -1
        picked = []
        for q in np.argsort(-votes)[:TOP_VOTES]:
            if votes[q] > 0:
                picked.append(int(q))
        for q in np.argsort(-scores):
            if len(picked) >= TOP_VOTES + TOP_PRINTS or scores[q] <= -2:
                break
            if int(q) not in picked:
                picked.append(int(q))
        if prior is not None and prior.get("centre") is not None:
            c = np.asarray(prior["centre"], float)
            f = np.asarray(prior.get("forward", [0, 0, 1]), float)
            # near the last pose, and looking the same way (a camera behind the wearer sees nothing they see)
            cost = np.linalg.norm(self.centres - c, axis=1) / self.median_depth + 1.5 * (1 - self.forward @ f)
            added = 0
            for q in np.argsort(cost):
                if added >= NEAR_PRIOR:
                    break
                if int(q) not in picked and allowed[q]:
                    picked.append(int(q))
                    added += 1
        return picked, scores, votes

    def locate(self, frame, prior=None, exclude=(), focal_scale=1.0):
        """frame: a BGR (or grey) photo. prior: {"centre", "forward"} of a recent pose in the splat's axes, or None.
        exclude: keyframes not to use (held-out tests). Returns a dict; "ok" False with "why" when not placed."""
        t0 = time.time()
        grey = prepare(frame)
        if float(grey.mean()) < 25:
            return {"ok": False, "why": "too dark", "seconds": round(time.time() - t0, 3)}
        kps, desc = self.sift_query.detectAndCompute(grey, None)
        if desc is None or len(kps) < MIN_INLIERS:
            return {"ok": False, "why": "too few features (blurred or bare)", "seconds": round(time.time() - t0, 3)}
        order = np.argsort([-k.response for k in kps])  # the strongest first (they vote for keyframes)
        desc = root_sift(desc)[order]
        x = np.array([kps[i].pt for i in order], float)
        picked, scores, votes = self.candidates(grey, desc, prior, exclude)
        best = {}  # query keypoint -> (distance, 3D point, keyframe)
        for q in picked:
            if len(self.desc[q]) < 2:
                continue
            for pair in self.matcher.knnMatch(desc, self.desc[q], k=2):  # exact: a search tree placed 5% fewer photos
                if len(pair) < 2:
                    continue
                m, second = pair
                point = self.points[q][m.trainIdx]
                if m.distance < RATIO * second.distance and np.isfinite(point[0]) and \
                        (m.queryIdx not in best or m.distance < best[m.queryIdx][0]):
                    best[m.queryIdx] = (m.distance, point, q)
        n = len(best)
        out = {"ok": False, "matches": n, "keyframes": picked, "print_best": round(float(scores.max()), 3),
               "votes_best": int(votes.max()) if len(votes) else 0}
        if n < MIN_INLIERS:
            return {**out, "why": f"{n} matches", "seconds": round(time.time() - t0, 3)}
        idx = np.array(list(best.keys()))
        X = np.array([best[i][1] for i in idx], float)
        img = x[idx]
        frames = np.array([best[i][2] for i in idx])
        scales = [focal_scale] if np.isscalar(focal_scale) else list(focal_scale)
        tries = [(s, self.solve(X, img, frames, s, n)) for s in scales]
        out["focal_tries"] = {round(s, 3): (got.get("inliers") or 0) for s, got in tries}
        s, got = max(tries, key=lambda sg: (sg[1]["ok"], sg[1].get("inliers") or 0))
        return {**out, **got, "focal_scale": s, "seconds": round(time.time() - t0, 3)}

    def solve(self, X, img, frames, focal_scale, n):
        """PnP inside RANSAC on the matched 3D points X and pixels img, the inliers refined by least squares, for a
        camera with the keyframes' focal length times focal_scale. A dict with "ok" and the pose, or "why"."""
        K = self.Kq.copy()
        K[0, 0] *= focal_scale
        K[1, 1] *= focal_scale
        ok, rvec, tvec, inl = cv2.solvePnPRansac(X, img, K, None, iterationsCount=1500, reprojectionError=RANSAC_PX,
                                                 confidence=0.999, flags=cv2.SOLVEPNP_AP3P)
        if not ok or inl is None or len(inl) < MIN_INLIERS:
            return {"ok": False, "why": f"{0 if inl is None else len(inl)} inliers", "inliers": 0 if inl is None else len(inl)}
        inl = inl.ravel()
        for _ in range(2):  # least squares on the inliers, then the inliers again with the better pose
            rvec, tvec = cv2.solvePnPRefineLM(X[inl], img[inl], K, None, rvec, tvec)
            R = cv2.Rodrigues(rvec)[0]
            uv, z = project(K, R, tvec.ravel(), X)
            inl = np.flatnonzero((np.linalg.norm(uv - img, axis=1) < RANSAC_PX) & (z > 0))
            if len(inl) < MIN_INLIERS:
                return {"ok": False, "why": f"{len(inl)} inliers after refining", "inliers": int(len(inl))}
        R = cv2.Rodrigues(rvec)[0]
        t = tvec.ravel()
        C = -R.T @ t
        share = len(inl) / n
        out = {"ok": False, "inliers": int(len(inl)), "share": round(share, 3)}
        if share < MIN_SHARE:
            return {**out, "why": f"only {share:.0%} of the matches agree"}
        if not (np.all(C > self.low) and np.all(C < self.high)):
            return {**out, "why": "outside the recorded space"}
        cov, rms = pose_covariance(K, R, C, X[inl], img[inl])
        pos_cov = cov[3:, 3:] * SIGMA_GAIN ** 2 + np.eye(3) * SIGMA_FLOOR ** 2
        turn_sd = math.degrees(math.sqrt(max(0.0, float(np.trace(cov[:3, :3])) / 3))) * SIGMA_GAIN
        votes = np.bincount(frames[inl], minlength=len(self.E))
        out.update(ok=True, centre=C, R=R, E=np.hstack([R, t[:, None]]), forward=R[2].copy(), cov=pos_cov,
                   sigma=float(np.sqrt(np.trace(pos_cov) / 3)), turn_sd_deg=round(turn_sd, 2), rms_px=round(rms, 2),
                   keyframe=int(np.argmax(votes)), keyframe_votes=int(votes.max()))
        return out


# ------------------------------------------------------------------ splat axes -> map axes
def to_map(fix, matrix):
    """A splat-axes fix through the /care/space matrix (rows; scale * rotation | shift) into map axes: position
    (east, north, up) in metres, heading (degrees from north towards east), pitch (degrees, up +), and the
    horizontal 1-sigma radius in metres."""
    M = np.asarray(matrix, float)
    A, shift = M[:3, :3], M[:3, 3]
    scale = float(np.cbrt(abs(np.linalg.det(A))))
    turn = A / scale
    pos = A @ np.asarray(fix["centre"], float) + shift
    ahead = turn @ np.asarray(fix["forward"], float)
    heading = math.degrees(math.atan2(ahead[0], ahead[1]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, ahead[2] / (np.linalg.norm(ahead) + 1e-12)))))
    cov = turn @ np.asarray(fix["cov"], float) @ turn.T * scale ** 2
    flat = np.linalg.eigvalsh(cov[:2, :2])
    return {"position": pos, "heading_deg": heading, "pitch_deg": pitch, "sigma_m": float(math.sqrt(max(flat.max(), 0.0))),
            "cov": cov, "scale": scale}


def wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


# ------------------------------------------------------------------ the steady pose
WALK_M_S = 1.8  # no faster than this between two fixes (a brisk walk), plus the fixes' own uncertainty
TURN_DEG_S = 240.0
ACCEL = 1.2  # m/s^2: how quickly a walking person changes speed (the filter's process noise)
GATE = 30.0  # chi-square (3 degrees of freedom): a fix further off than this, and than a walk allows, is held back
HOLD_S = 2.5  # with no fix for this long the velocity is forgotten (the wearer may have stopped)
EXTRAPOLATE_S = 1.0  # between fixes the pose moves on at its velocity for at most this long
# Consecutive fixes share keyframes and depth errors, so the filter's own spread shrinks too far: the stated one is
# never under this (held-out frames: the raw fixes are within 0.15 m half the time beyond their median 5 cm).
MIN_SIGMA_M = 0.12
DRIFT_M_S = 0.15  # the uncertainty grows at least this fast between fixes, however still the wearer was
LOST_S = 20.0  # and after this the pose is dropped
TRAIL = 40


class PoseFilter:
    """One steady pose from fixes in map axes (metres). Constant velocity; each fix comes with its own covariance
    (to_map). A fix that disagrees with the pose more than both uncertainties and a walking person allow is held
    back; a second fix that agrees with it moves the pose there (the wearer was elsewhere than thought). Plain
    numbers and lists, so dump()/PoseFilter(saved) carry it across a restart."""

    def __init__(self, state=None):
        s = state or {}
        self.x = np.asarray(s["x"], float) if s.get("x") is not None else None
        self.P = np.asarray(s["P"], float) if s.get("P") is not None else None
        self.heading = s.get("heading")
        self.heading_sd = s.get("heading_sd", 30.0)
        self.pitch = s.get("pitch", 0.0)
        self.at = s.get("at")
        self.waiting = s.get("waiting")
        self.trail = list(s.get("trail") or [])
        self.held = int(s.get("held", 0))
        self.fixes = int(s.get("fixes", 0))

    def dump(self):
        return {"x": None if self.x is None else self.x.tolist(), "P": None if self.P is None else self.P.tolist(),
                "heading": self.heading, "heading_sd": self.heading_sd, "pitch": self.pitch, "at": self.at,
                "waiting": self.waiting, "trail": self.trail, "held": self.held, "fixes": self.fixes}

    def _predict(self, now):
        dt = max(0.0, now - self.at)
        if dt > HOLD_S:  # no fix for a while: the wearer may have stopped; keep the place, not the speed
            self.x[3:] = 0.0
            self.P[3:, 3:] = np.eye(3) * 0.5
            self.P[:3, 3:] = self.P[3:, :3] = 0.0
        F = np.eye(6)
        F[:3, 3:] = np.eye(3) * min(dt, HOLD_S)
        G = np.vstack([np.eye(3) * dt * dt / 2, np.eye(3) * dt])
        Q = G @ G.T * ACCEL ** 2
        Q[2, 2] += 0.02 ** 2 * dt  # height changes little (the head bobs)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        self.at = now

    def _start(self, pos, cov, heading, pitch, now):
        self.x = np.concatenate([pos, np.zeros(3)])
        self.P = np.zeros((6, 6))
        self.P[:3, :3] = cov
        self.P[3:, 3:] = np.eye(3) * 0.3
        self.heading, self.heading_sd, self.pitch, self.at = heading, 10.0, pitch, now
        self.waiting, self.held = None, 0

    def update(self, fix, now):
        """fix: to_map()'s dict (position, heading_deg, pitch_deg, cov), or None for a photo that was not placed.
        now: when the photo was taken (seconds). Returns the kind of step: start, agree, held, jump or miss."""
        if fix is None:
            return "miss"
        pos = np.asarray(fix["position"], float)
        cov = np.asarray(fix["cov"], float)
        heading, pitch = float(fix["heading_deg"]), float(fix.get("pitch_deg") or 0.0)
        self.fixes += 1
        if self.x is None or self.at is None or now - self.at > LOST_S:
            self._start(pos, cov, heading, pitch, now)
            return self._note(now, "start")
        last = self.at
        self._predict(max(now, last))
        y = pos - self.x[:3]
        S = self.P[:3, :3] + cov
        d2 = float(y @ np.linalg.solve(S, y))
        dt = max(1e-3, now - last)
        reach = WALK_M_S * dt + 3 * math.sqrt(max(np.trace(S) / 3, 0.0))
        if d2 > GATE and np.linalg.norm(y[:2]) > reach * 0.5 or np.linalg.norm(y[:2]) > reach:
            w = self.waiting
            if w and np.linalg.norm(pos[:2] - np.asarray(w["position"])[:2]) <= WALK_M_S * max(0.0, now - w["at"]) + \
                    3 * math.sqrt(max(np.trace(cov) / 3, 0.0) + w["var"]):
                self._start(pos, cov, heading, pitch, now)  # two fixes agree with each other: the wearer is there
                return self._note(now, "jump")
            self.waiting = {"position": pos.tolist(), "at": now, "var": float(np.trace(cov) / 3)}
            self.held += 1
            return self._note(now, "held")
        Kg = self.P[:, :3] @ np.linalg.inv(S)
        self.x = self.x + Kg @ y
        self.P = (np.eye(6) - Kg[:, :3] @ np.eye(3, 6)) @ self.P
        speed = float(np.linalg.norm(self.x[3:5]))
        if speed > WALK_M_S:
            self.x[3:5] *= WALK_M_S / speed
        # heading: a weighted step towards the fix, no faster than a head turns
        sd_fix = max(2.0, float(fix.get("turn_sd_deg") or 4.0))
        self.heading_sd = min(60.0, self.heading_sd + 60.0 * dt)  # a head turns: the old heading is worth less with time
        gain = self.heading_sd ** 2 / (self.heading_sd ** 2 + sd_fix ** 2)
        step = wrap(heading - self.heading)
        step = max(-TURN_DEG_S * dt - 3 * sd_fix, min(TURN_DEG_S * dt + 3 * sd_fix, step))
        self.heading = wrap(self.heading + gain * step)
        self.heading_sd = math.sqrt(1.0 / (1.0 / self.heading_sd ** 2 + 1.0 / sd_fix ** 2))
        self.pitch = self.pitch + 0.6 * (pitch - self.pitch)
        self.waiting, self.held = None, 0
        return self._note(now, "agree")

    def _note(self, now, kind):
        if self.x is not None and kind in ("start", "agree", "jump"):
            self.trail = (self.trail + [[round(now, 2), *[round(float(v), 3) for v in self.x[:3]]]])[-TRAIL:]
        return kind

    def current(self, now):
        """The pose as of now: position, heading, pitch, the 95% radius, and a state. Between fixes the position
        moves on at the filter's velocity for up to EXTRAPOLATE_S, and the uncertainty grows with time. While a fix
        that disagrees is held back, the velocity is not trusted (no moving on) and the uncertainty reaches out to
        that fix: the wearer is somewhere between the two until the next photo settles it."""
        if self.x is None or self.at is None or now - self.at > LOST_S:
            return {"state": "lost", "position": None, "heading_deg": None, "sigma_m": None, "radius95_m": None,
                    "trail": self.trail}
        age = max(0.0, now - self.at)
        # older is less sure: as far as the wearer could have gone at their own speed (a still one may start walking)
        speed = max(float(np.linalg.norm(self.x[3:5])), DRIFT_M_S)
        var = float(np.linalg.eigvalsh(self.P[:2, :2]).max()) + (min(age, LOST_S) * speed) ** 2
        pos = self.x[:3] + self.x[3:] * min(age, EXTRAPOLATE_S) * (age <= HOLD_S) * (not self.waiting)
        if self.waiting:
            var += (float(np.linalg.norm(np.asarray(self.waiting["position"])[:2] - pos[:2])) / 2.45) ** 2
        sigma = math.sqrt(max(var, MIN_SIGMA_M ** 2))
        state = "found" if sigma < 0.35 and age < 3 else "unsure"
        return {"state": state, "position": [round(float(v), 3) for v in pos], "heading_deg": round(float(self.heading), 1),
                "pitch_deg": round(float(self.pitch), 1), "sigma_m": round(sigma, 3), "radius95_m": round(2.45 * sigma, 2),
                "heading_sd_deg": round(float(self.heading_sd), 1), "speed_m_s": round(float(np.linalg.norm(self.x[3:5])), 2),
                "age_s": round(age, 2), "held": self.held, "trail": self.trail}


# ------------------------------------------------------------------ the relay's side
FOCAL_TRIES = (1.0, 0.8, 0.9, 1.12, 1.25)  # the live camera's focal length against the recording's, until one is settled
FOCAL_PHOTOS = 4  # placed photos looked at before the focal length is settled
PRIOR_S = 3.0  # a fix this recent guides the choice of keyframes for the next photo


def chance_within(sigma, metres=0.5):
    """The chance that the truth lies within `metres` of the pose, for a round 2D Gaussian of this sigma."""
    return 1.0 - math.exp(-metres * metres / (2 * max(sigma, 1e-6) ** 2))


class Live:
    """What the relay keeps: one SplatIndex per trained splat folder (built on a thread: ~30 s the first time), one
    PoseFilter per zone, the live camera's focal length per zone, and the newest placed photo. Thread-safe. The
    filters are saved to `state_file` so a restart carries on (a filter made for another matrix is dropped)."""

    def __init__(self, state_file=None, log=print):
        self.state_file = Path(state_file) if state_file else None
        self.log = log
        self.indexes = {}  # folder -> SplatIndex, "building" or "failed: ..."
        self.lock = threading.Lock()
        self.locating = threading.Lock()
        self.zones = {}  # zone -> {"key", "filter", "focal", "tries", "prior", "last"}
        try:
            saved = json.loads(self.state_file.read_text()) if self.state_file else {}
            for zone, z in saved.items():
                self.zones[zone] = {"key": z.get("key"), "filter": PoseFilter(z.get("filter")), "focal": z.get("focal"),
                                    "tries": {}, "prior": None, "last": None}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        if not self.state_file:
            return
        try:
            self.state_file.write_text(json.dumps({zone: {"key": z["key"], "filter": z["filter"].dump(), "focal": z["focal"]}
                                                   for zone, z in self.zones.items()}))
        except OSError:
            pass

    def index(self, folder):
        """The folder's SplatIndex, or None while it is being built (the build starts on the first ask)."""
        folder = str(folder)
        with self.lock:
            got = self.indexes.get(folder)
            if isinstance(got, SplatIndex):
                return got
            if got is not None:
                return None
            self.indexes[folder] = "building"

        def build():
            try:
                made = SplatIndex(folder)
                self.log(f"splat pose: {Path(folder).name} ready in {made.build_seconds} s "
                         f"({sum(made.lifted)} points from depth, {made.triangulated} triangulated)")
            except Exception as err:  # noqa: BLE001  the relay goes on with its other fixes
                made = f"failed: {type(err).__name__}: {err}"
                self.log(f"splat pose: {Path(folder).name} {made}")
            with self.lock:
                self.indexes[folder] = made

        threading.Thread(target=build, daemon=True).start()
        return None

    def state(self, folder):
        got = self.indexes.get(str(folder))
        return "ready" if isinstance(got, SplatIndex) else got or "not asked"

    def zone(self, zone, key):
        z = self.zones.get(zone)
        if z is None or z["key"] != key:  # a new trained splat or a new matrix: the old poses are in other axes
            z = self.zones[zone] = {"key": key, "filter": PoseFilter(), "focal": None, "tries": {}, "prior": None, "last": None}
        return z

    def photo(self, zone, folder, matrix, frame, now):
        """Place one photo (BGR) of `zone`, whose trained splat is in `folder` and goes into map axes by `matrix`.
        Returns None while the index is being built, else {"placed": bool, "step": the filter's step, "fix": the
        raw fix in map axes or None, "why", "seconds", "keyframe", ...}."""
        index = self.index(folder)
        if index is None:
            return None
        key = json.dumps([str(folder), matrix])
        with self.locating:
            z = self.zone(zone, key)
            prior = z["prior"] if z["prior"] and now - z["prior"]["at"] <= PRIOR_S else None
            scales = (z["focal"],) if z["focal"] else FOCAL_TRIES
            got = index.locate(frame, prior, focal_scale=scales)
            out = {"placed": bool(got["ok"]), "why": got.get("why"), "seconds": got.get("seconds"), "matches": got.get("matches"),
                   "inliers": got.get("inliers"), "keyframe": got.get("keyframe"), "focal_scale": got.get("focal_scale"),
                   "focal": "settled" if z["focal"] else "trying"}
            if got["ok"]:
                if not z["focal"]:  # count the inliers each focal length gets, and settle on the best after a few photos
                    for s, n in (got.get("focal_tries") or {}).items():
                        z["tries"][s] = z["tries"].get(s, 0) + n
                    z["tries"]["photos"] = z["tries"].get("photos", 0) + 1
                    if z["tries"]["photos"] >= FOCAL_PHOTOS:
                        counts = {s: n for s, n in z["tries"].items() if s != "photos"}
                        best = max(counts, key=counts.get)
                        z["focal"] = 1.0 if counts.get(1.0, 0) >= 0.9 * counts[best] else float(best)
                        self.log(f"splat pose: {zone} camera focal length x{z['focal']} (inliers by scale: {counts})")
                fix = to_map(got, matrix)
                fix["turn_sd_deg"] = got.get("turn_sd_deg")
                out["step"] = z["filter"].update(fix, now)
                out["fix"] = {"position": [round(float(v), 3) for v in fix["position"]], "heading_deg": round(fix["heading_deg"], 1),
                              "pitch_deg": round(fix["pitch_deg"], 1), "sigma_m": round(fix["sigma_m"], 3)}
                z["prior"] = {"centre": got["centre"], "forward": got["forward"], "at": now}
            else:
                out["step"] = z["filter"].update(None, now)
                out["fix"] = None
            z["last"] = {**out, "at": now}
            self.save()
            return out

    def pose(self, zone, now):
        """The zone's steady pose (PoseFilter.current) with the newest photo's facts, or None when lost or unknown."""
        z = self.zones.get(zone)
        if not z:
            return None
        cur = z["filter"].current(now)
        if cur["state"] == "lost":
            return None
        return {**cur, "last_photo": z["last"], "focal": z["focal"]}
