"""Observation tools for the execution loop. The audit kernel remains the judge."""

from audit.document_tools import ParserLimitError, ReadLimits, snapshot

__all__ = ["ParserLimitError", "ReadLimits", "snapshot"]
