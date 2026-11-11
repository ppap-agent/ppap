"""Text normalization and structured keys used by the V2 fixed checkers."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_key(value: object) -> str:
    text = normalize_text(value).lower()
    return re.sub(r"[\s\-_/,.，。:：;；()（）\[\]{}]+", "", text)


def build_struct_key(*values: object) -> str:
    parts = [normalize_key(value) for value in values]
    parts = [part for part in parts if part]
    return "|".join(parts)
