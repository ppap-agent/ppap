"""Synthetic UAES R4 workbooks run through the frozen candidate entry point."""

import hashlib
import json
from pathlib import Path

from agent.execution.audit_runtime import run_audit
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import Workbook

UAES = Path(__file__).resolve().parents[5] / "assets/uaes"
LEAVES = UAES / "rulebook/v1.0/candidates/m4"


def check(path, leaf, clause, peers=None):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]})
    target = FrozenTarget("0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), binding)
    files = [(path, suite.document_type)] + [(p, typ) for typ, paths in (peers or {}).items() for p in paths]
    sources, inputs = {}, []
    for i, (p, typ) in enumerate(files):
        raw = p.read_bytes()
        sources[str(i)] = raw
        inputs.append(FrozenInput(str(i), hashlib.sha256(raw).hexdigest(), "reference" if i else "target", p.name, document_type=typ))
    snapshot = ExecutionSnapshot("r4", "candidate", tuple(inputs), (target,), "offline", library_revision(), {})
    finding = run_audit(snapshot, sources, {digest: suite.program_files})["findings"][0]
    if finding["status"] == "PASS":
        assert finding["checks_performed"] > 0, finding
    return finding


def book(path, sheets):
    wb = Workbook()
    for i, (title, rows) in enumerate(sheets.items()):
        ws = wb.create_sheet(title) if i else wb.active
        ws.title = title
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path
