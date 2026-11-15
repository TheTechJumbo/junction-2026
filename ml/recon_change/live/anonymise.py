#!/usr/bin/env python3
"""Blur faces and people in a picture before it leaves this laptop (for the relay, see relay.py).

The relay sends phone and glasses frames to Iridis (room maps, splats, item labels). Anyone the
camera caught would go with them. This module finds faces and people on the CPU, blurs them, and
re-encodes the picture from its pixels, so no metadata goes along (no EXIF, GPS, maker notes,
thumbnails, XMP, ICC profile or comments; the only header is the plain JFIF one).

    from anonymise import anonymise_jpeg, anonymise_zip, anonymise_image, anonymise_many
    clean, report = anonymise_jpeg(photo)            # report = {"faces": 1, "people": 0, "ms": 112.4}
    bundle, report = anonymise_zip(zip_of_frames)    # every .jpg/.jpeg/.png inside, names and order kept
    pictures = anonymise_many([jpeg, jpeg, ...], workers=4)   # [(clean, report), ...] in the same order

    python live/anonymise.py IN OUT          # a photo, a zip of frames, or a folder of them
    python live/anonymise.py --check FOLDER  # faces and people found in each frame; nothing is written

How (numpy and OpenCV only, nothing downloaded, no GPU):
  faces   YuNet, a small learned face detector, through OpenCV's FaceDetectorYN, when its model is in
          this repository: the backend already keeps it (backend/privacy/models, 230 KB, checked here
          against the same SHA-256). It finds faces down to about 20 px across in a 960 px frame,
          turned and tilted ones too. Besides it, OpenCV's bundled Haar cascades: frontalface_default,
          frontalface_alt2, and profileface on the picture and on its mirror (it only knows faces
          turned one way), each on a small grey copy. With YuNet they are a second opinion on faces
          above about 90 px; without it they run on larger copies and are all there is.
          ANONYMISE_YUNET=0 turns YuNet off, ANONYMISE_YUNET=/path/model.onnx uses another copy.
  people  OpenCV's HOG people detector, on a 400 px copy: bodies more than about 40% of the frame high.
  Detections only ever add blur. Every face box is grown by 40% of its size on each side (hair, ears,
  chin), cut to about 8 x 8 blocks and smoothed with a Gaussian: what is left is a blob of a few
  colours that cannot be turned back into the face (strength="solid": one flat colour instead).
  Every person box is grown by 15% and cut into blocks a third of a head high, then smoothed.
  Overlapping boxes are merged. The settings lean towards blurring too much; a frame with nobody in
  it comes back unchanged but for the JPEG re-encoding (above 40 dB PSNR in the tests).

Speed, measured on this laptop (i5-10300H, 4 cores) while three of its eight threads were busy with
other programs, for a 960 x 720 JPEG including decoding and encoding: about 140 to 270 ms one at a
time (median about 175 ms) and 85 to 110 ms a frame with anonymise_many(workers=4), so some 9 to 11
frames a second. That is short of a 15 fps stream. ANONYMISE_CASCADES=0 (YuNet and HOG only, which
with YuNet there loses little) gives about 95 to 135 ms one at a time and 47 to 65 ms a frame with
4 workers (15 to 20 frames a second). Without YuNet: about 240 ms, and 160 ms a frame with 4 workers.
The first frame also loads the detectors (about half a second; warm_up() does it beforehand).

Honest limits. This lowers the chance that someone can be recognised; it is not a guarantee.
  - Missed faces: below about 20 px across with YuNet (about 50 px with the cascades alone), turned
    well away from the camera, tilted far, very dark, or mostly hidden. A missed face goes out as it is.
  - Missed bodies: HOG knows people standing upright and wholly in view, at least about 40% of the
    frame high. People sitting, lying, seen from behind, cut off by the frame edge or behind furniture
    are found only through their face; back-facing ones not at all. The bundled upper-body cascade is
    left off (UPPER_BODY): it cost as much as YuNet, fired on clutter and missed our one real person.
  - A pixelated face still carries a few dozen colours. They cannot be turned back into a face, but
    against a short list of known people they are not nothing: use strength="solid" where that
    matters. Clothes, the room itself and sound are not anonymised.
  - Blurring on Iridis after the upload would be too late: the picture would already have left the
    laptop. The stronger option is what YuNet starts: learned detectors shipped with the app and run
    here before sending. A learned person detector (for example a small ONNX model) would need a
    model download that Seb approves first; YuNet needed none because it is already in the repo.
  - A picture that cannot be read raises ValueError. Callers must then send nothing (fail closed).
"""
import argparse
import hashlib
import io
import os
import queue
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
QUALITY = 90                 # JPEG quality of what is sent on
FACE_PAD, PERSON_PAD = 0.40, 0.15   # each box grows by this share of its size on every side
FACE_BLOCKS = 8              # a face box is pixelated to about this many blocks along its longer side
BLOCKS_PER_HEAD = 3          # a person box: blocks this many to a head's height
HEAD_SHARE = {"body": 1 / 7.5, "upper": 0.45}  # a head's height as a share of the detector's box

# Detection runs on small copies (long side in pixels), so each detector only sees faces and bodies
# above some size: a cascade's window is 20 to 24 px at its copy's size, HOG's 64 x 128 px. With the
# YuNet model (which finds faces down to about 20 px in a 960 px frame) the cascades are a second
# opinion on the larger faces and run smaller; without it they run larger, at about twice the cost.
SIDES_WITH_YUNET = {"yunet": 512, "frontalface_default": 256, "frontalface_alt2": 200, "profileface": 200,
                    "body": 400, "upperbody": 200}
SIDES_HAAR_ONLY = {"frontalface_default": 512, "frontalface_alt2": 448, "profileface": 384,
                   "body": 400, "upperbody": 200}
STEP = 1.2                   # size step between the scales a cascade tries
BODY_STEP = 1.15             # and HOG
# The bundled upper-body cascade (head and shoulders) costs as much as YuNet, fired on cluttered synthetic
# rooms and missed the one real person we have (the astronaut portrait). Off unless set to True.
UPPER_BODY = False
# With YuNet there, the cascades only add a second opinion on faces above about 90 px, for about 40% of the
# time a frame takes. ANONYMISE_CASCADES=0 skips them then (never without YuNet): for a 15 fps stream.
CASCADES_WITH_YUNET = os.environ.get("ANONYMISE_CASCADES", "1") != "0"
YUNET_SCORE = 0.5            # lower than YuNet's usual 0.6: a missed face is worse than a blurred wall
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"  # as in backend/privacy/worker.py
YUNET_DEFAULT = HERE.parents[2] / "backend" / "privacy" / "models" / "face_detection_yunet_2023mar.onnx"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")  # what anonymise_zip and the command line treat as pictures
MAX_UNZIPPED = 1 << 30
STRENGTHS = ("strong", "solid")


def _yunet_path():
    """The YuNet model to use, or None: off by ANONYMISE_YUNET=0, missing, or not the file we checked."""
    choice = os.environ.get("ANONYMISE_YUNET", "")
    if choice.lower() in ("0", "off", "no", "false"):
        return None
    path = Path(choice) if choice else YUNET_DEFAULT
    if not hasattr(cv2, "FaceDetectorYN") or not path.is_file():
        return None
    if not choice and hashlib.sha256(path.read_bytes()).hexdigest() != YUNET_SHA256:
        sys.stderr.write(f"anonymise: {path.name} is not the checked YuNet model, faces use the Haar cascades only\n")
        return None
    return path


_YUNET = _yunet_path()
SIDES = SIDES_WITH_YUNET if _YUNET else SIDES_HAAR_ONLY
# (cascade, neighbours a face needs, smallest window, also on the mirrored picture). Few neighbours:
# leaning towards a blurred wall rather than a missed face.
FACE_CASCADES = (("frontalface_default", 3, (24, 24), False), ("frontalface_alt2", 2, (20, 20), False),
                 ("profileface", 3, (20, 20), True))  # the profile cascade knows one side; the mirror gives the other


class _Detector:
    """One set of detectors. OpenCV's cascades keep per-image state, so a thread uses a set of its own."""

    def __init__(self, yunet=_YUNET):
        haar = cv2.data.haarcascades
        self.cascade = {name: cv2.CascadeClassifier(haar + f"haarcascade_{name}.xml")
                        for name in ("frontalface_default", "frontalface_alt2", "profileface", "upperbody")}
        missing = [name for name, c in self.cascade.items() if c.empty() and name != "upperbody"]
        if missing:
            raise RuntimeError(f"OpenCV's bundled cascades are missing: {missing}")
        if self.cascade["upperbody"].empty():
            del self.cascade["upperbody"]
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.yunet, self.yunet_size = None, None
        if yunet is not None:
            self.yunet = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320), YUNET_SCORE, 0.3, 5000,
                                                   cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
        self.sides = SIDES_WITH_YUNET if self.yunet is not None else SIDES_HAAR_ONLY

    def faces(self, bgr, grey):
        """Face boxes (x0, y0, x1, y1) in the picture's pixels, before padding."""
        boxes, copies = [], {}
        for name, neighbours, smallest, mirror in FACE_CASCADES if self.yunet is None or CASCADES_WITH_YUNET else ():
            side = self.sides[name]
            if side not in copies:
                copies[side] = _shrunk(grey, side, equalise=True)
            g, s = copies[side]
            if not _fits(g, smallest):
                continue
            for mirrored in (False, True) if mirror else (False,):
                found = _rows(self.cascade[name].detectMultiScale(cv2.flip(g, 1) if mirrored else g, STEP, neighbours, minSize=smallest))
                for x, y, w, h in found:
                    if mirrored:  # back to the unmirrored picture
                        x = g.shape[1] - x - w
                    boxes.append((x / s, y / s, (x + w) / s, (y + h) / s))
        if self.yunet is not None:
            boxes += self._yunet(bgr)
        return boxes

    def _yunet(self, bgr):
        small, s = _shrunk(bgr, self.sides["yunet"])
        h, w = small.shape[:2]
        if min(h, w) < 64:  # tiny inputs give the model's coarsest grid nothing to work on (see backend/privacy/worker.py)
            small = cv2.copyMakeBorder(small, 0, max(0, 64 - h), 0, max(0, 64 - w), cv2.BORDER_CONSTANT)
        if self.yunet_size != small.shape[:2]:
            self.yunet.setInputSize((small.shape[1], small.shape[0]))
            self.yunet_size = small.shape[:2]
        _, found = self.yunet.detect(small)
        boxes = []
        for row in (found if found is not None else []):
            x, y, bw, bh = (float(v) for v in row[:4])
            if np.isfinite(row[:4]).all() and bw > 0 and bh > 0:
                boxes.append((x / s, y / s, (x + bw) / s, (y + bh) / s))
        return boxes

    def people(self, bgr, grey):
        """Person boxes (x0, y0, x1, y1, head height) in the picture's pixels, before padding."""
        boxes = []
        small, s = _shrunk(bgr, self.sides["body"])
        if _fits(small, (64 + 16, 128 + 16)):  # HOG crashes the process on a picture smaller than its window
            found, _ = self.hog.detectMultiScale(small, hitThreshold=0, winStride=(8, 8), padding=(8, 8), scale=BODY_STEP)
            for x, y, w, h in _rows(found):
                boxes.append((x / s, y / s, (x + w) / s, (y + h) / s, HEAD_SHARE["body"] * h / s))
        if not UPPER_BODY or "upperbody" not in self.cascade:
            return boxes
        g, s = _shrunk(grey, self.sides["upperbody"], equalise=True)
        if _fits(g, (22, 18)):
            for x, y, w, h in _rows(self.cascade["upperbody"].detectMultiScale(g, 1.15, 4, minSize=(22, 18))):
                boxes.append((x / s, y / s, (x + w) / s, (y + h) / s, HEAD_SHARE["upper"] * h / s))
        return boxes


_spare = queue.SimpleQueue()  # detector sets not in use, shared by every thread


def describe():
    """Which detectors run, in words."""
    faces = ("YuNet + Haar cascades" if CASCADES_WITH_YUNET else "YuNet (cascades off)") if _YUNET else "Haar cascades only (no YuNet model)"
    return f"faces: {faces}; people: HOG{' + upper-body cascade' if UPPER_BODY else ''}"


def warm_up(sets=1):
    """Load `sets` detector sets now (about half a second each), so the first frames do not wait for it."""
    for _ in range(sets):
        _spare.put(_Detector())


def _fits(picture, size):
    """Is the picture at least size = (width, height)? Smaller ones are not given to a detector at all."""
    return picture.shape[1] >= size[0] and picture.shape[0] >= size[1]


def _rows(found):
    return [] if found is None or len(found) == 0 else [tuple(int(v) for v in row) for row in np.asarray(found).reshape(-1, 4)]


def _shrunk(picture, side, equalise=False):
    """The picture with its long side at most `side` pixels, and the scale used."""
    h, w = picture.shape[:2]
    s = min(1.0, side / max(h, w))
    if s < 1:
        picture = cv2.resize(picture, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    if equalise:
        picture = cv2.equalizeHist(picture)
    return picture, s


def _padded(box, pad, shape):
    x0, y0, x1, y1 = box[:4]
    dx, dy = pad * (x1 - x0), pad * (y1 - y0)
    h, w = shape[:2]
    return [max(0, int(x0 - dx)), max(0, int(y0 - dy)), min(w, int(np.ceil(x1 + dx))), min(h, int(np.ceil(y1 + dy))), *box[4:]]


def _merge(boxes):
    """Boxes that overlap become the one box around them (repeated until none overlap). A fifth value
    (a person's head height) is kept as the larger of the two, so a merged box is blurred no less."""
    boxes = [list(b) for b in boxes if b[2] > b[0] and b[3] > b[1]]
    changed = True
    while changed:
        changed, out = False, []
        for b in boxes:
            for o in out:
                if b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3]:
                    o[:4] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    if len(o) > 4:
                        o[4] = max(o[4], b[4])
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def _pixelate(region, block):
    """The region as blocks of about `block` pixels (their mean colours are all that is kept), smoothed by a
    Gaussian half a block wide so the edges do not show. The smoothing runs at 8 px a block, then the result
    is stretched to the region: the same picture as blurring at full size, at a fraction of the cost."""
    h, w = region.shape[:2]
    block = max(2.0, float(block))
    cols, rows = max(1, round(w / block)), max(1, round(h / block))
    blocks = cv2.resize(region, (cols, rows), interpolation=cv2.INTER_AREA)
    soft = cv2.GaussianBlur(cv2.resize(blocks, (cols * 8, rows * 8), interpolation=cv2.INTER_NEAREST), (0, 0), 4)
    return cv2.resize(soft, (w, h), interpolation=cv2.INTER_LINEAR)


def _upright_bgr(picture):
    """A BGR uint8 copy of a BGR, BGRA or grey picture."""
    shape_ok = isinstance(picture, np.ndarray) and (picture.ndim == 2 or (picture.ndim == 3 and picture.shape[2] in (1, 3, 4)))
    if not shape_ok or picture.dtype != np.uint8 or picture.size == 0:
        raise ValueError("not a picture (a BGR uint8 array is expected)")
    if picture.ndim == 2 or picture.shape[2] == 1:
        return cv2.cvtColor(picture.reshape(picture.shape[:2]), cv2.COLOR_GRAY2BGR)
    return picture[:, :, :3].copy()


def detect(bgr, *, faces=True, people=True):
    """Where the faces and people are: ([[x0, y0, x1, y1], ...], [[x0, y0, x1, y1, head height], ...]) in the
    picture's pixels, already grown by FACE_PAD and PERSON_PAD and merged where they overlap."""
    bgr = bgr if isinstance(bgr, np.ndarray) and bgr.ndim == 3 and bgr.shape[2] == 3 and bgr.dtype == np.uint8 else _upright_bgr(bgr)
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    try:
        detector = _spare.get_nowait()
    except queue.Empty:
        detector = _Detector()
    try:
        face_boxes = _merge(_padded(b, FACE_PAD, bgr.shape) for b in detector.faces(bgr, grey)) if faces else []
        person_boxes = _merge(_padded(b, PERSON_PAD, bgr.shape) for b in detector.people(bgr, grey)) if people else []
    finally:
        _spare.put(detector)
    return face_boxes, person_boxes


def blur(bgr, face_boxes=(), person_boxes=(), strength="strong"):
    """Blur those boxes in place: bodies first, then faces over them at full strength."""
    if strength not in STRENGTHS:
        raise ValueError(f"strength is one of {STRENGTHS}")
    for x0, y0, x1, y1, head in person_boxes:
        bgr[y0:y1, x0:x1] = _pixelate(bgr[y0:y1, x0:x1], max(4.0, head / BLOCKS_PER_HEAD))
    for x0, y0, x1, y1 in face_boxes:
        region = bgr[y0:y1, x0:x1]
        if strength == "solid":
            region[:] = np.asarray(cv2.mean(region)[:3]).round().astype(np.uint8)
        else:
            region[:] = _pixelate(region, max(x1 - x0, y1 - y0) / FACE_BLOCKS)
    return bgr


def anonymise_image(bgr, *, faces=True, people=True, strength="strong"):
    """A copy of the BGR picture with faces and people blurred, and {"faces": n, "people": n, "ms": ...}.
    The picture passed in is not changed."""
    if strength not in STRENGTHS:
        raise ValueError(f"strength is one of {STRENGTHS}")
    t0 = time.perf_counter()
    out = _upright_bgr(bgr)
    face_boxes, person_boxes = detect(out, faces=faces, people=people)
    blur(out, face_boxes, person_boxes, strength)
    return out, {"faces": len(face_boxes), "people": len(person_boxes), "ms": round(1000 * (time.perf_counter() - t0), 1)}


def _decode(data):
    if not isinstance(data, (bytes, bytearray, memoryview)) or not len(data):
        raise ValueError("not a picture")
    picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)  # turned upright by its EXIF orientation
    if picture is None:
        raise ValueError("not a picture")
    return picture


def _encode(picture, png=False):
    ok, data = cv2.imencode(".png", picture) if png else cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
    if not ok:
        raise ValueError("could not encode the picture")
    return data.tobytes()  # a fresh file from pixels: no EXIF, XMP, ICC profile or comment goes with it


def anonymise_jpeg(data, *, faces=True, people=True, strength="strong", png=False):
    """Any picture OpenCV reads (JPEG, PNG, WebP) -> a clean JPEG at quality 90 (a PNG with png=True) and
    {"faces": n, "people": n, "ms": ...}. Raises ValueError on something that is not a picture."""
    t0 = time.perf_counter()
    picture, report = anonymise_image(_decode(data), faces=faces, people=people, strength=strength)
    clean = _encode(picture, png)
    return clean, {**report, "ms": round(1000 * (time.perf_counter() - t0), 1)}


def anonymise_many(items, workers=4, **options):
    """anonymise_jpeg on each picture, `workers` at a time, in the order given: [(bytes, report), ...]."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [anonymise_jpeg(item, **options) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(lambda item: anonymise_jpeg(item, **options), items))


def anonymise_zip(data, *, faces=True, people=True, strength="strong", workers=4):
    """Every .jpg/.jpeg/.png in the zip anonymised, everything else as it was, same names and order.
    Report: {"faces", "people", "ms", "frames", "frames_with_someone"} for the whole zip."""
    t0 = time.perf_counter()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            if sum(info.file_size for info in source.infolist()) > MAX_UNZIPPED:
                raise ValueError(f"more than {MAX_UNZIPPED >> 20} MB unzipped")
            entries = [(info, source.read(info)) for info in source.infolist()]
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as err:  # RuntimeError: an encrypted entry
        raise ValueError(f"not a zip of frames: {err}") from None
    pictures = [k for k, (info, _) in enumerate(entries) if info.filename.lower().endswith(IMAGE_SUFFIXES) and not info.is_dir()]

    def one(k):
        info, raw = entries[k]
        try:
            return anonymise_jpeg(raw, faces=faces, people=people, strength=strength, png=info.filename.lower().endswith(".png"))
        except ValueError:
            raise ValueError(f"{info.filename}: not a picture") from None

    if workers > 1 and len(pictures) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(pictures))) as pool:
            done = dict(zip(pictures, pool.map(one, pictures), strict=True))
    else:
        done = {k: one(k) for k in pictures}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for k, (info, raw) in enumerate(entries):
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.compress_type, entry.external_attr = info.compress_type, info.external_attr
            bundle.writestr(entry, done[k][0] if k in done else raw)
    reports = [report for _, report in done.values()]
    return out.getvalue(), {"faces": sum(r["faces"] for r in reports), "people": sum(r["people"] for r in reports),
                            "ms": round(1000 * (time.perf_counter() - t0), 1), "frames": len(reports),
                            "frames_with_someone": sum(1 for r in reports if r["faces"] or r["people"])}


def _files(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES + (".zip",))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Blur faces and people in pictures before they leave this laptop.")
    ap.add_argument("paths", nargs="*", metavar="IN OUT", help="a picture, a zip of frames or a folder, and where to write the result")
    ap.add_argument("--check", metavar="FOLDER", help="print the faces and people found in each frame, write nothing")
    ap.add_argument("--solid", action="store_true", help="fill faces with one flat colour instead of pixelating them")
    ap.add_argument("--no-people", action="store_true", help="faces only")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    options = {"strength": "solid" if args.solid else "strong", "people": not args.no_people}
    print(describe(), file=sys.stderr)
    if args.check:
        root = Path(args.check)
        files = [root] if root.is_file() else _files(root)
        frames, total_ms = [], 0.0
        for path in files:
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as bundle:
                    items = [(f"{path.name}:{n}", bundle.read(n)) for n in bundle.namelist() if n.lower().endswith(IMAGE_SUFFIXES)]
            else:
                items = [(str(path.relative_to(root)) if root.is_dir() else path.name, path.read_bytes())]
            for name, data in items:
                _, report = anonymise_jpeg(data, **options)
                frames.append(report)
                total_ms += report["ms"]
                print(f"{name:48s} faces {report['faces']:2d}  people {report['people']:2d}  {report['ms']:7.1f} ms")
        if frames:
            print(f"{len(frames)} frames: {sum(r['faces'] for r in frames)} faces, {sum(r['people'] for r in frames)} people, "
                  f"{sum(1 for r in frames if r['faces'] or r['people'])} frames with someone, {total_ms / len(frames):.1f} ms a frame")
        return 0
    if len(args.paths) != 2:
        ap.error("give IN and OUT, or --check FOLDER")
    src, dst = Path(args.paths[0]), Path(args.paths[1])
    pairs = [(src, dst)] if src.is_file() else [(p, dst / p.relative_to(src)) for p in _files(src)]
    if not pairs:
        ap.error(f"no pictures or zips in {src}")
    t0, faces, people, frames = time.perf_counter(), 0, 0, 0
    for path, target in pairs:
        if path.suffix.lower() == ".zip":
            clean, report = anonymise_zip(path.read_bytes(), workers=args.workers, **options)
        else:
            clean, report = anonymise_jpeg(path.read_bytes(), png=target.suffix.lower() == ".png", **options)
            report["frames"] = 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(clean)
        faces, people, frames = faces + report["faces"], people + report["people"], frames + report["frames"]
        print(f"{path.name:40s} -> {target}  faces {report['faces']}  people {report['people']}  {report['ms']:.0f} ms")
    seconds = time.perf_counter() - t0
    print(f"{frames} frames, {faces} faces, {people} people blurred in {seconds:.1f} s "
          f"({1000 * seconds / max(1, frames):.0f} ms a frame)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
