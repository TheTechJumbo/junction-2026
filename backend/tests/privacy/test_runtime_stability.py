"""Regression for native nonfinite outputs on small detector input shapes."""
import io

import pytest
from PIL import Image

from backend.app import verify_image
from backend.privacy import LocalFaceRedactor


@pytest.mark.parametrize("case", ["jpeg", "two_color", "tiny_png"])
def test_tiny_evidence_is_stable_across_fresh_native_workers(case):
    size, color, media_type = (8, 8), "green", "image/jpeg"
    if case == "two_color":
        size, color, media_type = (32, 24), "orange", "image/png"
    elif case == "tiny_png":
        size, color, media_type = (3, 2), "blue", "image/png"
    image = Image.new("RGB", size, color)
    if case == "two_color":
        image.paste("navy", (16, 0, 32, 24))
    source = io.BytesIO()
    image.save(source, format="JPEG" if media_type == "image/jpeg" else "PNG")
    normalized, mime = verify_image(source.getvalue(), media_type)
    processor = LocalFaceRedactor()
    # The Linux failure varied across fresh processes. Exercise the production
    # subprocess path, not an in-process stand-in or a retry-until-success loop.
    for _ in range(10):
        result = processor.redact(normalized)
        assert result.status == "no_faces_detected"
        assert result.content_type == mime
        with Image.open(io.BytesIO(result.image_bytes)) as stored:
            assert stored.size == size
