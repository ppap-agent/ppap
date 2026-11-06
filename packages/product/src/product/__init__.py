"""PPAP application orchestration."""

from .composition import build_product_store
from .rules import latest_rule_versions

__all__ = [
    "build_product_store",
    "latest_rule_versions",
]
