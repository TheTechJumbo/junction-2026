# Garbling on the phone: spec for the app session (JUN-26, PRIVACY.md)

Status: a spec. Nothing in the app does this yet. `live/garble.py` is the reference implementation, and
`live/garble_reference.txt` holds the values a Kotlin version must reproduce. The laptop path (the relay does all of it)
is implemented in `live/private_train.py`. This spec is the phone path, if Seb chooses it.

## What the phone does to every picture before it leaves the phone

This covers every frame of a recording chunk (`POST /recording`), every `/locate` photo, the `big.jpg` full-size photo,
item photos (`/enroll`) and video chunks. The order:

1. **Crop and shrink.** For a portrait frame, keep the middle square (`h > 1.05 w`: rows `(h-w)/2 ... (h-w)/2 + w`).
   Then shrink so the long side is at most 768 px (area averaging). Iridis only ever uses 518 px.
2. **Blur faces and people.** Faces get a flat fill of their mean colour, after growing the box by 40% on each side.
   People get pixelation to about 8 blocks along the box's longer side. A detector is a new dependency, and Seb approves
   any download first. Options: ML Kit face detection (bundled model, about 2–3 MB in the APK), or MediaPipe BlazeFace
   (about 0.2 MB `.tflite`) plus MediaPipe's EfficientDet-Lite0 object detector for "person" (about 4.4 MB). If no
   detector is shipped, the relay can still run `anonymise.py` on the garbled frames, but less well: its colour-based
   cues are gone. Not recommended.
3. **Garble** with the session's garbling key (below). It is a per-pixel colour map, applied as a 3D LUT.
4. **Encode losslessly: WebP lossless** (`Bitmap.CompressFormat.WEBP_LOSSLESS`, API 30+). Android's JPEG encoder always
   subsamples chroma (4:2:0). That averages garbled colours, which do not average like true ones, and costs about 7 dB
   after decoding. Measured on 8 keyframes at 518 px, decoded colours against the original:

   | sent as | round trip | size |
   |---|---|---|
   | WebP lossless | 47.7 dB | 217 kB |
   | JPEG q95 4:4:4 (libjpeg-turbo, `TJSAMP_444`, via NDK) | 35.1 dB | 124 kB |
   | JPEG q95 4:2:0 (`Bitmap.compress`) | 27.8 dB | 79 kB |
   | the plain photo, JPEG q90 (no garbling) | 49.8 dB | 40 kB |

   Keep the file names the app uses now (`NNNNNN_<ms>.jpg`). OpenCV on the relay and on Iridis reads the format from
   the bytes, not the name.
5. **Strip metadata.** Write a new file from pixels, with no EXIF and no GPS. Both encoders above already do that.
6. Mark the upload with a header: `X-Garbled: v1; kid=<16 hex digits>`, where `kid` is the first 8 bytes of
   HMAC-SHA256(garbling key, "key-id"). Without this header the relay garbles the frame itself (the laptop path).

The relay then seals each zip with the vault key (`vault.py`, AES-256-GCM) before anything goes to Iridis. The phone
does not need to seal: its link to the relay is local (adb reverse over USB, or the LAN with the relay key).

## The key

* The relay makes a session master key K: 32 bytes from `secrets.token_bytes`, one per zone and recording session.
* The phone gets only `derive(K, "garble")` = HMAC-SHA256(K, "derive|garble"). Iridis gets only
  `derive(K, "vault")`. Neither can compute the other's key.
* Handover: `POST /privacy/session?zone=Z` on the relay, with `X-Key`, over adb reverse (localhost) only, returns
  `{"garble_key": hex, "kid": hex}`. This endpoint is still to be added to the relay; coordinate with the parent session.
  Keep the key in memory. Never write it to storage, never log it, and drop it when the session ends.

## The transform G (all maths in float64)

`stream(key, label, n)`: for counter = 0, 1, ..., concatenate
HMAC-SHA256(key, "junction-garble-v1" + "|" + label + "|" + counter as 4 bytes big-endian). Read each 4-byte
big-endian word w as u = (w + 0.5) / 2^32, giving n numbers in (0, 1).
`normal(key, label, n)`: pairs (u1, u2) from `stream(key, label, 2*ceil(n/2))`. For each pair,
r = sqrt(-2 ln u1), t = 2π u2, and output r cos t, then r sin t. Keep the first n.

Constants: BINS = 8, LAYERS = 4, FOURIER = 8, HIDDEN = 24, MIX = 0.4, FREQ = 1.0, SLOPE_MIN = 0.5, GRID = 65.

`heights(z)` for a vector z of 8 logits: softmax(z − max z) × (1 − SLOPE_MIN) + SLOPE_MIN / 8.
`curve(x, h)`: knots k_0 = 0, k_i = h_0 + ... + h_{i-1}. With b = clamp(floor(8x), 0, 7) and t = 8x − b, the result is
clamp(k_b + t h_b, 0, 1).

Parameters (label in quotes; arrays are row-major):
* `tone_in` = heights(1.2 × normal("tone_in", 24) as 3×8), one row per channel; `tone_out` the same with "tone_out".
* flips = stream("flips", 10) < 0.5: flip_in = flips[0..2], flip_out = flips[3..5], flip_layer = flips[6..9].
* order = the permutation with index floor(6 × stream("order", 1)[0]) in this list: (0,1,2) (0,2,1) (1,0,2) (1,2,0)
  (2,0,1) (2,1,0).
* first = floor(3 × stream("first", 1)[0]). Layer l re-maps channel (first + l) mod 3.
* For each layer l, with labels "layer{l}.W" and so on:
  * W = FREQ × normal("layer{l}.W", 16) as 8×2
  * phase = 2π × stream("layer{l}.phase", 8)
  * W1 = normal("layer{l}.W1", 384) as 16×24, divided by 4
  * b1 = 0.5 × normal("layer{l}.b1", 24)
  * W2 = MIX × normal("layer{l}.W2", 192) as 24×8
  * b2 = 0.5 × normal("layer{l}.b2", 8)

G(r, g, b) on [0, 1]^3, in R, G, B order:
1. For each channel c: x_c = curve(x_c, tone_in[c]); if flip_in[c], x_c = 1 − x_c.
2. x = (x[order[0]], x[order[1]], x[order[2]]).
3. For each layer l: c = (first + l) mod 3, and (a, b) = the other two channels in increasing index order.
   * f = 2π (W · (a, b)) + phase (8 values)
   * hidden = tanh([sin f, cos f] · W1 + b1)
   * logits = hidden · W2 + b2
   * y = curve(x_c, heights(logits)); x_c = 1 − y if flip_layer[l], otherwise y.
4. For each channel c: x_c = curve(x_c, tone_out[c]); if flip_out[c], x_c = 1 − x_c.

**Applying it fast.** Build G on the 65³ grid (R slowest). Interpolate linearly along R to all 256 levels. Store the
result as uint8 (round(255 v)) in an atlas: 65 rows (G grid), 256 × 65 columns (R level × 65 + B grid), with B', G', R'
per texel. For a pixel (R, G, B):
* x = R × 65 + B × 64/255
* y = G × 64/255
* bilinear-sample the atlas at (x, y)

This is exactly what `garble.py` does with `cv2.remap`. On Android, do the bilinear lookup on the CPU (about 20
operations a pixel, which is fine at 2 frames a second), or on the GPU as an AGSL `RuntimeShader` with the atlas as a
bitmap.

**Check:** with key bytes 00 01 ... 1f, G must reproduce `live/garble_reference.txt` to 6 decimals, for example
G(0.2, 0.5, 0.8) = (0.096227, 0.476902, 0.854748). The same file lists `stream` and `normal` values, and the order and
flips, for checking the first steps.

## Cost on the phone (estimate, not measured)

Laptop, measured (i5-10300H with other sessions running, venue frames 1376×1840, one thread, median ms):
* read 109, crop and shrink to 768 px 15, anonymise 176, garble 16, JPEG 4:4:4 62: total 377 ms
* 4 threads: 156 ms a frame

A Pixel gets frames from the camera as pixels, so it does not read a JPEG first. The garble costs about 10–40 ms (atlas
on the CPU) or under 5 ms (GPU). A face detector is 10–30 ms on its NPU or GPU. WebP lossless at 768 px is about 40–80 ms.
That comes to roughly 60–150 ms a frame. Recordings send 2 frames a second, so that fits on one core. Building the LUT
when a session starts is 274,625 evaluations of G, under a second.
