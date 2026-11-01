"""Checker instance. v2 and v3 rule profiles keep their existing classes."""

from audit.checkers.v2 import xir_assembly_issue_agent as v2
from audit.checkers.v3 import xir_assembly_issue_agent as v3

__all__ = ["v3", "v2"]
