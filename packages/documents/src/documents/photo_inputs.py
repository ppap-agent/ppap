"""Bounded decoding for an explicitly selected photo-only document input.

Calling code owns the customer/rule scope. A valid input says that readable
image pixels exist. It does not say that the photograph shows a correct part.
Photo collections are multiple frozen files, not an extracted archive.
"""

from __future__ import annotations

import warnings
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, UnidentifiedImageError

_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_DIMENSION = 16384
_MAX_IMAGE_PIXELS = 32_000_000
_MAX_TOTAL_PIXELS = 128_000_000
_MAX_PDF_PAGES = 256
_MAX_IMAGES = 256
_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


class _PhotoLimit(Exception):
    pass


def _decode(content: bytes, expected: str | None = None) -> int:
    if len(content) > _MAX_IMAGE_BYTES:
        raise _PhotoLimit
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(content)) as picture:
            if expected is not None and picture.format != expected:
                raise ValueError("image carrier mismatch")
            width, height = picture.size
            if width <= 0 or height <= 0:
                raise ValueError("empty image")
            pixels = width * height
            if max(width, height) > _MAX_DIMENSION or pixels > _MAX_IMAGE_PIXELS:
                raise _PhotoLimit
            if getattr(picture, "n_frames", 1) != 1:
                raise ValueError("photo input requires one frame")
            # verify() alone does not decode all image data.
            picture.load()
            return pixels


def validate_photo_input(filename: str, content: bytes) -> dict:
    """Return valid/invalid/error without a business judgment or external IO."""
    suffix = Path(filename).suffix.casefold()
    if suffix not in {*_FORMATS, ".pdf"}:
        return {"state": "invalid", "reasons": ["样品照片仅支持 PNG、JPEG、WebP 或含照片的 PDF；照片集合请逐文件上传。"], "image_count": 0}
    if len(content) > _MAX_SOURCE_BYTES:
        return {"state": "error", "reasons": ["照片输入超出安全读取限制，尚未完成检查。"], "image_count": 0}
    try:
        if suffix in _FORMATS:
            _decode(content, _FORMATS[suffix])
            count = 1
        else:
            count = pixels = 0
            seen = set()
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass or document.is_encrypted or document.is_repaired or document.page_count == 0:
                    raise ValueError("unreadable PDF")
                if document.page_count > _MAX_PDF_PAGES:
                    raise _PhotoLimit
                for page in document:
                    images = page.get_images(full=True)
                    if not images:
                        raise ValueError("PDF page has no photo pixels")
                    for image in images:
                        xref, _mask, width, height = image[:4]
                        if max(width, height) > _MAX_DIMENSION or width * height > _MAX_IMAGE_PIXELS:
                            raise _PhotoLimit
                        if xref in seen:
                            continue
                        seen.add(xref)
                        count += 1
                        if count > _MAX_IMAGES:
                            raise _PhotoLimit
                        decoded = document.extract_image(xref)
                        if not decoded or not decoded.get("image"):
                            raise ValueError("unreadable PDF image")
                        pixels += _decode(decoded["image"])
                        if pixels > _MAX_TOTAL_PIXELS:
                            raise _PhotoLimit
                    # Decode the composed page too, at a bounded resolution.
                    scale = min(1.0, (2_000_000 / max(1, page.rect.width * page.rect.height)) ** 0.5)
                    if not page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).samples:
                        raise ValueError("empty rendered page")
                if document.is_repaired:
                    raise ValueError("repaired PDF")
    except (_PhotoLimit, Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError):
        return {"state": "error", "reasons": ["照片解码超出安全限制，尚不能确认输入有效。"], "image_count": 0}
    except (UnidentifiedImageError, ValueError, OSError, fitz.FileDataError):
        return {"state": "invalid", "reasons": ["照片格式与内容不符、已损坏、已加密，或 PDF 页中没有可解码照片。"], "image_count": 0}
    except Exception:
        return {"state": "error", "reasons": ["照片读取暂不可用，尚未完成检查。"], "image_count": 0}
    return {"state": "valid", "reasons": [], "image_count": count}
