"""Photo-only scope can decode pixels without declaring part correctness."""

from io import BytesIO

import fitz
import pytest
from documents.photo_inputs import validate_photo_input
from PIL import Image


def photo(format="PNG"):
    output = BytesIO()
    Image.new("RGB", (16, 12), "white").save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize(("filename", "format"), [("part.png", "PNG"), ("part.jpg", "JPEG"), ("part.jpeg", "JPEG"), ("part.webp", "WEBP")])
def test_actual_pixels_are_valid_without_a_content_judgment(filename, format):
    assert validate_photo_input(filename, photo(format)) == {"state": "valid", "reasons": [], "image_count": 1}


@pytest.mark.parametrize(
    ("filename", "content"),
    [("part.png", b"damaged"), ("part.png", photo("JPEG")), ("part.pdf", b"damaged"), ("part.svg", b"<svg/>"), ("photos.zip", b"PK")],
    ids=["damaged-image", "wrong-carrier", "damaged-pdf", "svg", "zip"],
)
def test_invalid_or_disguised_carriers_cannot_be_declared_readable(filename, content):
    assert validate_photo_input(filename, content)["state"] == "invalid"


def test_image_only_pdf_is_readable_but_text_only_pdf_is_not_photo_input():
    with fitz.open() as document:
        document.new_page().insert_image(fitz.Rect(0, 0, 100, 100), stream=photo())
        result = validate_photo_input("photos.pdf", document.tobytes())
        assert result == {"state": "valid", "reasons": [], "image_count": 1}
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "No photograph is present")
        assert validate_photo_input("text.pdf", document.tobytes())["state"] == "invalid"


def test_budget_exhaustion_is_unknown_technical_state(monkeypatch):
    monkeypatch.setattr("documents.photo_inputs._MAX_IMAGE_PIXELS", 10)
    assert validate_photo_input("part.png", photo())["state"] == "error"
