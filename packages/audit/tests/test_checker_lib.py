"""Shared checker_lib identity and direct helper delegation."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from audit.checker_core import ValidationRecord, checker_digest
from audit.standard_suites import hash_library, library_files, library_revision, load_predefined_checkers

_AUDIT = Path(__file__).resolve().parents[1] / "src" / "audit"
_CHECKERS = _AUDIT / "checkers"
_COMPANIES = ("uaes", "li", "nio")


def test_public_fingerprint_includes_company_implementations_not_company_leaves():
    import documents

    relative = [path.relative_to(_AUDIT).as_posix() for path in library_files("audit", _AUDIT)]
    assert "checker_lib/excel.py" in relative
    assert "checker_lib/visual.py" in relative
    assert "engine/checkers/pdf_field_region.py" in relative
    assert "checkers/__init__.py" in relative
    assert "checker_lib/li/psw_excel_agent.py" in relative
    assert any(item.startswith("checker_lib/nio/") for item in relative)
    assert any(item.startswith("checker_lib/uaes/") for item in relative)
    assert not any(item.split("/")[1] in _COMPANIES for item in relative if item.startswith("checkers/"))
    # The public fingerprint is the audit and documents source trees, nothing else.
    assert library_revision() == hash_library((("audit", _AUDIT), ("documents", Path(documents.__file__).resolve().parent)))


def test_editing_a_company_leaf_changes_only_its_checker_digest(tmp_path):
    root = tmp_path / "checkers"
    for company in _COMPANIES:
        shutil.copytree(_CHECKERS / company, root / company, ignore=shutil.ignore_patterns("__pycache__"))
    loaded = {suite.source_path: suite for suite in load_predefined_checkers(root)}
    checker = root / "uaes" / "U08" / "checker.py"
    checker.write_text(checker.read_text(encoding="utf-8") + "\n# business change only\n", encoding="utf-8")
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
    shared = root / "checker_lib" / "excel.py"
    shared.write_text("VALUE = 1\n", encoding="utf-8")
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
    shared.write_text("VALUE = 2\n", encoding="utf-8")
    assert hash_library((("audit", root),)) != after
    names = [path.relative_to(root).as_posix() for path in library_files("audit", root)]
    assert "checker_lib/li/agent.py" in names and "checkers/uaes/U08/checker.py" not in names


def test_workbook_reader_keeps_cached_values_or_formulas(tmp_path):
    from audit.checker_lib.excel import load_workbook
    from openpyxl import Workbook

    path = tmp_path / "plan.xlsx"
    book = Workbook()
    book.active["A2"] = "=1+1"
    book.save(path)
    book.close()
    formulas = load_workbook(str(path), formulas=True)
    assert formulas.active["A2"].value == "=1+1"
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
