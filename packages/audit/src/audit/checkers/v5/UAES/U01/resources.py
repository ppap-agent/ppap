"""Evidence scope owned by this directory leaf.

`clauses` is empty: this leaf has no evidence reader. Every bound clause
stays UNABLE. Unknown is not a pass. This module does not call another checker.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U01",
    "supported_suffixes": (),
    "clauses": {},
}
