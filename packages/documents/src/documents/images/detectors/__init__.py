"""Seal and signature detectors.

提供图像中的印章和签名检测功能。
"""

from .seal import detect_seal
from .signature import detect_signature_or_stamp

__all__ = [
    "detect_seal",
    "detect_signature_or_stamp",
]
