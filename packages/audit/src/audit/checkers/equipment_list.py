"""Checker instance. v2 and v3 rule profiles keep their existing classes."""

from audit.checkers.v3 import equipment_list_agent as v3
from audit.checkers.v2 import equipment_list_agent as v2

__all__ = ["v3", "v2"]
