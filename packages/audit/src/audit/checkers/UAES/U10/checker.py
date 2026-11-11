"""U10 source-backed UAES checks owned by this immutable directory leaf.

The business implementation is local. No checker-version fallback is used.
"""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return ControlledDocumentAudit(audit_file, rule, group_context, resource=RESOURCE)


class ControlledDocumentAudit(SourceAudit):
    def rule_1(self):
        return self.controlled_stamp()
