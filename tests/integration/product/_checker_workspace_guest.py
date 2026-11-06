"""Deterministic author boundary for the workspace browser contract.

This is not a provider and never accepts a real key. All manifests, trials,
Core checks and evidence validation still execute the production guest code.
The subprocess is not a security/container isolation claim.
"""

import json
import sys
from pathlib import Path

PROGRAM = """import json
from types import SimpleNamespace
from openpyxl import load_workbook
from audit.contracts import AuditResultItem, AuditEvidence, EvidenceSource

def create_audit(audit_file, rule, group_context=None):
    book = load_workbook(audit_file.file_url, data_only=True)
    try:
        value = book.active["A1"].value
    finally:
        book.close()
    def check(clause):
        locator = json.dumps({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1",
                              "file_version_id": audit_file.file_metadata["file_version_id"]})
        return AuditResultItem(classification="规则审核", item="阈值", priority="P0",
            status=STATUS_EXPRESSION, suggestions="已核对原件 A1 的阈值。", clause_id=clause, checks_performed=1,
            evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),)))
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id=row["id"]),
        check=lambda clause=row["id"]: check(clause)) for row in rule.rules])
"""
BASELINE = PROGRAM.replace("STATUS_EXPRESSION", '"PASS" if value == 5 else "NG" if value in (6, 15) else "UNABLE"')
CORRECTED = PROGRAM.replace("STATUS_EXPRESSION", '"PASS" if value >= 10 else "NG"')


class DeterministicAuthor:
    wall_seconds = 60
    input_tokens = output_tokens = 0

    def __init__(self, grant):
        assert grant["token"] == "synthetic-unused-workspace-grant-1234567890"

    def run(self, work, prompt, schema, deadline, **kwargs):
        # Held-out original and label must not enter the author directory/prompt.
        assert "holdout-secret" not in prompt
        from openpyxl import load_workbook

        for path in work.rglob("*.xlsx"):
            book = load_workbook(path, data_only=True)
            try:
                assert book.active["B1"].value != "holdout-secret"
            finally:
                book.close()
        return schema(entrypoint="checker:create_audit", files={"checker.py": CORRECTED})


if __name__ == "__main__":
    from agent.execution import guest_model
    from agent.execution.guest import run_entrypoint

    guest_model.PiRunner = DeterministicAuthor
    inputs, work, grants = map(Path, sys.argv[1:])
    # A localhost dead endpoint fails closed if any unexpected code tries to
    # reach a provider. No user key or ambient network credentials are passed.
    grants.mkdir()
    (grants / "model.json").write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:1/v1",
                "token": "synthetic-unused-workspace-grant-1234567890",
                "model": "deepseek-flash",
                "wall_seconds": 60,
                "max_turns": 1,
            }
        )
    )
    run_entrypoint(inputs, work, grants)
