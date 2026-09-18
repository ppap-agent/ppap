"""Product draft roles and identification statuses. Not an accepted audit round."""
from __future__ import annotations

ROLE_TARGET = "target"
ROLE_REFERENCE = "reference"
ROLES = {ROLE_TARGET, ROLE_REFERENCE}

DRAFT_OPEN = "open"

IDENT_PENDING = "pending"
IDENT_UNRECOGNIZED = "unrecognized"
IDENT_UNSUPPORTED = "unsupported"
IDENT_AMBIGUOUS = "ambiguous"
IDENT_UNCERTAIN = "uncertain"
IDENT_MATCHED = "matched"
IDENT_UNKNOWN_PROJECT = "unknown_project"
IDENT_STATUSES = {
    IDENT_PENDING,
    IDENT_UNRECOGNIZED,
    IDENT_UNSUPPORTED,
    IDENT_AMBIGUOUS,
    IDENT_UNCERTAIN,
    IDENT_MATCHED,
    IDENT_UNKNOWN_PROJECT,
}

SUPPORTED_AUDIT_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".pdf", ".doc", ".docx"}

# Filename token-alias weight is 12; below that a type hit is shown as uncertain, not a default match.
MATCH_MIN_SCORE = 12
MATCH_MIN_MARGIN = 8
