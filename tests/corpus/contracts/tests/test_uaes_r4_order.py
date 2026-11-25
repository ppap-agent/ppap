"""Revision 4 ORD:1: a completely read order specification without a responsibility signature or stamp fails.
Visual transcriptions here are fixtures, not live model tests."""

import hashlib
import json
import re
from pathlib import Path

import fitz
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision

UAES = Path(__file__).resolve().parents[4] / "assets/uaes"
CANDIDATES = UAES / "rulebook/v1.0/candidates/m4"
ORDER = next((UAES / "samples").rglob("BV_F01RL0Y033*.pdf"))
STAMP = {"kind": "controlled_stamp", "label": "EN 受控 EN CONTROL", "state": "visible", "bbox": [0.33, 0.56, 0.88, 0.76]}
SUP = {"kind": "supplier_slot", "label": "Supplier signature", "state": "signed", "bbox": [0.08, 0.15, 0.80, 0.27]}
CUS = {"kind": "customer_slot", "label": "Customer signature", "state": "signed", "bbox": [0.08, 0.28, 0.80, 0.40]}


def run(target, reader):
    suite = _load_leaf(CANDIDATES, "uaes", CANDIDATES / "uaes/U14")
    data = target.read_bytes()
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {"ORD:1": suite.entrypoints["ORD:1"]})
    frozen = FrozenTarget("f0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), binding)
    source = FrozenInput("f0", hashlib.sha256(data).hexdigest(), "target", target.name, document_type=suite.document_type)
    snap = ExecutionSnapshot("test", "candidate", (source,), (frozen,), "test", library_revision(), {})
    runtime = CheckerRuntime()
    runtime.read_ocr_visual_facts = reader
    return run_audit(snap, {"f0": data}, {digest: suite.program_files}, runtime=runtime)["findings"][0]


def probe(objects, *, complete=True, skip=()):
    """Every sent page answered with its objects; the model's own completeness flag is `complete`; pages in `skip` unanswered."""

    def read(**kw):
        if kw["response_schema"]["name"] != "order_fields":
            return {"visible": True}
        pages = [int(n) for n in re.search(r"原页号：([\d、]+)", kw["prompt"])[1].split("、")]
        rows = [{"page": n, "slots_complete": complete, "seals_complete": complete, "objects": objects.get(n, [])} for n in pages if n not in skip]
        return {"pages": rows}

    return read


def test_every_page_looked_at_without_a_responsibility_slot_fails_even_if_the_model_doubts_corners():
    result = run(ORDER, probe({1: [STAMP]}, complete=False))
    assert result["status"] == "NG", result
    assert "已逐页看过原件全部17页，未见供方、需方责任签署栏的负责人签字" in result["suggestions"], result
    assert "EC10000417" in result["suggestions"], result


def test_every_page_looked_at_without_a_stamp_fails():
    result = run(ORDER, probe({}))
    assert result["status"] == "NG" and "已逐页看过原件全部17页，未见受控章或供应商签署栏合同章" in result["suggestions"], result


def test_a_page_left_unread_keeps_absence_open():
    result = run(ORDER, probe({1: [STAMP]}, skip={17}))
    assert result["status"] == "UNABLE" and "第17页未能看过" in result["suggestions"], result


def test_an_unconfirmed_slot_keeps_absence_open():
    odd = {**SUP, "state": "unknown"}
    result = run(ORDER, probe({1: [STAMP], 9: [odd]}))
    assert result["status"] == "UNABLE" and "第9页有未能核实的责任签署栏" in result["suggestions"], result


def signed_order(path):
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((42, 112), "Supplier signature:")
        page.insert_text((42, 174), "Customer signature:")
        document.save(path)
    return path


def test_a_pass_still_needs_every_page_reported_complete(tmp_path):
    path = signed_order(tmp_path / "order.pdf")
    assert run(path, probe({1: [SUP, CUS, STAMP]}))["status"] == "PASS"
    doubted = run(path, probe({1: [SUP, CUS, STAMP]}, complete=False))
    assert doubted["status"] == "UNABLE" and "未报告看全" in doubted["suggestions"], doubted
