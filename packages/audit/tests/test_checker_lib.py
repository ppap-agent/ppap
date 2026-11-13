"""Shared checker_lib identity, import lint, and direct helper delegation."""

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from audit.checker_core import ValidationRecord, checker_digest
from audit.checker_lib.imports import import_violations
from audit.standard_suites import hash_library, library_files, library_revision, load_predefined_checkers

_AUDIT = Path(__file__).resolve().parents[1] / "src" / "audit"
_CHECKERS = _AUDIT / "checkers"
_COMPANIES = ("uaes", "li", "nio")


def test_public_fingerprint_includes_company_implementations_not_company_leaves():
    relative = [path.relative_to(_AUDIT).as_posix() for path in library_files("audit", _AUDIT)]
    assert "checker_lib/excel.py" in relative
    assert "checker_lib/pdf.py" in relative
    assert "checker_lib/visual.py" in relative
    assert "engine/checkers/pdf_field_region.py" in relative
    assert "checkers/__init__.py" in relative
    assert "checker_lib/li/psw_excel_agent.py" in relative
    assert any(item.startswith("checker_lib/nio/") for item in relative)
    assert any(item.startswith("checker_lib/uaes/") for item in relative)
    assert not any(item.split("/")[1] in _COMPANIES for item in relative if item.startswith("checkers/"))
    revision = library_revision()
    assert len(revision) == 64 and revision == library_revision()


def test_editing_a_company_leaf_changes_only_its_checker_digest(tmp_path):
    root = tmp_path / "checkers"
    for company in _COMPANIES:
        shutil.copytree(_CHECKERS / company, root / company, ignore=shutil.ignore_patterns("__pycache__"))
    loaded = {suite.source_path: suite for suite in load_predefined_checkers(root)}
    before = library_revision()
    checker = root / "uaes" / "U08" / "checker.py"
    checker.write_text(checker.read_text(encoding="utf-8") + "\n# business change only\n", encoding="utf-8")
    library_revision.cache_clear()
    assert library_revision() == before
    again = {suite.source_path: suite for suite in load_predefined_checkers(root)}
    assert checker_digest(again["uaes/U07"].program_files) == checker_digest(loaded["uaes/U07"].program_files)
    assert checker_digest(again["uaes/U08"].program_files) != checker_digest(loaded["uaes/U08"].program_files)
    assert again["uaes/U07"].revision_digest == loaded["uaes/U07"].revision_digest
    assert again["uaes/U08"].revision_digest != loaded["uaes/U08"].revision_digest


def test_implementation_sources_move_the_public_fingerprint_and_company_leaves_do_not(tmp_path):
    root = tmp_path / "audit"
    for folder in ("checker_lib/li", "checkers/uaes/U08"):
        (root / folder).mkdir(parents=True)
    implementation = root / "checker_lib" / "li" / "agent.py"
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    leaf = root / "checkers" / "uaes" / "U08" / "checker.py"
    leaf.write_text("VALUE = 1\n", encoding="utf-8")
    before = hash_library((("audit", root),))
    leaf.write_text("VALUE = 2\n", encoding="utf-8")
    assert hash_library((("audit", root),)) == before
    implementation.write_text("VALUE = 2\n", encoding="utf-8")
    after = hash_library((("audit", root),))
    assert after != before
    same = dict(suite_digest="a" * 64, rule_digest="b" * 64, checker_digest="c" * 64, related_suite_digests={})
    record = ValidationRecord("a" * 64, "b" * 64, "c" * 64, {}, before)
    assert record.status(**same, library_revision=before) == {"state": "verified", "samples_not_rerun": False}
    assert record.status(**same, library_revision=after) == {"state": "verified", "samples_not_rerun": True}
    names = [path.relative_to(root).as_posix() for path in library_files("audit", root)]
    assert "checker_lib/li/agent.py" in names and "checkers/uaes/U08/checker.py" not in names


def test_implementation_edit_expires_proofs_and_company_leaf_edit_does_not(tmp_path):
    import documents
    from audit.checker_core import rule_digest
    from audit.standard_suites import predefined_directory_suites

    suites = predefined_directory_suites()
    audit_copy = tmp_path / "audit"
    shutil.copytree(_AUDIT, audit_copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    documents_root = Path(documents.__file__).resolve().parent
    entries = (("audit", audit_copy), ("documents", documents_root))
    before = hash_library(entries)
    assert before == library_revision()
    leaves = {suite.suite_key: checker_digest(suite.program_files) for suite in suites}

    leaf_dir = audit_copy / "checkers" / "uaes" / "U08"
    leaf_files = {"checker.py": (leaf_dir / "checker.py").read_bytes(), "resources.py": (leaf_dir / "resources.py").read_bytes()}
    leaf_before = checker_digest(leaf_files)
    checker = leaf_dir / "checker.py"
    checker.write_bytes(checker.read_bytes() + b"\n# company leaf only\n")
    assert hash_library(entries) == before
    leaf_after = checker_digest({"checker.py": checker.read_bytes(), "resources.py": leaf_files["resources.py"]})
    assert leaf_after != leaf_before

    implementation = audit_copy / "checker_lib" / "li" / "psw_excel_agent.py"
    implementation.write_bytes(implementation.read_bytes() + b"\n# implementation only\n")
    after_implementation = hash_library(entries)
    assert after_implementation != before
    for suite in suites:
        rules = json.loads(suite.files["rules.json"])
        appendix = tuple(json.loads(suite.files.get("appendix.json", b"[]")))
        digested_rules = rule_digest(rules, appendix)
        record = ValidationRecord(suite.revision_digest, digested_rules, leaves[suite.suite_key], {}, before)
        same = dict(suite_digest=suite.revision_digest, rule_digest=digested_rules, checker_digest=leaves[suite.suite_key], related_suite_digests={})
        assert record.status(**same, library_revision=before) == {"state": "verified", "samples_not_rerun": False}
        assert record.status(**same, library_revision=after_implementation) == {"state": "verified", "samples_not_rerun": True}

    shared = audit_copy / "checker_lib" / "excel.py"
    shared.write_bytes(shared.read_bytes() + b"\n# shared library\n")
    assert hash_library(entries) != after_implementation
    assert checker_digest({"checker.py": checker.read_bytes(), "resources.py": leaf_files["resources.py"]}) == leaf_after


def test_import_lint_flags_static_and_dynamic_legacy_imports():
    source = "\n".join(
        [
            "import audit.checkers.v4.uaes",
            "from audit.checkers.v2.base_agent import BaseAgent",
            "from audit import checkers",
            "from audit.standard_library import check_clause, check_factory",
            "from importlib import import_module",
            "import importlib",
            "import_module('audit.checkers.v3.excel_agent')",
            "importlib.import_module('audit.checkers.v4.uaes.native')",
            "import_module('audit.checker_lib.excel')",
            "name = 'audit.checkers.v2'",
            "import_module(name)",
            "import audit.checker_lib.nio.excel_agent",
            "# import audit.engine.checkers.visual_facts",
            "",
        ]
    )
    problems = "\n".join(import_violations(ast.parse(source)))
    assert "audit.checkers.v4.uaes" in problems
    assert "audit.checkers.v2.base_agent" in problems
    assert "audit.checkers" in problems
    assert "audit.standard_library.check_clause" in problems
    assert "audit.checkers.v3.excel_agent" in problems
    assert "audit.checkers.v4.uaes.native" in problems
    assert "audit.checker_lib.nio.excel_agent" in problems
    assert "non_literal_import" in problems
    assert "audit.checker_lib.excel" not in problems
    assert "audit.engine.checkers.visual_facts" not in problems
    assert import_violations(ast.parse("# from audit.checkers.v4.uaes import Agent\nfrom audit.checker_lib.excel import load_workbook\n")) == ()


@pytest.mark.parametrize(
    "source",
    [
        "from audit.standard_library import check_clause\n\ndef create_audit(audit_file, rule, group_context=None):\n    return check_clause\n",
        "import importlib\nname = 'audit.checker_lib.excel'\n\ndef create_audit(audit_file, rule, group_context=None):\n    return importlib.import_module(name)\n",
    ],
)
def test_loader_rejects_imports_the_lint_reports(tmp_path, source):
    leaf = tmp_path / "uaes" / "U08"
    leaf.mkdir(parents=True)
    (leaf / "checker.py").write_text(source, encoding="utf-8")
    rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}}
    (leaf / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    with pytest.raises(ValueError, match="predefined_legacy_checker_import"):
        load_predefined_checkers(tmp_path)


def test_table_and_workbook_helpers_read_without_a_checker_class(tmp_path):
    from audit.checker_lib.excel import column_letter, load_workbook
    from audit.checker_lib.tables import cell_span, find_headers, is_blank
    from openpyxl import Workbook

    path = tmp_path / "plan.xlsx"
    book = Workbook()
    book.active["A1"] = "工序名称/操作描述"
    book.active["B1"] = "机器/装置/夹具/工装"
    book.active["A2"] = "=1+1"
    book.active["B2"] = "压机"
    book.save(path)
    book.close()
    cached = load_workbook(str(path))
    formulas = load_workbook(str(path), formulas=True)
    found = find_headers(
        cached.active,
        {"operation": ("工序名称/操作描述",), "resource": ("机器/装置/夹具/工装",)},
        max_rows=40,
        max_cols=30,
    )
    assert found == (1, {"operation": 1, "resource": 2})
    assert formulas.active["A2"].value == "=1+1"
    assert cell_span(1, 2, 1, 2) == ("A1", "B2")
    assert column_letter(2) == "B"
    assert is_blank("  ") and not is_blank("压机")
    cached.close()
    formulas.close()


def test_whole_field_observation_reads_the_rendered_field_region(tmp_path):
    script = tmp_path / "whole_field.py"
    sheet = tmp_path / "sheet.xlsx"
    script.write_text(
        """
import json
import sys

import fitz
from openpyxl import Workbook, load_workbook

document = fitz.open()
page = document.new_page(width=595, height=842)
page.insert_text((72, 80), "SUBMISSION RESULTS", fontsize=16)
page.insert_text((72, 420), "DECLARATION", fontsize=16)
pdf_bytes = document.tobytes()
document.close()

book = Workbook()
book.active.title = "Sheet1"
book.active["A1"] = "x"
book.save(sys.argv[1])
book.close()

import documents.excel.visual as visual
import documents.ocr.env as env

visual.render_xlsx_pdf = lambda *args, **kwargs: pdf_bytes

def configured():
    return "configured"

env.ocr_model_name = configured
env.ocr_openai_api_key = configured
env.ocr_openai_base_url = configured
env.ocr_policy_identity = lambda: "policy"

from audit.checker_lib.visual import observe_excel_region
import audit.engine.checkers.excel_field_evidence as evidence
import audit.engine.checkers.pdf_field_region as region

seen = {}
original = region.pdf_field_region

def wrapped(page, fields):
    box = original(page, fields)
    seen["box"] = [box.y0, box.y1, page.rect.y1]
    seen["module"] = original.__module__
    return box

region.pdf_field_region = wrapped

def fake_ocr(images, fields, record, context=None):
    seen["png"] = list(images[0].data[:4])
    seen["context"] = context or ""
    return {name: "yes" for name in fields}

evidence.observe_ocr_images = fake_ocr
opened = load_workbook(sys.argv[1])
from audit.contracts import AuditInput

result = observe_excel_region(
    AuditInput(audit_id="a1", file_url=sys.argv[1], file_metadata={"file_version_id": "v1"}),
    opened.active,
    None,
    {"dimensional_checked": "dimensions"},
    ("SUBMISSION RESULTS",),
    cache={},
    witness=lambda *args, **kwargs: None,
    whole_field=True,
)
opened.close()
print(json.dumps({"result": result, "seen": seen}))
""".lstrip(),
        encoding="utf-8",
    )
    completed = subprocess.run([sys.executable, str(script), str(sheet)], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["result"] == {"dimensional_checked": "yes"}
    assert payload["seen"]["png"] == [137, 80, 78, 71]
    assert "SUBMISSION RESULTS" in payload["seen"]["context"]
    assert payload["seen"]["module"] == "audit.engine.checkers.pdf_field_region"
    assert payload["seen"]["box"][1] < payload["seen"]["box"][2] - 10


def test_structure_evidence_records_the_frozen_file_without_a_pass():
    from types import SimpleNamespace

    from audit.checker_lib.evidence import content_sha256, json_locator, structure_evidence, version_id

    audit_file = SimpleNamespace(file_metadata={"file_version_id": " v1 ", "sha256": "ab" * 32}, audit_id="other")
    assert version_id(audit_file) == "v1"
    assert len(content_sha256(audit_file)) == 64
    locator = json.loads(json_locator({"kind": "xlsx_range", "start": "A1", "end": "B2"}, file_version_id="v1", source_sha256="ab" * 32))
    assert locator["file_version_id"] == "v1" and locator["source_sha256"] == "ab" * 32
    assert structure_evidence(source_format="xlsx", sources=()).used == "none"
