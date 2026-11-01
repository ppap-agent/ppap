"""Real OpenCV detectors preserve the existing seal/signature signal boundary."""

import base64
from io import BytesIO

import pytest
from documents.images.detectors import detect_seal, detect_signature_or_stamp
from PIL import Image, ImageDraw


@pytest.mark.parametrize(
    ("image_kind", "expected"),
    [("center-stamp", (True, True)), ("corner-stamp", (True, False)), ("blank", (False, False)), ("invalid", (False, False))],
)
@pytest.mark.parametrize("data_url", [True, False], ids=["data-url", "raw-base64"])
def test_actual_detectors_distinguish_stamp_position_blank_and_invalid_images(image_kind, expected, data_url):
    if image_kind == "invalid":
        image_bytes = b"not an image"
    else:
        image = Image.new("RGB", (300, 300), "white")
        if image_kind != "blank":
            bounds = (100, 100, 230, 230) if image_kind == "center-stamp" else (25, 25, 155, 155)
            ImageDraw.Draw(image).ellipse(bounds, outline=(220, 0, 0), width=18)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    if data_url:
        encoded = f"data:image/png;base64,{encoded}"

    assert (detect_seal(encoded), detect_signature_or_stamp(encoded)) == expected
