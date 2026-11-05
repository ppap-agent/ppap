import pytest

from scripts.check_oracle_changes import ORACLE, verify_change

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


def test_added_or_deleted_human_truth_is_not_an_escape():
    with pytest.raises(ValueError, match="separate commit"):
        verify_change("", BEFORE, [ORACLE, "packages/audit/new.py"], "Oracle-Basis: Product owner confirmed the new source finding.")
    with pytest.raises(ValueError, match="Oracle-Basis"):
        verify_change(BEFORE, "", [ORACLE], "Delete difficult case")
