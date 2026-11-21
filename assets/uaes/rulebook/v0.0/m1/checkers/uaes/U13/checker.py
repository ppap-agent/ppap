"""UAES U13 runs the uaes reader implementation for each bound clause."""

from audit.checker_lib.agents import AgentAudit
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return AgentAudit(audit_file, rule, group_context, resource=RESOURCE)
