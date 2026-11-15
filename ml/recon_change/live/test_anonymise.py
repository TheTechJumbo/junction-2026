#!/usr/bin/env python3
"""Offline test of the anonymiser (anonymise.py): faces and people are blurred, the rest of the picture is
left alone, metadata is gone, zips keep their names and order, and a frame takes well under a second.
Needs OpenCV; no network, no cluster. The pictures are drawn here; the one real face is the public-domain
astronaut portrait the backend's privacy tests already keep (skipped when that file is not there).

    python live/test_anonymise.py
"""
import contextlib
import io
import struct
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import anonymise  # noqa: E402

ASTRONAUT = Path(__file__).resolve().parents[3] / "backend" / "tests" / "privacy" / "fixtures" / "astronaut.png"
ASTRONAUT_FACE = (175, 65, 270, 170)  # x0, y0, x1, y1 of the face in that 512 x 512 picture (its README)
TARGET_MS = 150  # what a frame should take on the laptop; the test fails only well above it (a busy laptop is slower)
LIMIT_MS = 500  # fails only on something badly wrong (a busy laptop already doubles the time)


def room(seed, w=960, h=720):
    """A made-up room with nobody in it: blobs and boxes of colour on a gradient, a little sensor noise."""
    rng = np.random.default_rng(seed)
    picture = cv2.cvtColor(np.tile(np.linspace(70, 190, w, dtype=np.float32), (h, 1)).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    for _ in range(40):
        x, y, r = int(rng.integers(0, w)), int(rng.integers(0, h)), int(rng.integers(15, 70))
        colour = tuple(int(c) for c in rng.integers(30, 225, 3))
        if rng.random() < 0.5:
            cv2.circle(picture, (x, y), r, colour, -1)
        else:
            cv2.rectangle(picture, (x, y), (x + 2 * r, y + r), colour, -1)
    return np.clip(picture + rng.normal(0, 2, picture.shape), 0, 255).astype(np.uint8)


def drawn_face(size):
    """A cartoon face OpenCV's frontal face cascades take for a face: skin oval, hair, brows, eyes, nose, mouth."""
    s = size
    picture = np.full((int(s * 1.6), int(s * 1.4), 3), (60, 70, 80), np.uint8)
    cx, cy = picture.shape[1] // 2, picture.shape[0] // 2
    cv2.ellipse(picture, (cx, cy), (int(s * .42), int(s * .55)), 0, 0, 360, (150, 175, 215), -1)
    cv2.ellipse(picture, (cx, cy - int(s * .38)), (int(s * .44), int(s * .25)), 0, 180, 360, (30, 30, 40), -1)
    eyes = cy - int(s * .08)
    for side in (-1, 1):
        x = cx + side * int(s * .17)
        cv2.ellipse(picture, (x, eyes - int(s * .09)), (int(s * .11), int(s * .025)), 0, 0, 360, (40, 45, 60), -1)
        cv2.ellipse(picture, (x, eyes), (int(s * .09), int(s * .045)), 0, 0, 360, (235, 235, 235), -1)
        cv2.circle(picture, (x, eyes), int(s * .04), (40, 30, 30), -1)
    cv2.ellipse(picture, (cx, cy + int(s * .12)), (int(s * .06), int(s * .03)), 0, 0, 360, (110, 130, 170), -1)
    cv2.ellipse(picture, (cx, cy + int(s * .28)), (int(s * .15), int(s * .04)), 0, 0, 360, (70, 70, 150), -1)
    return cv2.GaussianBlur(picture, (0, 0), s / 60)


def jpeg(picture, quality=90):
    return cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def decode(data):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def detail(region):
    """How much fine detail a region has (variance of its Laplacian)."""
    return float(cv2.Laplacian(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def changed(a, b):
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def outside(picture, box, margin=2):
    """The picture's pixels more than `margin` px outside the box, as one flat array."""
    mask = np.ones(picture.shape[:2], bool)
    x0, y0, x1, y1 = box[:4]
    mask[max(0, y0 - margin):y1 + margin, max(0, x0 - margin):x1 + margin] = False
    return picture[mask]


def with_metadata(data, orientation=None):
    """The JPEG with an EXIF APP1 segment (camera make, a description with a place, maybe an orientation)
    and a comment segment put in right after its start marker."""
    texts = {0x010E: b"Home of A. Person, 51.5074N 0.1278W\x00", 0x010F: b"SecretCam\x00"}
    tags = sorted(list(texts) + ([0x0112] if orientation else []))
    blob, offset, entries = b"", 8 + 2 + 12 * len(tags) + 4, b""
    for tag in tags:
        if tag == 0x0112:
            entries += struct.pack("<HHIHH", tag, 3, 1, orientation, 0)
        else:
            entries += struct.pack("<HHII", tag, 2, len(texts[tag]), offset + len(blob))
            blob += texts[tag]
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(tags)) + entries + struct.pack("<I", 0) + blob
    app1 = b"Exif\x00\x00" + tiff
    comment = b"recorded at 12 Example Road"
    return (data[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment + data[2:])


def segments(data):
    """(marker, payload) of each JPEG segment before the picture data."""
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    found, i = [], 2
    while i + 4 <= len(data):
        assert data[i] == 0xFF, f"no marker at {i}"
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        found.append((marker, data[i + 4:i + 2 + length]))
        if marker == 0xDA:  # start of scan: the picture data follows
            break
        i += 2 + length
    return found


def png_with_text(picture):
    data = cv2.imencode(".png", picture)[1].tobytes()
    body = b"Comment\x00filmed in the bedroom of A. Person"
    chunk = struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body))
    return data[:33] + chunk + data[33:]  # after the signature (8) and the IHDR chunk (25)


def png_chunks(data):
    i, names = 8, []
    while i < len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        names.append(data[i + 4:i + 8])
        i += 12 + length
    return names


@contextlib.contextmanager
def detectors_say(faces=(), people=()):
    """Every detector set reports exactly these boxes (raw, before padding)."""
    saved = anonymise._Detector.faces, anonymise._Detector.people
    anonymise._Detector.faces = lambda self, bgr, grey: list(faces)
    anonymise._Detector.people = lambda self, bgr, grey: list(people)
    try:
        yield
    finally:
        anonymise._Detector.faces, anonymise._Detector.people = saved


def haar_only():
    """A detector set without YuNet: the cascades at the sizes they use when the model is not there."""
    return anonymise._Detector(yunet=None)


def check_blurring():
    """With known boxes: the face box (grown by 40%) loses its detail, a person box too, nothing else changes."""
    frame = room(1)
    rng = np.random.default_rng(5)
    frame[200:320, 300:400] = rng.integers(0, 255, (120, 100, 3), dtype=np.uint8)   # a "face" full of detail
    frame[300:700, 600:800] = rng.integers(0, 255, (400, 200, 3), dtype=np.uint8)   # a "person"
    before = frame.copy()
    with detectors_say(faces=[(300, 200, 400, 320)], people=[(600, 300, 800, 700, 400 / 7.5)]):
        out, report = anonymise.anonymise_image(frame)
        solid, _ = anonymise.anonymise_image(frame, strength="solid")
        face_boxes, person_boxes = anonymise.detect(frame)
    assert np.array_equal(frame, before), "the picture passed in was changed"
    assert report["faces"] == 1 and report["people"] == 1 and report["ms"] > 0, report
    assert face_boxes == [[260, 152, 440, 368]], face_boxes  # 40% of 100 x 120 on every side
    assert person_boxes[0][:4] == [570, 240, 830, 720], person_boxes  # 15% of 200 x 400, cut at the frame's edge
    face = (slice(200, 320), slice(300, 400))
    assert changed(out[face], before[face]) > 40, changed(out[face], before[face])
    assert detail(out[face]) < 0.01 * detail(before[face]), (detail(out[face]), detail(before[face]))
    assert len(np.unique(solid[face].reshape(-1, 3), axis=0)) == 1, "solid: one colour"
    body = (slice(300, 700), slice(600, 800))
    assert changed(out[body], before[body]) > 30 and detail(out[body]) < 0.05 * detail(before[body]), detail(out[body])
    for box in face_boxes:  # in the grown box, outside the detector's box, it is blurred too
        band = out[box[1]:200, box[0]:box[2]]
        assert changed(band, before[box[1]:200, box[0]:box[2]]) > 2, "the padding was not blurred"
    rest = np.ones(frame.shape[:2], bool)
    for x0, y0, x1, y1, *_ in face_boxes + person_boxes:
        rest[y0:y1, x0:x1] = False
    assert np.array_equal(out[rest], before[rest]), "pixels away from the boxes changed"
    # The same through JPEG: away from the boxes, only the re-encoding differs.
    plain = room(1)
    plain[face] = before[face]  # the detailed "face" alone: random pixels elsewhere would not survive any re-encoding
    data = jpeg(plain)
    with detectors_say(faces=[(300, 200, 400, 320)]):
        clean, report = anonymise.anonymise_jpeg(data)
    got, sent = decode(clean), decode(data)
    psnr = cv2.PSNR(outside(got, face_boxes[0], 4).reshape(-1, 1, 3), outside(sent, face_boxes[0], 4).reshape(-1, 1, 3))
    assert report["faces"] == 1 and psnr > 40, psnr
    assert detail(got[face]) < 0.02 * detail(sent[face])
    # Overlapping boxes become one; separate ones stay separate; nothing to blur: no change at all.
    with detectors_say(faces=[(100, 100, 200, 200), (180, 150, 260, 260), (700, 50, 760, 110)]):
        assert anonymise.anonymise_image(frame)[1]["faces"] == 2
    with detectors_say():
        out, report = anonymise.anonymise_image(frame)
    assert report == {**report, "faces": 0, "people": 0} and np.array_equal(out, frame)
    print(f"blurring: face box detail {detail(got[face]) / detail(sent[face]):.4f} of before, rest of the frame "
          f"{psnr:.1f} dB after JPEG, overlapping boxes merged")


def check_detection():
    """The cascades find a drawn face (also without YuNet), and the profile pass maps mirrored boxes back."""
    frame = room(2)
    face = drawn_face(160)
    frame[150:150 + face.shape[0], 380:380 + face.shape[1]] = face
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    head = (380 + 30, 150 + 60, 380 + 195, 150 + 200)  # around the drawn oval
    raw = haar_only().faces(frame, grey)
    assert raw, "the Haar cascades did not find the drawn face"
    face_boxes, _ = anonymise.detect(frame)
    assert any(b[0] <= head[0] and b[1] <= head[1] and b[2] >= head[2] and b[3] >= head[3] for b in face_boxes), (face_boxes, head)
    out, report = anonymise.anonymise_image(frame)
    region = (slice(head[1], head[3]), slice(head[0], head[2]))
    assert report["faces"] >= 1 and changed(out[region], frame[region]) > 15, (report, changed(out[region], frame[region]))
    assert not haar_only().faces(out, cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)), "a face is still found after blurring"
    # The profile cascade only knows faces turned one way: it also runs on the mirror, and those boxes come back.

    class Stub:
        def __init__(self, box):
            self.box = box

        def detectMultiScale(self, picture, *args, **kwargs):
            return np.array([self.box]) if self.box else ()

    detector = haar_only()
    detector.cascade = {"frontalface_default": Stub(None), "frontalface_alt2": Stub(None), "profileface": Stub((10, 20, 30, 30))}
    side = detector.sides["profileface"]
    scale = side / 960
    boxes = sorted(detector.faces(frame, grey))
    width = round(960 * scale)
    expected = sorted([(10 / scale, 20 / scale, 40 / scale, 50 / scale),
                       ((width - 40) / scale, 20 / scale, (width - 10) / scale, 50 / scale)])
    assert np.allclose(boxes, expected), (boxes, expected)
    print(f"detection: drawn face found by the Haar cascades alone ({len(raw)} raw boxes) and blurred; "
          f"mirrored profile boxes land on the right side")


def check_real_face():
    """The public-domain astronaut portrait in a room, large and small: the face is found and blurred."""
    if not ASTRONAUT.is_file():
        print(f"real face: skipped ({ASTRONAUT} is not there)")
        return
    portrait = cv2.imread(str(ASTRONAUT), cv2.IMREAD_COLOR)
    # The portrait's face is about a fifth of its width: 74, 48 and 24 px across in the 960 px frame. The cascades
    # alone are only sure of the large one (48 px is at their limit and comes and goes with JPEG noise).
    sizes = (400,) if anonymise._YUNET is None else (400, 256, 128)
    for size in sizes:
        for mirrored in (False, True):
            frame = room(3 + size)
            x, y = 300, 100
            frame[y:y + size, x:x + size] = cv2.resize(cv2.flip(portrait, 1) if mirrored else portrait, (size, size),
                                                       interpolation=cv2.INTER_AREA)
            k = size / 512
            fx0, fy0, fx1, fy1 = ASTRONAUT_FACE
            if mirrored:
                fx0, fx1 = 512 - fx1, 512 - fx0
            face = (int(x + fx0 * k), int(y + fy0 * k), int(np.ceil(x + fx1 * k)), int(np.ceil(y + fy1 * k)))
            sent = jpeg(frame)
            face_boxes, _ = anonymise.detect(decode(sent))
            assert any(b[0] <= face[0] and b[1] <= face[1] and b[2] >= face[2] and b[3] >= face[3] for b in face_boxes), \
                (size, mirrored, face_boxes, face)
            clean, report = anonymise.anonymise_jpeg(sent)
            assert report["faces"] >= 1, report
            region = (slice(face[1], face[3]), slice(face[0], face[2]))
            assert detail(decode(clean)[region]) < 0.05 * detail(frame[region]), (size, detail(decode(clean)[region]))
    detector = haar_only()  # the cascades alone, on the big one
    frame = room(9)
    frame[100:500, 300:700] = cv2.resize(portrait, (400, 400), interpolation=cv2.INTER_AREA)
    assert detector.faces(frame, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)), "the Haar cascades missed the astronaut"
    print(f"real face: astronaut found and blurred at {', '.join(f'{s} px' for s in sizes)} (and mirrored); "
          f"the Haar cascades alone find the big one")


def check_metadata():
    """EXIF (with a place and a camera), comments and PNG text do not come out; the EXIF orientation is applied."""
    picture = room(4)[:480, :640]
    marked = with_metadata(jpeg(picture))
    assert any(m == 0xE1 for m, _ in segments(marked)) and decode(marked) is not None, "the test JPEG is wrong"
    clean, _ = anonymise.anonymise_jpeg(marked)
    kept = segments(clean)
    assert not [m for m, _ in kept if 0xE1 <= m <= 0xEF or m == 0xFE], [hex(m) for m, _ in kept]
    assert all(payload.startswith(b"JFIF\x00") for m, payload in kept if m == 0xE0), "only the plain JFIF header"
    for secret in (b"Exif", b"SecretCam", b"51.5074", b"Example Road"):
        assert secret not in clean, secret
    assert cv2.PSNR(decode(clean), decode(marked)) > 40
    # A phone photo held upright says so in EXIF (orientation 6: turn 90 degrees clockwise). The pixels come out turned,
    # so the picture still looks right without its EXIF.
    turned, _ = anonymise.anonymise_jpeg(with_metadata(jpeg(picture), orientation=6))
    assert decode(turned).shape[:2] == (640, 480) and b"Exif" not in turned, decode(turned).shape
    assert cv2.PSNR(decode(turned), cv2.rotate(decode(clean), cv2.ROTATE_90_CLOCKWISE)) > 35
    png, _ = anonymise.anonymise_jpeg(png_with_text(picture), png=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and not {b"tEXt", b"iTXt", b"zTXt", b"eXIf"} & set(png_chunks(png)), png_chunks(png)
    print("metadata: EXIF, comment and PNG text removed; EXIF orientation applied to the pixels")


def check_zip():
    """Every picture in a zip is done, other files are kept byte for byte, names and order stay."""
    names = ["000123_1700000000000.jpg", "000124_1700000000066.JPEG", "notes.txt", "depth/000125.png", "000122_1699999999933.jpg"]
    pictures = {n: room(10 + i)[:360, :480] for i, n in enumerate(names)}
    sent = {n: png_with_text(p) if n.endswith(".png") else with_metadata(jpeg(p)) for n, p in pictures.items()}
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w", zipfile.ZIP_STORED) as bundle:
        for n in names:
            if n.endswith(".txt"):
                bundle.writestr(n, b"not a picture: kept as it is")
            elif n.endswith(".png"):
                bundle.writestr(n, sent[n])
            else:
                bundle.writestr(zipfile.ZipInfo(n, (2026, 11, 15, 12, 0, 0)), sent[n], zipfile.ZIP_DEFLATED)
    clean, report = anonymise.anonymise_zip(source.getvalue())
    with zipfile.ZipFile(io.BytesIO(clean)) as bundle:
        assert bundle.namelist() == names, bundle.namelist()
        assert bundle.read("notes.txt") == b"not a picture: kept as it is"
        assert [i.compress_type for i in bundle.infolist()] == [zipfile.ZIP_DEFLATED] * 2 + [zipfile.ZIP_STORED] * 2 + [zipfile.ZIP_DEFLATED]
        for n in names:
            if n.endswith(".txt"):
                continue
            data = bundle.read(n)
            assert (data[:4] == b"\x89PNG") == n.endswith(".png") and b"Exif" not in data and b"tEXt" not in data, n
            assert cv2.PSNR(decode(data), decode(sent[n])) > 40, (n, cv2.PSNR(decode(data), decode(sent[n])))
    assert report["frames"] == 4 and report["faces"] == 0 and report["people"] == 0, report
    for broken in (b"PK\x03\x04 not really", b""):
        try:
            anonymise.anonymise_zip(broken)
            raise AssertionError("a broken zip went through")
        except ValueError:
            pass
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr("000.jpg", jpeg(pictures[names[0]]))
        bundle.writestr("001.jpg", b"\xff\xd8 truncated")
    try:
        anonymise.anonymise_zip(bad.getvalue())
        raise AssertionError("a zip with a picture that cannot be read went through")
    except ValueError as err:
        assert "001.jpg" in str(err), err
    print(f"zip: {report['frames']} pictures done, names, order and the text file kept, broken input refused")


def check_nobody():
    """Frames with nobody in them come back almost as they were, and nothing is reported."""
    worst = 99.0
    for seed in range(20, 26):
        data = jpeg(room(seed))
        clean, report = anonymise.anonymise_jpeg(data)
        assert report["faces"] == 0 and report["people"] == 0, (seed, report)
        worst = min(worst, cv2.PSNR(decode(clean), decode(data)))
    assert worst > 40, worst
    for junk in (b"", b"not a picture", b"\xff\xd8\xff\xe0 truncated"):
        try:
            anonymise.anonymise_jpeg(junk)
            raise AssertionError(f"{junk!r} went through")
        except ValueError:
            pass
    for h, w in ((1, 1), (10, 10), (40, 120), (130, 70), (100, 2000), (3000, 50)):  # no detector may crash on these
        odd = room(31, max(w, 2), max(h, 2))[:h, :w]
        assert anonymise.anonymise_image(odd)[0].shape == odd.shape and anonymise.anonymise_jpeg(jpeg(odd))[0][:2] == b"\xff\xd8"
    grey, _ = anonymise.anonymise_image(cv2.cvtColor(room(30), cv2.COLOR_BGR2GRAY))
    bgra, _ = anonymise.anonymise_image(cv2.cvtColor(room(30), cv2.COLOR_BGR2BGRA))
    assert grey.shape == bgra.shape == (720, 960, 3)
    print(f"nobody: 6 empty frames unchanged but for re-encoding (worst {worst:.1f} dB), junk refused, tiny and odd sizes fine")


def check_speed_and_cli():
    """A 960 x 720 frame with a face, one at a time and four at a time; and the command line."""
    frames = []
    for seed in range(8):
        frame = room(40 + seed)
        face = drawn_face(100 + 10 * seed)
        frame[200:200 + face.shape[0], 100 + 60 * seed:100 + 60 * seed + face.shape[1]] = face
        frames.append(jpeg(frame, 85))
    anonymise.anonymise_jpeg(frames[0])  # the first call loads the detectors
    times = [anonymise.anonymise_jpeg(f)[1]["ms"] for f in frames for _ in range(2)]
    one = float(np.median(times))
    batch = frames * 2
    t0 = time.perf_counter()
    results = anonymise.anonymise_many(batch, workers=4)
    many = 1000 * (time.perf_counter() - t0) / len(batch)
    assert len(results) == len(batch) and all(r["faces"] >= 1 for _, r in results), [r for _, r in results]
    assert [r["faces"] for _, r in results] == [anonymise.anonymise_jpeg(f)[1]["faces"] for f in batch]
    assert one < LIMIT_MS, f"{one:.0f} ms a frame"
    note = "" if one <= TARGET_MS else f" (above the {TARGET_MS} ms target: is the laptop busy?)"
    with tempfile.TemporaryDirectory() as folder:
        src, dst = Path(folder) / "in", Path(folder) / "out"
        (src / "sub").mkdir(parents=True)
        for k, f in enumerate(frames[:3]):
            (src / ("sub" if k == 2 else "") / f"{k:03d}.jpg").write_bytes(with_metadata(f))
        with zipfile.ZipFile(src / "chunk.zip", "w") as bundle:
            bundle.writestr("000000_0.jpg", frames[3])
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            assert anonymise.main([str(src), str(dst)]) == 0
            assert anonymise.main([str(src / "000.jpg"), str(Path(folder) / "one.jpg")]) == 0
            assert anonymise.main(["--check", str(src)]) == 0
        written = sorted(str(p.relative_to(dst)).replace("\\", "/") for p in dst.rglob("*") if p.is_file())
        assert written == ["000.jpg", "001.jpg", "chunk.zip", "sub/002.jpg"], written
        assert all(b"Exif" not in p.read_bytes() for p in dst.rglob("*.jpg")) and (Path(folder) / "one.jpg").is_file()
        lines = printed.getvalue().splitlines()
        assert any(line.startswith("4 frames:") for line in lines), lines
    print(f"speed: {one:.0f} ms a frame one at a time{note}, {many:.0f} ms a frame with 4 workers "
          f"({anonymise.describe()}); command line writes files and checks a folder")
    return one, many


def main():
    check_blurring()
    check_detection()
    check_real_face()
    check_metadata()
    check_zip()
    check_nobody()
    check_speed_and_cli()
    print("anonymise: all checks passed")


if __name__ == "__main__":
    main()
