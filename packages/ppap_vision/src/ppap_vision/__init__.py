"""PPAP Vision - image processing, seal/signature detection.

This package provides vision-related capabilities:

- **image**: Image extraction, conversion, and OpenAI message building
- **detectors**: Seal and signature detection using computer vision

Example usage:
    from ppap_vision.image import ImageExtractor, extract_images_from_bytes
    from ppap_vision.detectors import detect_seal, detect_signature_or_stamp
"""

from typing import TYPE_CHECKING

__version__ = "0.1.0"

# Re-export common image utilities eagerly; keep detector imports lazy so
# non-vision image paths do not import OpenCV as a side effect.
from .image import (
    ImageExtractor,
    OpenAIImageMessageBuilder,
    create_openai_image_message,
    extract_images_from_bytes,
)

if TYPE_CHECKING:
    from .detectors import detect_seal, detect_signature_or_stamp


def __getattr__(name: str) -> object:
    if name == "detect_seal":
        from .detectors import detect_seal

        globals()[name] = detect_seal
        return detect_seal
    if name == "detect_signature_or_stamp":
        from .detectors import detect_signature_or_stamp

        globals()[name] = detect_signature_or_stamp
        return detect_signature_or_stamp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Version
    "__version__",
    # Image utilities
    "ImageExtractor",
    "OpenAIImageMessageBuilder",
    "extract_images_from_bytes",
    "create_openai_image_message",
    # Detectors
    "detect_seal",
    "detect_signature_or_stamp",
]
