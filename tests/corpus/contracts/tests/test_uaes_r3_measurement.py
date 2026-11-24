"""Only revision-3 leaf code, run with frozen originals and no model grants."""

import hashlib
import json
from pathlib import Path

import pytest
from agent.execution.audit_runtime import run_audit
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _CHECKERS_ROOT, _load_leaf, library_revision

BASE = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = _CHECKERS_ROOT
NORMAL = next((BASE / "samples").glob("F01RD0YA40*")) / "测试文件"
IR = next(NORMAL.glob("01-1 */*207*.xls"))
OTS = next(NORMAL.glob("17 */*OTS*.pdf"))


def run(leaf, clauses, target, references=()):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    body = json.loads(suite.files["rules.json"])
    values = [(target, suite.document_type), *references]
    inputs, sources = [], {}
    for i, (path, kind) in enumerate(values):
        key = f"file{i}"
        raw = path.read_bytes()
        inputs.append(FrozenInput(key, hashlib.sha256(raw).hexdigest(), "target" if i == 0 else "reference", path.name, document_type=kind))
        sources[key] = raw
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {k: v for k, v in suite.entrypoints.items() if k in clauses})
    frozen = FrozenTarget("file0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, body, binding)
    snapshot = ExecutionSnapshot("test", "candidate", tuple(inputs), (frozen,), "test", library_revision(), {})
    findings = run_audit(snapshot, sources, {digest: suite.program_files})["findings"]
    return {f["clause_id"]: f for f in findings}


def test_ir_excludes_only_different_explicit_release_part():
    finding = run("U01", ["IR:1"], IR, [(OTS, "E11")])["IR:1"]
    assert finding["status"] == "NG"
    assert all(value in finding["suggestions"] for value in ("F01R10YA40", "F01RD0YA4001", "8.118"))
    assert any(json.loads(s["locator"]).get("file_version_id") == "file1" and s["excerpt"] == "F01RD0YA4001" for s in finding["evidence"]["sources"])


def test_cgk_normal_original_under_formal_msa_binding():
    cgk = next(NORMAL.glob("06 */MSA/A40-气动量仪-Cgk.xls"))
    clauses = [f"MSA:{i}" for i in range(1, 7)]
    findings = run("U05", clauses, cgk)
    assert set(findings) == set(clauses)
    assert findings["MSA:1"]["status"] == "NA"
    assert findings["MSA:5"]["status"] == "PASS" and "1.69" in findings["MSA:5"]["suggestions"]
    assert all(f["status"] != "ERR" for f in findings.values())


@pytest.mark.parametrize(("filename", "evidence"), [("MSA-2-CMM.xls", "67.5±0.15"), ("MSA-3-粗糙度仪.xls", "粗糙度RZmax25"), ("MSA-4-气动量仪.xls", "Φ57H7")])
def test_776_msa_specific_characteristic_not_covered_by_a_different_study(filename, evidence):
    root = next((BASE / "samples").glob("F01R10Y776*"))
    reports = list((root / "测试文件/06 测量系统分析评价").glob("*.xls"))
    target = next(p for p in reports if p.name == filename)
    references = [(p, "测量系统分析评价") for p in reports if p != target] + [(next(root.glob("比对文件/*/*PGQP1*")), "E12")]
    finding = run("U05", ["MSA:6"], target, references)["MSA:6"]
    assert finding["status"] == "NG" and evidence in finding["suggestions"]
    cells = [(json.loads(s["locator"]), s["excerpt"]) for s in finding["evidence"]["sources"]]
    assert any(loc["cell"] == "I6" and loc["file_version_id"] == "file0" for loc, _ in cells)
    assert any(value == evidence and loc["sheet"] == "PGQP1" for loc, value in cells)
