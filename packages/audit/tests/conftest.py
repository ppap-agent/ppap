"""Pytest-native fixtures for audit-owned directory checker tests."""

from __future__ import annotations

import hashlib
import json
import sys
from io import BytesIO
from types import ModuleType, SimpleNamespace

import pytest
from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, checker_digest, validate_results
from audit.contracts import AuditInput, RuleInput
from audit.standard_library import check_factory
from audit.standard_suites import _CHECKERS_ROOT, _load_leaf
from openpyxl import Workbook


@pytest.fixture
def text_model(monkeypatch):
    """Answer direct text model calls with a stub that reads the request and returns JSON text."""

    def install(stub):
        from documents.ai import AIResult

        def run(task, *, prompt, instructions="", max_tokens=1000, timeout=900, **kwargs):
            raw = stub(operation=task, system_prompt=instructions, user_prompt=prompt, max_tokens=max_tokens, request_timeout=timeout, **kwargs)
            return AIResult(json.loads(raw) if isinstance(raw, str) else raw)

        monkeypatch.setattr("documents.ai.run_json", run)

    return install


@pytest.fixture
def nio_execute(tmp_path, monkeypatch):
    """Execute one NIO frozen leaf through its production adapter and Core."""

    def execute(entry, clause, sheets=None, *, content=None, suffix=".xlsx"):
        # Exercise the production leaf parser without reading unrelated leaves
        # while other workers are editing them in the shared checkout.
        root = _CHECKERS_ROOT.resolve()
        suite = _load_leaf(root, "v5", "蔚来", root / "v5" / "蔚来" / entry)
        resources = ModuleType("resources")
        exec(compile(suite.files["resources.py"], f"{suite.source_path}/resources.py", "exec"), resources.__dict__)
        monkeypatch.setitem(sys.modules, "resources", resources)
        checker = ModuleType(f"nio_directory_{entry}")
        exec(compile(suite.files["checker.py"], f"{suite.source_path}/checker.py", "exec"), checker.__dict__)
        body = json.loads(suite.files["rules.json"])
        body["rules"] = [row for row in body["rules"] if row["id"] == clause]
        body["checker_binding"] = {clause: suite.entrypoints[clause]}
        assert len(body["rules"]) == 1
        if content is None:
            workbook = Workbook()
            workbook.remove(workbook.active)
            for title, rows in sheets.items():
                worksheet = workbook.create_sheet(title)
                for row in rows:
                    worksheet.append(row)
            output = BytesIO()
            workbook.save(output)
            workbook.close()
            content = output.getvalue()
        path = tmp_path / (entry + suffix)
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        audit = AuditInput(audit_id="frozen-nio-source", file_url=str(path), customer="蔚来", project_id="project", file_metadata={"sha256": digest})
        rule = RuleInput(rule_id="frozen-nio-rule", **body)
        target = FrozenTarget(
            audit.audit_id, "suite-revision", suite.suite_key, suite.revision_digest, body, RuleBinding(suite.suite_key, checker_digest(suite.program_files), body["checker_binding"])
        )
        opened = []
        context = SimpleNamespace(audit_file=audit, rule=rule, target=target, group_context=None, cache={}, on_close=opened.append, clause_id=clause)
        try:
            result = check_factory(context, checker.create_audit).model_dump(mode="json")
            result["evidence_file_version_ids"] = [audit.audit_id]
            checked = validate_results([result], target=target, inputs=[FrozenInput(audit.audit_id, digest, "target", path.name)])
            assert checked["coverage"] == [{"file_version_id": audit.audit_id, "clause_id": clause, "coverage": "mapped"}]
            return checked["findings"][0]
        finally:
            for owner in opened:
                owner.close()

    return execute
