"""Human review notes. Not formal approval and not S05 Live."""
from __future__ import annotations

REVIEW_NOTED = "noted"
REVIEW_QUESTIONED = "questioned"
REVIEW_ACTION_REQUIRED = "action_required"
REVIEW_JUDGMENTS = {REVIEW_NOTED, REVIEW_QUESTIONED, REVIEW_ACTION_REQUIRED}
