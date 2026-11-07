import pytest

from scripts.check_oracle_changes import CASES, ORACLE, REVIEWED_RULES, case_bindings, verify_case_bindings_change, verify_change, verify_reviewed_rules_change

BEFORE = "数据集,组合ID,文件ID,条款ID,人工真值,已知差距\nv4,c,f,U16:2,PASS,UNABLE 原图\n"
AFTER = BEFORE.replace("PASS", "NG")


def test_migration_cannot_change_human_truth_even_with_explanation():
    with pytest.raises(ValueError, match="separate commit"):
        verify_change(BEFORE, AFTER, [ORACLE, "packages/audit/src/audit/standard_suites.py"], "Oracle-Basis: Independent original source review by product owner.")


def test_truth_correction_requires_independent_human_basis():
    with pytest.raises(ValueError, match="Oracle-Basis"):
        verify_change(BEFORE, AFTER, [ORACLE], "Make the tests green")
    verify_change(BEFORE, AFTER, [ORACLE, "docs/review.md"], "Correct source finding\n\nOracle-Basis: Product owner rechecked the original signature field.")


def test_known_gap_update_does_not_change_human_truth():
    verify_change(BEFORE, BEFORE.replace("UNABLE 原图", ""), [ORACLE, "packages/audit/src/audit/standard_suites.py"], "Implement original visual reading")


def test_reviewed_semantics_have_the_same_independent_review_workflow():
    verify_reviewed_rules_change(
        '{"suite": {"rule": "before"}}',
        '{"suite": {"rule": "after"}}',
        [REVIEWED_RULES, ORACLE],
        "Oracle-Basis: Product owner independently reviewed the changed source clause.",
    )
    verify_reviewed_rules_change('{"suite": {"rule": "same"}}', '{ "suite": { "rule": "same" } }', [REVIEWED_RULES, "packages/audit/implementation.py"], "Reformat reviewed evidence")


def test_source_associations_are_reviewed_independently_of_row_or_binding_order():
    header = "数据集,组合ID,文件ID,原件,提交文件名,角色,Suite版本,Checker组,Binding\n"
    row = "v3,c,f,original.xlsx,file.xlsx,target,PPAP:控制计划@v3,PPAP:控制计划,1;2\n"
    assert case_bindings(header + row) == case_bindings(header + row.replace("1;2", "2;1"))
    verify_case_bindings_change(
        header + row,
        header + row.replace("@v3", "@v2"),
        [CASES, ORACLE],
        "Oracle-Basis: Product owner independently confirmed the answers against the selected source revision.",
    )


def test_added_or_deleted_human_truth_is_not_an_escape():
    with pytest.raises(ValueError, match="separate commit"):
        verify_change("", BEFORE, [ORACLE, "packages/audit/new.py"], "Oracle-Basis: Product owner confirmed the new source finding.")
    with pytest.raises(ValueError, match="Oracle-Basis"):
        verify_change(BEFORE, "", [ORACLE], "Delete difficult case")
