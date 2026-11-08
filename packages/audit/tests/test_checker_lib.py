"""Shared checker_lib identity, import lint, and direct helper delegation."""

import json
import shutil
from pathlib import Path

import pytest
from audit.checker_core import ValidationRecord, checker_digest
from audit.checker_lib.imports import directory_import_violations
from audit.standard_suites import hash_library, library_files, library_revision, load_predefined_checkers

_AUDIT = Path(__file__).resolve().parents[1] / "src" / "audit"
_CHECKERS = _AUDIT / "checkers"
_V5 = _CHECKERS / "v5"


def test_installed_leaves_are_outside_the_library_fingerprint():
    relative = [path.relative_to(_AUDIT).as_posix() for path in library_files("audit", _AUDIT)]
    assert "checker_lib/excel.py" in relative
    assert "checker_lib/pdf.py" in relative
    assert "checker_lib/visual.py" in relative
    assert "checkers/v2/__init__.py" in relative
    assert "checkers/v3/__init__.py" in relative
    assert "checkers/v4/__init__.py" in relative
    assert not any(item.startswith("checkers/v5/") for item in relative)
    revision = library_revision()
    assert len(revision) == 64 and revision == library_revision()


def test_copying_v5_to_v6_changes_the_checker_digest_only(tmp_path):
    root = tmp_path / "checkers"
    shutil.copytree(_V5, root / "v5")
    shutil.copytree(_V5, root / "v6")
    loaded = {suite.source_path: suite for suite in load_predefined_checkers(root)}
    before = library_revision()
    assert checker_digest(loaded["v5/UAES/U08"].program_files) == checker_digest(loaded["v6/UAES/U08"].program_files)
    checker = root / "v6" / "UAES" / "U08" / "checker.py"
    checker.write_text(checker.read_text(encoding="utf-8") + "\n# v6 business only\n", encoding="utf-8")
    library_revision.cache_clear()
    assert library_revision() == before
    again = {suite.source_path: suite for suite in load_predefined_checkers(root)}
    assert checker_digest(again["v5/UAES/U08"].program_files) == checker_digest(loaded["v5/UAES/U08"].program_files)
    assert checker_digest(again["v6/UAES/U08"].program_files) != checker_digest(loaded["v6/UAES/U08"].program_files)
    assert again["v5/UAES/U08"].revision_digest == loaded["v5/UAES/U08"].revision_digest
    assert again["v6/UAES/U08"].revision_digest != loaded["v6/UAES/U08"].revision_digest


def test_public_library_edits_change_the_fingerprint_and_mark_retest(tmp_path):
    root = tmp_path / "audit"
    (root / "checker_lib").mkdir(parents=True)
    excel = root / "checker_lib" / "excel.py"
    excel.write_text("VALUE = 1\n", encoding="utf-8")
    (root / "engine").mkdir()
    (root / "engine" / "routing.py").write_text("PROFILE = 'explicit'\n", encoding="utf-8")
    legacy = root / "checkers" / "v4"
    legacy.mkdir(parents=True)
    (legacy / "__init__.py").write_text("LEGACY = 1\n", encoding="utf-8")
    leaf = root / "checkers" / "v5" / "UAES" / "U08"
    leaf.mkdir(parents=True)
    (leaf / "checker.py").write_text("def create_audit():\n    return 1\n", encoding="utf-8")
    before = hash_library((("audit", root),))
    shutil.copytree(root / "checkers" / "v5", root / "checkers" / "v6")
    assert hash_library((("audit", root),)) == before
    v6 = root / "checkers" / "v6" / "UAES" / "U08" / "checker.py"
    original = v6.read_bytes()
    v6.write_text(v6.read_text(encoding="utf-8") + "\n# v6 only\n", encoding="utf-8")
    assert hash_library((("audit", root),)) == before
    assert checker_digest({"checker.py": original}) != checker_digest({"checker.py": v6.read_bytes()})
    (legacy / "__init__.py").write_text("LEGACY = 2\n", encoding="utf-8")
    after_legacy = hash_library((("audit", root),))
    assert after_legacy != before
    excel.write_text("VALUE = 2\n", encoding="utf-8")
    after_public = hash_library((("audit", root),))
    assert after_public != after_legacy
    names = [path.relative_to(root).as_posix() for path in library_files("audit", root)]
    assert "checker_lib/excel.py" in names and "checkers/v4/__init__.py" in names
    assert not any(name.startswith("checkers/v5/") or name.startswith("checkers/v6/") for name in names)
    # publish_official refuses samples_not_rerun with official_suite_validation_required.
    record = ValidationRecord("a" * 64, "b" * 64, "c" * 64, {}, before)
    same = dict(suite_digest="a" * 64, rule_digest="b" * 64, checker_digest="c" * 64, related_suite_digests={}, library_revision=before)
    assert record.status(**same) == {"state": "verified", "samples_not_rerun": False}
    assert record.status(**{**same, "library_revision": after_public}) == {"state": "verified", "samples_not_rerun": True}


def test_installed_directory_leaves_have_no_legacy_checker_imports():
    assert directory_import_violations(_CHECKERS) == ()
    checker = (_V5 / "UAES" / "U08" / "checker.py").read_text(encoding="utf-8")
    assert "audit.checker_lib.excel" in checker
    assert "audit.checker_lib.tables" in checker
    assert "audit.checker_lib.evidence" in checker
    assert "resolve_engine_agent_class" not in checker
    assert "audit.checkers" not in checker


def test_import_lint_flags_static_and_dynamic_legacy_imports(tmp_path):
    leaf = tmp_path / "v6" / "UAES" / "U08"
    leaf.mkdir(parents=True)
    (leaf / "checker.py").write_text(
        "\n".join(
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
                "# import audit.checkers.v4.visual_facts",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (leaf / "resources.py").write_text("from audit.checkers.v3.excel_agent import ExcelAgent\nRESOURCE = {}\n", encoding="utf-8")
    clean = tmp_path / "v6" / "UAES" / "U99"
    clean.mkdir()
    (clean / "checker.py").write_text("# from audit.checkers.v4.uaes import Agent\nfrom audit.checker_lib.excel import load_workbook\n", encoding="utf-8")
    legacy = tmp_path / "v4"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("import audit.checkers.v2.base_agent\n", encoding="utf-8")
    problems = "\n".join(directory_import_violations(tmp_path))
    assert "audit.checkers.v4.uaes" in problems
    assert "audit.checkers.v2.base_agent" in problems
    assert "audit.checkers" in problems
    assert "audit.standard_library.check_clause" in problems
    assert "audit.checkers.v3.excel_agent" in problems
    assert "audit.checkers.v4.uaes.native" in problems
    assert "non_literal_import" in problems
    assert "resources.py" in problems
    assert "U99" not in problems
    assert "v4/__init__.py" not in problems
    assert "audit.checker_lib.excel" not in problems


@pytest.mark.parametrize(
    "source",
    [
        "from audit.standard_library import check_clause\n\ndef create_audit(audit_file, rule, group_context=None):\n    return check_clause\n",
        "import importlib\nname = 'audit.checker_lib.excel'\n\ndef create_audit(audit_file, rule, group_context=None):\n    return importlib.import_module(name)\n",
    ],
)
def test_loader_rejects_imports_the_lint_reports(tmp_path, source):
    leaf = tmp_path / "v6" / "UAES" / "U08"
    leaf.mkdir(parents=True)
    (leaf / "checker.py").write_text(source, encoding="utf-8")
    rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}}
    (leaf / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    with pytest.raises(ValueError, match="predefined_legacy_checker_import"):
        load_predefined_checkers(tmp_path)


def test_loading_directory_leaves_does_not_fill_the_v3_overlay():
    from audit.checkers.v3 import V3_OVERLAY_DOCX_AGENTS, V3_OVERLAY_EXCEL_AGENTS, V3_OVERLAY_PDF_AGENTS

    before = (dict(V3_OVERLAY_EXCEL_AGENTS), dict(V3_OVERLAY_PDF_AGENTS), dict(V3_OVERLAY_DOCX_AGENTS))
    load_predefined_checkers()
    assert (dict(V3_OVERLAY_EXCEL_AGENTS), dict(V3_OVERLAY_PDF_AGENTS), dict(V3_OVERLAY_DOCX_AGENTS)) == before


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


def test_pdf_and_visual_helpers_delegate_to_existing_readers(monkeypatch):
    def tables(source, *, spec=None):
        return ("tables", source, spec)

    def convert(source, output_path, *, spec=None):
        return ("convert", source, output_path, spec)

    def region(*args, **kwargs):
        return {"args": args, "whole_field": kwargs.get("whole_field")}

    monkeypatch.setattr("documents.pdf.extract_pdf_tables", tables)
    monkeypatch.setattr("documents.pdf.convert_pdf_to_excel", convert)
    monkeypatch.setattr("audit.engine.checkers.excel_field_evidence.observe_excel_region", region)
    from audit.checker_lib.pdf import convert_pdf_to_excel, read_tables
    from audit.checker_lib.visual import observe_excel_region

    assert read_tables("in.pdf", spec="spec") == ("tables", "in.pdf", "spec")
    assert convert_pdf_to_excel("in.pdf", "out.xlsx", spec="spec") == ("convert", "in.pdf", "out.xlsx", "spec")
    assert observe_excel_region("sheet", whole_field=False) == {"args": ("sheet",), "whole_field": False}


def test_structure_evidence_records_the_frozen_file_without_a_pass():
    from types import SimpleNamespace

    from audit.checker_lib.evidence import content_sha256, json_locator, structure_evidence, version_id

    audit_file = SimpleNamespace(file_metadata={"file_version_id": " v1 ", "sha256": "ab" * 32}, audit_id="other")
    assert version_id(audit_file) == "v1"
    assert len(content_sha256(audit_file)) == 64
    locator = json.loads(json_locator({"kind": "xlsx_range", "start": "A1", "end": "B2"}, file_version_id="v1", source_sha256="ab" * 32))
    assert locator["file_version_id"] == "v1" and locator["source_sha256"] == "ab" * 32
    assert structure_evidence(source_format="xlsx", sources=()).used == "none"
