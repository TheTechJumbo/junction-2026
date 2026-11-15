#!/usr/bin/env python3
"""Offline test of the laptop half of a private training (private_train.py): a frame comes out cropped, blurred where a
face is, garbled (not viewable) and decodable with the key; a splat whose colours were garbled decodes to the true
colours; a sealed bundle opens only with the vault key, and the vault key does not undo the garbling. No cluster.

    python live/test_private_train.py
"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import garble  # noqa: E402
import private_train  # noqa: E402
import vault  # noqa: E402
from test_anonymise import drawn_face, room  # noqa: E402


def main():
    master = vault.new_key()
    g = garble.Garbler(vault.derive(master, "garble"))
    vkey = vault.derive(master, "vault")
    with tempfile.TemporaryDirectory() as folder:
        frame = room(5, 720, 1280)  # a portrait frame: 720 wide, 1280 high
        face = drawn_face(160)
        frame[500:500 + face.shape[0], 250:250 + face.shape[1]] = face
        path = Path(folder) / "000000_0.jpg"
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        data, square, ms, report = private_train.prepare(path, g, side=600)
        sent = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert sent.shape == square.shape == (600, 600, 3), (sent.shape, square.shape)
        assert report["faces"] >= 1, report
        assert garble.psnr(sent, square) < 15, "the frame sent must not be viewable as the photo"
        assert garble.psnr(g.ungarble_bgr(sent), square) > 27, garble.psnr(g.ungarble_bgr(sent), square)
        assert set(ms) == {"read", "square", "anonymise", "garble", "jpeg"}

        # a splat file whose colours are garbled decodes back
        n = 5000
        rgb = np.random.default_rng(3).integers(0, 256, (n, 3)).astype(np.float64)
        rec = np.zeros((n, 32), np.uint8)
        rec[:, 24:27] = np.clip(g.encode_colours(rgb) + 0.5, 0, 255).astype(np.uint8)
        rec[:, 27] = 200
        splat = Path(folder) / "trained.splat"
        rec.tofile(splat)
        assert private_train.decode_splat(splat, g) == n
        back = np.fromfile(splat, np.uint8).reshape(-1, 32)
        err = np.abs(back[:, 24:27].astype(int) - rgb).mean()
        assert err < 1.5 and (back[:, 27] == 200).all(), err

        # the bundle as sent: sealed with the vault key, which cannot undo the garbling
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as z:
            z.writestr(path.name, data)
        sealed = vault.seal(vkey, bundle.getvalue())
        assert data not in sealed
        try:
            vault.unseal(master, sealed)
            raise AssertionError("the master key must not open what the vault key sealed")
        except vault.VaultError:
            pass
        assert zipfile.ZipFile(io.BytesIO(vault.unseal(vkey, sealed))).read(path.name) == data
        wrong = garble.Garbler(vkey)  # what Iridis could try with the only key it has
        assert garble.psnr(wrong.ungarble_bgr(sent), square) < 15
    print(f"private training (laptop half): frame cropped to 600 px, {report['faces']} face blurred, garbled "
          f"({garble.psnr(sent, square):.1f} dB from the photo), back with the key at {garble.psnr(g.ungarble_bgr(sent), square):.1f} dB "
          f"and not with the vault key; splat colours decode within {err:.2f} levels; one frame {sum(ms.values()):.0f} ms")
    print("private_train: all checks passed")


if __name__ == "__main__":
    main()
