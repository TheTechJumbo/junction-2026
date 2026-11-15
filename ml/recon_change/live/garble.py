#!/usr/bin/env python3
"""Garble the colours of a picture with a secret key, so what reaches Iridis is not a viewable photo, while VGGT
(cameras and depth) and gsplat (training a splat) still work on it. Invertible: the key undoes it exactly (up to 8-bit
rounding), on the pictures and on a trained splat's colours. See PRIVACY.md for what it does and does not protect.

    from garble import Garbler, new_key
    key = new_key()                          # 32 random bytes, one per session; never written to Iridis
    g = Garbler(key)
    hidden = g.garble_bgr(picture)           # uint8 BGR in, uint8 BGR out (same size): about 10 ms at 960 x 720
    back = g.ungarble_bgr(hidden)            # the picture again (within a few levels of 255)
    rgb = g.decode_colours(splat_rgb)        # a trained splat's colours (N x 3, 0..255, RGB) back to true colours
    data = g.garble_jpeg(jpeg_bytes)         # JPEG in, garbled JPEG out (quality 95, no chroma subsampling)

    python live/garble.py IN.jpg OUT.jpg [--key-hex HEX]     # for a look; prints the key it used

The transform G acts on each pixel's colour (R, G, B) in [0, 1]^3 and maps the colour cube onto itself:
  1. a keyed tone curve per channel (piecewise linear, 8 bins, slopes kept between 0.5 and about 3; some reversed),
  2. a keyed order of the three channels,
  3. LAYERS keyed coupling layers (RealNVP-style, as in neural spline flows): each re-maps one channel with a
     monotone piecewise-linear curve whose 8 bin heights a small keyed MLP computes from the other two channels
     (random Fourier features, 24 tanh units), sometimes reversed,
  4. another keyed tone curve per channel.
Every step is a bijection of the cube with bounded slopes, so G is too, and its inverse is computed exactly, step by
step. For speed G and its inverse are also sampled on a 65^3 grid (a 3D LUT, as colour grading uses) and applied with
trilinear interpolation through two cv2.remap calls. All parameters come from HMAC-SHA256(key, label || counter),
so any language with HMAC-SHA256 and float64 maths rebuilds the same G (PHONE_SPEC.md).

Keys differ in how much they hide: for many, the garbled picture's brightness pattern is close to the photo's (or its
negative). screened_key() draws keys until one hides the session's own sample frames' brightness (brightness_kept).

What it is and is not: a keyed, non-linear, invertible colour code. Without the key the colours are wrong in a way
that has no simple inverse (no 3 x 3 colour matrix comes close; measured in PRIVACY.md). It keeps every edge and shape:
someone looking at a garbled frame still sees the room's layout, objects and large text in false colours. One
picture whose true colours are known (a known-plaintext pair) gives enough colour pairs to fit the inverse, so use a
fresh key per session. It is obfuscation of appearance, not encryption: vault.py encrypts the files at rest.
"""
import argparse
import hashlib
import hmac
import secrets
import sys
import time

import cv2
import numpy as np

VERSION = b"junction-garble-v1"
BINS = 8             # bins of each piecewise-linear curve
GRID = 65            # side of the 3D LUT (65^3 points)
LAYERS = 4           # coupling layers
FOURIER = 8          # random Fourier features of the MLP (sin and cos of each)
HIDDEN = 24          # tanh units of the MLP
MIX = 0.4            # spread of the MLP's output weights: how much a curve changes with the other two channels
FREQ = 1.0           # spread of the Fourier frequencies: how fast it changes
SLOPE_MIN = 0.5      # every curve's smallest slope, so the inverse amplifies 8-bit rounding by at most 2x per step
JPEG_QUALITY = 95


def new_key():
    """32 random bytes from the operating system: one key per session, kept on the trusted side only."""
    return secrets.token_bytes(32)


def _stream(key, label, n):
    """n uniform floats in (0, 1) from HMAC-SHA256(key, VERSION | label | counter): 4 bytes each, big-endian."""
    out, counter = bytearray(), 0
    while len(out) < 4 * n:
        out += hmac.new(key, VERSION + b"|" + label.encode() + b"|" + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    words = np.frombuffer(bytes(out[:4 * n]), dtype=">u4").astype(np.float64)
    return (words + 0.5) / 2.0 ** 32


def _normal(key, label, n):
    """n standard normal floats (Box-Muller on pairs of _stream values: cos for even, sin for odd positions)."""
    u = _stream(key, label, 2 * ((n + 1) // 2)).reshape(-1, 2)
    r, t = np.sqrt(-2.0 * np.log(u[:, 0])), 2.0 * np.pi * u[:, 1]
    return np.stack([r * np.cos(t), r * np.sin(t)], 1).ravel()[:n]


def _heights(logits):
    """Bin heights of a monotone curve from logits (..., BINS): each at least SLOPE_MIN / BINS, summing to 1."""
    z = logits - logits.max(-1, keepdims=True)
    e = np.exp(z)
    return SLOPE_MIN / BINS + (1.0 - SLOPE_MIN) * e / e.sum(-1, keepdims=True)


def _curve(x, h):
    """The monotone piecewise-linear curve with bin heights h (..., BINS) at x in [0, 1]."""
    knots = np.concatenate([np.zeros(h.shape[:-1] + (1,)), np.cumsum(h, -1)], -1)
    k = np.clip(np.floor(x * BINS), 0, BINS - 1).astype(np.int64)
    t = x * BINS - k
    take = (lambda a: np.take_along_axis(a, k[..., None], -1)[..., 0]) if h.ndim > 1 else (lambda a: a[k])
    return np.clip(take(knots) + t * take(h), 0.0, 1.0)


def _uncurve(y, h):
    """The inverse of _curve."""
    knots = np.cumsum(h, -1)
    if h.ndim > 1:
        k = np.clip((y[..., None] >= knots[..., :-1]).sum(-1), 0, BINS - 1)
        hk = np.take_along_axis(h, k[..., None], -1)[..., 0]
        start = np.take_along_axis(np.concatenate([np.zeros(h.shape[:-1] + (1,)), knots], -1), k[..., None], -1)[..., 0]
    else:
        k = np.clip(np.searchsorted(knots[:-1], y, side="right"), 0, BINS - 1)
        hk, start = h[k], np.concatenate([[0.0], knots])[k]
    return np.clip((k + (y - start) / hk) / BINS, 0.0, 1.0)


class Garbler:
    """The keyed colour code for one key. Cheap to make; the first garble_bgr builds the forward LUT (about 2 s on the
    laptop), the first ungarble_bgr the inverse one."""

    def __init__(self, key, layers=LAYERS, mix=MIX, freq=FREQ, grid=GRID, flips=True, permute=True):
        if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
            raise ValueError("the key is at least 16 random bytes")
        key = bytes(key)
        self.layers, self.grid = layers, grid
        self.tone_in = _heights(1.2 * _normal(key, "tone_in", 3 * BINS).reshape(3, BINS))
        self.tone_out = _heights(1.2 * _normal(key, "tone_out", 3 * BINS).reshape(3, BINS))
        # flips=False, permute=False: the "mild" code (no reversed channels, channels kept in order), which VGGT reads
        # more like a photo; weaker, since brightness then stays roughly in order (PRIVACY.md)
        reversed_ = (_stream(key, "flips", 6 + layers) < 0.5) & flips
        self.flip_in, self.flip_out, self.flip_layer = reversed_[:3], reversed_[3:6], reversed_[6:]
        perms = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
        self.order = np.array(perms[int(_stream(key, "order", 1)[0] * 6)] if permute else perms[0])
        start = int(_stream(key, "first", 1)[0] * 3)
        self.target = [(start + k) % 3 for k in range(layers)]  # the channel each layer re-maps: every one in turn
        self.mlp = []
        for layer in range(layers):
            def n(what, size, layer=layer):
                return _normal(key, f"layer{layer}.{what}", size)
            self.mlp.append({
                "W": freq * n("W", FOURIER * 2).reshape(FOURIER, 2), "phase": 2 * np.pi * _stream(key, f"layer{layer}.phase", FOURIER),
                "W1": n("W1", 2 * FOURIER * HIDDEN).reshape(2 * FOURIER, HIDDEN) / np.sqrt(2 * FOURIER),
                "b1": 0.5 * n("b1", HIDDEN), "W2": mix * n("W2", HIDDEN * BINS).reshape(HIDDEN, BINS), "b2": 0.5 * n("b2", BINS)})
        self._atlases = {}  # the LUTs, built on first use (about 2 s each), so judging a key (screened_key) is cheap
        self._rx = np.arange(256, dtype=np.float32) * grid  # where tile R starts in an atlas row
        self._coord = np.arange(256, dtype=np.float32) * (grid - 1) / 255.0  # a level's position on the G and B grid

    # ---- the exact transform on colours in [0, 1], arrays (N, 3) in R, G, B order ----
    def _logits(self, layer, ab):
        p = self.mlp[layer]
        f = 2 * np.pi * ab @ p["W"].T + p["phase"]
        hidden = np.tanh(np.concatenate([np.sin(f), np.cos(f)], -1) @ p["W1"] + p["b1"])
        return hidden @ p["W2"] + p["b2"]

    def forward(self, rgb):
        x = np.array(rgb, dtype=np.float64).reshape(-1, 3)
        for c in range(3):
            x[:, c] = _curve(x[:, c], self.tone_in[c])
            if self.flip_in[c]:
                x[:, c] = 1.0 - x[:, c]
        x = x[:, self.order]
        for layer in range(self.layers):
            c = self.target[layer]
            others = [k for k in range(3) if k != c]
            y = _curve(x[:, c], _heights(self._logits(layer, x[:, others])))
            x[:, c] = 1.0 - y if self.flip_layer[layer] else y
        for c in range(3):
            x[:, c] = _curve(x[:, c], self.tone_out[c])
            if self.flip_out[c]:
                x[:, c] = 1.0 - x[:, c]
        return x.reshape(np.shape(rgb))

    def inverse(self, rgb):
        x = np.array(rgb, dtype=np.float64).reshape(-1, 3)
        for c in range(3):
            if self.flip_out[c]:
                x[:, c] = 1.0 - x[:, c]
            x[:, c] = _uncurve(x[:, c], self.tone_out[c])
        for layer in reversed(range(self.layers)):
            c = self.target[layer]
            others = [k for k in range(3) if k != c]
            y = 1.0 - x[:, c] if self.flip_layer[layer] else x[:, c]
            x[:, c] = _uncurve(y, _heights(self._logits(layer, x[:, others])))
        undo = np.empty(3, dtype=np.int64)
        undo[self.order] = np.arange(3)
        x = x[:, undo]
        for c in range(3):
            if self.flip_in[c]:
                x[:, c] = 1.0 - x[:, c]
            x[:, c] = _uncurve(x[:, c], self.tone_in[c])
        return x.reshape(np.shape(rgb))

    # ---- fast path on pictures: a 3D LUT as one 2D uint8 atlas, so one cv2.remap applies it ----
    def atlas(self, which):
        """The LUT of G ("forward") or of its inverse ("inverse"), built on first use: G sampled exactly on a GRID^3 cube,
        then interpolated along R to all 256 levels. Row = G grid position, column = R level * GRID + B grid position,
        value = the result as B', G', R' in 0..255, so cv2.remap's bilinear step does G and B and R is exact."""
        if which not in self._atlases:
            n = self.grid
            g = np.linspace(0.0, 1.0, n)
            cube = np.stack(np.meshgrid(g, g, g, indexing="ij"), -1).reshape(-1, 3)  # (R, G, B) on the grid, R slowest
            v = (self.forward(cube) if which == "forward" else self.inverse(cube)).reshape(n, n, n, 3)
            r = np.arange(256) * (n - 1) / 255.0
            r0 = np.minimum(np.floor(r).astype(int), n - 2)
            fr = (r - r0)[:, None, None, None]
            levels = v[r0] * (1 - fr) + v[r0 + 1] * fr  # (256 R levels, G grid, B grid, 3)
            atlas = levels.transpose(1, 0, 2, 3)[..., ::-1].reshape(n, 256 * n, 3)
            self._atlases[which] = np.ascontiguousarray(np.clip(atlas * 255.0 + 0.5, 0, 255).astype(np.uint8))
        return self._atlases[which]

    def _apply(self, atlas, bgr):
        if not (isinstance(bgr, np.ndarray) and bgr.dtype == np.uint8 and bgr.ndim == 3 and bgr.shape[2] == 3):
            raise ValueError("a BGR uint8 picture is expected")
        b, g, r = cv2.split(bgr)
        mx = cv2.add(cv2.LUT(r, self._rx), cv2.LUT(b, self._coord))
        my = cv2.LUT(g, self._coord)
        return cv2.remap(atlas, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    def garble_bgr(self, bgr):
        """The garbled picture (uint8 BGR, same size). The picture passed in is not changed."""
        return self._apply(self.atlas("forward"), bgr)

    def ungarble_bgr(self, bgr):
        """A garbled picture (or a render of a garbled splat) back to true colours."""
        return self._apply(self.atlas("inverse"), bgr)

    def decode_colours(self, rgb255):
        """Colours (N x 3, RGB, 0..255, e.g. a trained splat's) back to true colours, exactly (no LUT)."""
        return np.clip(self.inverse(np.asarray(rgb255, dtype=np.float64) / 255.0) * 255.0, 0, 255)

    def encode_colours(self, rgb255):
        return np.clip(self.forward(np.asarray(rgb255, dtype=np.float64) / 255.0) * 255.0, 0, 255)

    def garble_jpeg(self, data, quality=JPEG_QUALITY):
        """A picture file (JPEG, PNG, WebP) -> the garbled picture as a JPEG without chroma subsampling. A new file
        from pixels: no metadata. Raises ValueError on something that is not a picture."""
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        if picture is None:
            raise ValueError("not a picture")
        return encode_jpeg(self.garble_bgr(picture), quality)

    def ungarble_jpeg(self, data, quality=JPEG_QUALITY):
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        if picture is None:
            raise ValueError("not a picture")
        return encode_jpeg(self.ungarble_bgr(picture), quality)


def brightness_kept(g, pictures, side=128):
    """How much of the pictures' brightness pattern survives garbling with g: the median, over the pictures, of
    |correlation| between the grey garbled picture and the grey photo (1: the grey picture, or its negative, is the photo's;
    0: unrelated). Uses the exact transform on small copies, so no LUT is built."""
    values = []
    for picture in pictures:
        small = cv2.resize(picture, (side, side), interpolation=cv2.INTER_AREA)
        hidden = np.clip(g.forward(small[..., ::-1].reshape(-1, 3) / 255.0) * 255 + 0.5, 0, 255).astype(np.uint8)
        a = cv2.cvtColor(hidden.reshape(small.shape)[..., ::-1].copy(), cv2.COLOR_BGR2GRAY).astype(np.float64).ravel()
        b = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float64).ravel()
        values.append(abs(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else 1.0)
    return float(np.median(values))


def screened_key(pictures, derive=lambda k: k, limit=0.8, tries=64, make=new_key, **options):
    """A new key whose code hides these pictures' brightness pattern: brightness_kept below `limit` on them (some sample
    frames of the session, on the trusted side). Keys vary a lot: over 40 random keys on real keyframes the median was
    0.95, the best 0.3 (PRIVACY.md). derive(key) gives the garbling key (e.g. vault.derive(K, "garble")).
    Returns (key, its brightness_kept, tries); the best key seen if none is under the limit."""
    best = None
    for n in range(1, tries + 1):
        key = make()
        kept = brightness_kept(Garbler(derive(key), **options), pictures)
        if best is None or kept < best[1]:
            best = (key, kept, n)
        if kept < limit:
            return key, kept, n
    return best[0], best[1], tries


def encode_jpeg(bgr, quality=JPEG_QUALITY):
    """JPEG with 4:4:4 sampling: chroma subsampling would average garbled colours, which do not average like true ones."""
    options = [cv2.IMWRITE_JPEG_QUALITY, quality]
    if hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR"):
        options += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
    ok, data = cv2.imencode(".jpg", bgr, options)
    if not ok:
        raise ValueError("could not encode the picture")
    return data.tobytes()


def psnr(a, b):
    mse = float(np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2))
    return 99.0 if mse < 1e-12 else 10 * np.log10(255.0 ** 2 / mse)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Garble a picture's colours with a key (and back with --undo).")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--key-hex", help="the key as hex (default: a new random one, printed)")
    ap.add_argument("--undo", action="store_true", help="ungarble instead")
    args = ap.parse_args(argv)
    key = bytes.fromhex(args.key_hex) if args.key_hex else new_key()
    t0 = time.perf_counter()
    g = Garbler(key)
    built = time.perf_counter() - t0
    picture = cv2.imread(args.src, cv2.IMREAD_COLOR)
    if picture is None:
        ap.error(f"cannot read {args.src}")
    t0 = time.perf_counter()
    out = g.ungarble_bgr(picture) if args.undo else g.garble_bgr(picture)
    took = time.perf_counter() - t0
    cv2.imwrite(args.dst, out, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not args.key_hex:
        print(f"key {key.hex()}  (keep it: it is the only way back)", file=sys.stderr)
    print(f"built in {1000 * built:.0f} ms, {'ungarbled' if args.undo else 'garbled'} {picture.shape[1]}x{picture.shape[0]} "
          f"in {1000 * took:.1f} ms -> {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
