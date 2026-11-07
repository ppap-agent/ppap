"""Malformed human tables must fail before any product execution."""

import csv
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest
from corpus_contracts.oracle import EXPECTED_COLUMNS, FILE_COLUMNS, build_round, load_oracle, original, programs_for, reviewed_contract
from corpus_contracts.product_round import BLOCKING, KNOWN_GAP, MATCH, OK, SETUP_FAILURE, STATUS_MISMATCH, RuleVerdict, compare_file, severity


def suite_columns(document_type, *, group=None):
    from audit.suite_migration import migrated_builtin_suite

    suite = migrated_builtin_suite("v3", document_type)
    return [suite.suite_key + "@v3", group or suite.suite_key, ";".join(suite.entrypoints)]


def write_oracle(tmp_path, *, files=None, expected=None):
    (tmp_path / "input.xlsx").write_bytes(b"original")
    tables = {
        "cases.csv": (
            FILE_COLUMNS,
            files if files is not None else [["v3", "group", "target", "input.xlsx", "upload.xlsx", "target", *suite_columns("控制计划")]],
        ),
        "oracle.csv": (EXPECTED_COLUMNS, expected if expected is not None else [["v3", "group", "target", "1", "PASS", "Rule", "Human evidence", "", "", ""]]),
    }
    for name, (headers, rows) in tables.items():
        with (tmp_path / name).open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(rows)
    return {"cases": tmp_path / "cases.csv", "oracle": tmp_path / "oracle.csv", "root": tmp_path}


def test_whole_combination_keeps_reference_without_asserting_it(tmp_path):
    target = ["v3", "group", "target", "input.xlsx", "upload.xlsx", "target", *suite_columns("控制计划")]
    drawing = ["v3", "group", "drawing", "input.xlsx", "drawing.xlsx", "reference", "", "", ""]
    paths = write_oracle(tmp_path, files=[target, drawing])
    (case,) = load_oracle(**paths)
    assert [file.file_id for file in case.files] == ["target", "drawing"]
    assert case.files[1].expected == {}


@pytest.mark.parametrize(
    "change,match",
    [
        ("missing-original", "Missing original"),
        ("invalid-route", "Invalid Suite revision"),
        ("missing-group", "Invalid Suite revision"),
        ("duplicate-file", "Duplicate file"),
        ("duplicate-rule", "Duplicate expectation"),
        ("orphan-rule", "no target"),
        ("blank-status", "Invalid expectation"),
        ("unknown-status", "Invalid expectation"),
        ("zero-assertions", "zero assertions"),
        ("formula", "formulas"),
        ("gap-without-reason", "Invalid known gap"),
        ("gap-equals-truth", "Invalid known gap"),
    ],
)
def test_invalid_table_cannot_pass(tmp_path, change, match):
    files = [["v3", "group", "target", "input.xlsx", "upload.xlsx", "target", *suite_columns("控制计划")]]
    expected = [["v3", "group", "target", "1", "PASS", "Rule", "Evidence", "", "", ""]]
    if change == "missing-original":
        files[0][3] = "missing.xlsx"
    if change == "invalid-route":
        files[0][6] = "missing.json"
    if change == "missing-group":
        files[0][7] = ""
    if change == "duplicate-file":
        files.append(files[0][:])
    if change == "duplicate-rule":
        expected.append(expected[0][:])
    if change == "orphan-rule":
        expected[0][2] = "other"
    if change == "blank-status":
        expected[0][4] = ""
    if change == "unknown-status":
        expected[0][4] = "MAYBE"
    if change == "zero-assertions":
        files.append(["v3", "group", "other", "input.xlsx", "other.xlsx", "target", *suite_columns("控制计划")])
    if change == "formula":
        expected[0][4] = '=IF(1,"PASS","NG")'
    if change == "gap-without-reason":
        expected[0][9] = "UNABLE"
    if change == "gap-equals-truth":
        expected[0][9] = "PASS 说明"
    with pytest.raises(ValueError, match=match):
        load_oracle(**write_oracle(tmp_path, files=files, expected=expected))


def test_all_real_tables_bind_every_assertion_to_a_declared_rule():
    for case in load_oracle():
        work, blobs, totals = build_round(case)
        assert len(work.inputs) == len(case.files) == len(blobs)
        assert all(totals[file.file_id] >= len(file.expected) > 0 for file in case.files if file.role == "target")


def test_readable_source_selectors_freeze_content_addressed_execution():
    for case in load_oracle():
        work, _, _ = build_round(case)
        files = {file.file_id: file for file in case.files}
        programs = programs_for(work)
        for target in work.targets:
            file = files[target.file_version_id]
            suite = file.suite()
            assert file.suite_revision.startswith(suite.suite_key + "@v")
            assert target.suite_revision_id == "sha256:" + suite.revision_digest
            assert target.suite_digest == suite.revision_digest
            assert programs[target.binding.checker_digest] == suite.program_files


def test_implementation_edit_preserves_reviewed_meaning_with_a_new_execution_identity():
    from audit.suite_migration import migrated_builtin_suite

    suite = migrated_builtin_suite("v3", "控制计划")
    updated = replace(suite, files={**suite.files, "checker.py": suite.files["checker.py"] + b"\n# implementation clarification\n"})
    assert reviewed_contract(updated) == reviewed_contract(suite)
    assert updated.revision_digest != suite.revision_digest


def test_human_provenance_is_retained_with_the_expectation(tmp_path):
    expected = [["v3", "group", "target", "1", "PASS", "Rule", "Human evidence", "Reviewer", "2026-11-07", ""]]
    (case,) = load_oracle(**write_oracle(tmp_path, expected=expected))
    answer = case.files[0].expected["1"]
    assert (answer.confirmed_by, answer.confirmed_on) == ("Reviewer", "2026-11-07")


def test_a_case_cannot_silently_run_on_another_checker_group(tmp_path):
    files = [["v3", "group", "target", "input.xlsx", "upload.xlsx", "target", *suite_columns("控制计划", group="PPAP:潜在过程失效模式及后果分析")]]
    with pytest.raises(ValueError, match="Suite revision belongs to PPAP:控制计划"):
        load_oracle(**write_oracle(tmp_path, files=files))


def test_same_document_in_pdf_and_excel_has_one_truth():
    truths = defaultdict(dict)
    for case in load_oracle():
        for file in case.files:
            if file.expected:
                truths[(case.dataset, Path(file.filename).stem)][Path(file.filename).suffix.lower()] = {rule: item.status for rule, item in file.expected.items()}
    pairs = [formats for formats in truths.values() if ".pdf" in formats and len(formats) > 1]
    assert pairs
    for formats in pairs:
        first = formats[".pdf"]
        assert all(other == first for other in formats.values())


def test_original_lookup_rejects_an_unknown_name():
    assert original("5-模具验收报告-上海亚大.xls").is_file()
    with pytest.raises(ValueError, match="Expected one original"):
        original("no-such-file.xlsx")


@pytest.mark.parametrize(
    "kind,truth,actual,gap,expected",
    [
        (MATCH, "NG", "NG", "", OK),
        (MATCH, "NG", "NG", "UNABLE", BLOCKING),
        (STATUS_MISMATCH, "NG", "PASS", "", BLOCKING),
        (STATUS_MISMATCH, "PASS", "NG", "", BLOCKING),
        (STATUS_MISMATCH, "NG", "UNABLE", "", BLOCKING),
        (STATUS_MISMATCH, "PASS", "UNABLE", "", BLOCKING),
        (STATUS_MISMATCH, "NG", "UNABLE", "UNABLE", KNOWN_GAP),
        (STATUS_MISMATCH, "UNABLE", "PASS", "", BLOCKING),
        (STATUS_MISMATCH, "UNABLE", "NG", "", BLOCKING),
        (STATUS_MISMATCH, "NA", "UNABLE", "", BLOCKING),
        (STATUS_MISMATCH, "NG", "PASS", "PASS", KNOWN_GAP),
        (STATUS_MISMATCH, "NG", "PASS", "UNABLE", BLOCKING),
        ("execution_failure", "NG", "ERR", "", BLOCKING),
        ("missing_rule", "NG", None, "", BLOCKING),
    ],
)
def test_difference_from_truth_has_a_severity(kind, truth, actual, gap, expected):
    assert severity(RuleVerdict("file", "1", kind, truth, actual), gap) == expected


def test_duplicate_result_id_is_not_silently_overwritten():
    result = {"coverage": {"rule_items": [{"file_version_id": "v1", "id": "U08:1"}, {"file_version_id": "v1", "id": "U08:1"}]}}
    (verdict,) = compare_file(file_name="file", version_id="v1", expected={"U08:1": "PASS"}, result=result, setup_error=None)
    assert verdict.kind == SETUP_FAILURE
