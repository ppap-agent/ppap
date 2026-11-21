"""An incomplete Suite binding never turns an unbound clause into PASS."""

import hashlib
import json

from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import predefined_directory_suites


def test_u08_bytes_do_not_pass_unbound_clauses_or_unsupported_formats(tmp_path):
    from agent.execution.audit_runtime import run_audit_process
    from openpyxl import Workbook

    suite = next(item for item in predefined_directory_suites() if item.source_path == "uaes/U08")
    rules = json.loads(suite.files["rules.json"])
    # Exercise an explicitly incomplete binding independently of the installed
    # leaf's current business maturity.
    rules["checker_binding"].pop("U08:2")
    program = {name: suite.files[name] for name in ("checker.py", "resources.py")}
    digest = checker_digest(program)
    binding = RuleBinding(suite.suite_key, digest, rules["checker_binding"])
    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    path = tmp_path / "control-plan.xlsx"
    book.save(path)
    book.close()
    original = path.read_bytes()
    unsupported_file = tmp_path / "control-plan.txt"
    unsupported_file.write_bytes(b"Unsupported carrier")

    def run(raw: bytes, filename: str):
        source = FrozenInput("target", hashlib.sha256(raw).hexdigest(), "target", filename)
        target = FrozenTarget("target", "revision", suite.suite_key, "c" * 64, rules, binding)
        snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", {})
        return run_audit_process(snapshot, {"target": raw}, {digest: program})

    excel = run(original, "control-plan.xlsx")
    coverage = {row["clause_id"]: row["coverage"] for row in excel["coverage"]}
    unbound = {clause for clause, state in coverage.items() if state == "unsupported"}
    assert unbound == {row["id"] for row in rules["rules"]} - set(rules["checker_binding"])
    assert unbound
    assert not any(row["clause_id"] in unbound and row["status"] == "PASS" for row in excel["findings"])
    unsupported = run(unsupported_file.read_bytes(), "control-plan.txt")
    assert unsupported["findings"]
    assert {row["status"] for row in unsupported["findings"]} == {"UNABLE"}
    assert not any(row["coverage"] == "unsupported" and row["clause_id"] in {item["clause_id"] for item in unsupported["findings"]} for row in unsupported["coverage"])
