# Local detected-face redaction · JUN-18

This module adds offline CPU face detection and strong local blur before evidence
storage. It does not upload images, call a model service, create a cache, write raw
image files, or download anything at runtime. All detected faces are blurred; there
is no face recognition or attempt to distinguish a bystander from the wearer.
The sensor/question fast path must not call this module.

## Integration contract

```python
from backend.privacy import LocalFaceRedactor, RedactionUnavailable

redactor = LocalFaceRedactor(timeout_seconds=3)  # one shared instance per app
try:
    result = redactor.redact(normalized_image_bytes)
except RedactionUnavailable:
    # Reject evidence attachment. Never store the original as an error fallback.
    raise
# Store only result.image_bytes, MIME result.content_type (image/png).
# result.faces_redacted is operator diagnostics, not a canonical Event field.
# result.status == "no_faces_detected" explicitly means none were detected;
# it does not establish that no face exists in the image.
```

The synchronous API belongs in a bounded thread or synchronous request handler;
it must not block an async event loop. One instance admits at most two child
processes, with immediate busy rejection. Root owns HTTP wiring/error envelopes,
manifest changes, storage and 24-hour retention. This module changes none of them.
Metadata should be normalized/stripped before this call; the module also strips
metadata from its output and handles EXIF orientation for standalone callers.
Use PNG metadata consistently when storing output: input MIME may have been JPEG.

A killable isolated Python child decodes, detects, blurs and re-encodes through
stdin/stdout pipes. The deadline starts before the input-header check and includes
spawn, processing and IPC; expired work is killed and reaped. OS scheduling/process
creation/reaping can add overhead outside the requested budget. The child inherits
only PATH/SYSTEMROOT, has no application/provider credentials, ignores PYTHONPATH
and user-site packages, and pins native thread counts to one. This is processing
isolation, not an OS security sandbox or a guarantee against host swap/core dumps.

Bounds: 5 MiB encoded input, JPEG or single-frame PNG only, both dimensions
32..1280, at most 1,048,576 decoded pixels, two concurrent children, 64 returned
faces, 5,000 pre-NMS candidates, 8 MiB output. The bundled 232,589-byte YuNet model
must match its SHA-256 before detection. OpenCV CPU backend/target and no OpenCL;
score threshold 0.6, NMS 0.3. Each detected box expands by 35% per side and receives
a Gaussian blur with radius one third of the expanded maximum dimension. Pixels
outside expanded boxes remain unchanged in decoded RGB. Size/format/model/runtime/
malformed detector output/timeout/busy errors return no accepted image.

## Achievable acceptance and limits

JUN-18 asks for no unblurred bystander face ever stored. A detector cannot prove
that condition: small, rotated, occluded, poorly lit, blurred, unusual or distant
faces can be missed; the real fixture is not a demographic or capture-condition
benchmark. Zero detections is reported explicitly, never described as face-free.
A broad threshold and padded blur reduce exposure on detections but do not prove
anonymity or prevent every recognition method. Unusual settings can cause false
positives that blur meaningful scene details. Oversized inputs are rejected rather
than downsampled and possibly hiding small faces. No full-image blanking fallback.

Verified acceptance here: a real public-domain portrait is detected, its known
face extent falls inside the expanded mask, facial detail is strongly reduced,
nonface context survives, two copies produce two masks, and technical failures
cannot return the original. Keep the stronger JUN-18 guarantee unresolved; policy
for review/retention of zero-detection images belongs to the product owner. Do not
mark the issue complete based only on this module. No household examples tested.

## Assets and primary sources

- Detector: OpenCV Zoo YuNet `face_detection_yunet_2023mar.onnx`, pinned repository
  commit `48b10fa505aeb51fbd127fc60a5c660040e823b7`.
  SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
  MIT license copied unmodified to `models/LICENSE`. Official source:
  https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet
  Binary download used (build-time only):
  https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
- OpenCV FaceDetectorYN CPU API and parameters:
  https://docs.opencv.org/4.13.0/df/d20/classcv_1_1FaceDetectorYN.html
- Real test fixture is a public NASA astronaut portrait distributed by scikit-image;
  provenance and public-domain statement are in the adjacent fixture README.

## Dependency/package proposal for integrator (not applied here)

Add to pyproject dependencies, matching the tested major/minor:
`opencv-python-headless>=4.13,<4.14`. Add exact lock lines:

```text
numpy==2.2.6
opencv-python-headless==4.13.0.92
```

OpenCV's official PyPI metadata requires NumPy>=2 for supported Python versions.
Only one OpenCV distribution should be installed, to avoid sharing the cv2 module
with opencv-python/contrib variants. These are CPU/headless wheels, no GPU runtime.

For installed-wheel use outside a checkout, root must add:

```toml
[tool.setuptools.package-data]
"backend.privacy" = ["models/*.onnx", "models/LICENSE"]
```

Verified installation on Python3.12 macOS ARM64. Official PyPI files also provide
compatible Linux x86_64 wheels (availability verified, not executed here):

- opencv_python_headless-4.13.0.92-cp37-abi3-macosx_13_0_arm64.whl
- opencv_python_headless-4.13.0.92-cp37-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
- numpy-2.2.6-cp312-cp312-macosx_11_0_arm64.whl (and macOS14 ARM64)
- numpy-2.2.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl

NumPy2.2.6 requires Python>=3.10 and also provides cp311 versions of the listed
macOS/Linux wheels, preserving the project's Python3.11 minimum. The initial
NumPy2.5.4 evaluation required Python>=3.12 and is not the proposed lock.

Primary metadata: https://pypi.org/pypi/opencv-python-headless/4.13.0.92/json
and https://pypi.org/pypi/numpy/2.2.6/json . No shared manifests edited by this worker.

## Verification and measurements

Run `.venv/bin/python -m pytest -q backend/tests/privacy` after dependency install;
tests deliberately do not skip a missing detector. All 23 privacy tests passed on the proposed NumPy2.2.6 / OpenCV4.13.0.92 pairing,
and pip check found no broken requirements. All model and fixture assets are
bundled, so tests need no credentials or network. Tests cover public-face masks,
nonface pixels, multiple faces, explicit no-detection output, metadata/orientation,
input/pixel/animation limits, model integrity, detector errors, subprocess crash/
timeout/reaping, total deadline and concurrency saturation.

Measured initially on this worker's macOS ARM64 / Python3.12 with NumPy2.5.4: first native import after wheel
installation exceeded the default 3s budget and was rejected; a retry with a 10s
budget took 9.017s. Subsequent ten full subprocess calls on the 512x512 portrait
had median 0.162s, maximum 0.324s (includes imports/decode/detect/blur/IPC). These are
local warmed measurements, not a first-call latency guarantee or production p95.
Default budget remains 3s, configurable >0 through 10s. API importer read budgets
must include configured processing time plus normalization/network margin. Test
fixture calls use 10s to tolerate cold native loading; timeout tests exercise
rejection. No Linux runtime or live hardware measurement performed by this worker.

On the final proposed NumPy2.2.6 pair, ten subsequent full worker calls had median
0.143s and maximum 0.292s on the same fixture/machine. Final `make check`: 332
passed, 8 integration tests deselected; ruff/schema checks passed. Linux execution and
HTTP/storage integration remain integrator checks.
