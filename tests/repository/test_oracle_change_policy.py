import pytest

from scripts.check_oracle_changes import CASES, ORACLES, REVIEWED_RULES, case_bindings, human_state, verify_change

HEADER = "数据集,组合ID,文件ID,条款ID,人工真值,已知差距\n"
LI = HEADER + "li,c,f,L16:2,PASS,UNABLE 原图\n"
UAES = HEADER + "uaes,u,f,U16:2,NG,\n"


def state(**tables: str):
    """Human state of named tables: oracle_li, oracle_uaes, cases_li, reviewed_li..."""
    paths = {f"oracle_{path.split('/')[-1].split('-')[0]}": path for path in ORACLES}
    paths |= {f"cases_{path.split('/')[-1].split('-')[0]}": path for path in CASES}
    paths |= {f"reviewed_{path.split('/')[-1].split('-')[0]}": path for path in REVIEWED_RULES}
    files = {paths[name]: text for name, text in tables.items()}
    return human_state(lambda path: files.get(path, ""))


def test_migration_cannot_change_human_truth_even_with_explanation():
    with pytest.raises(ValueError, match="separate commit"):
        before, after = state(oracle_li=LI), state(oracle_li=LI.replace("PASS", "NG"))
        verify_change(before, after, [ORACLES[0], "packages/audit/src/audit/standard_suites.py"], "Oracle-Basis: Independent original source review by product owner.")


def test_truth_correction_requires_independent_human_basis():
    before, after = state(oracle_li=LI), state(oracle_li=LI.replace("PASS", "NG"))
    with pytest.raises(ValueError, match="Oracle-Basis"):
        verify_change(before, after, [ORACLES[0]], "Make the tests green")
    verify_change(before, after, [ORACLES[0], "docs/review.md"], "Correct source finding\n\nOracle-Basis: Product owner rechecked the original signature field.")


def test_every_customer_table_is_protected():
    # The UAES table is guarded like the others: a changed answer there needs the same review.
    with pytest.raises(ValueError, match="separate commit"):
        verify_change(state(oracle_uaes=UAES), state(oracle_uaes=UAES.replace("NG", "PASS")), [ORACLES[2], "packages/audit/x.py"], "Oracle-Basis: product owner reviewed it.")


def test_moving_rows_between_customer_tables_changes_nothing():
    both = LI + UAES.removeprefix(HEADER)
    verify_change(state(oracle_li=both), state(oracle_li=LI, oracle_uaes=UAES), [*ORACLES, "tests/corpus/contracts/src/corpus_contracts/oracle.py"], "Split the tables")


def test_known_gap_update_does_not_change_human_truth():
    verify_change(state(oracle_li=LI), state(oracle_li=LI.replace("UNABLE 原图", "")), [ORACLES[0], "packages/audit/src/audit/standard_suites.py"], "Implement original visual reading")


def test_reviewed_semantics_have_the_same_independent_review_workflow():
    before, after = state(reviewed_li='{"li/L1": {"rule": "before"}}'), state(reviewed_li='{"li/L1": {"rule": "after"}}')
    verify_change(before, after, [REVIEWED_RULES[0], ORACLES[0]], "Oracle-Basis: Product owner independently reviewed the changed source clause.")
    with pytest.raises(ValueError, match="separate commit"):
        verify_change(before, after, [REVIEWED_RULES[0], "packages/audit/implementation.py"], "Oracle-Basis: Product owner reviewed it.")
    verify_change(before, state(reviewed_li='{ "li/L1": { "rule": "before" } }'), [REVIEWED_RULES[0], "packages/audit/implementation.py"], "Reformat reviewed evidence")


def test_source_associations_are_reviewed_independently_of_row_or_binding_order():
    header = "数据集,组合ID,文件ID,原件,提交文件名,角色,Suite版本,Checker组,Binding\n"
    row = "nio,c,f,original.xlsx,file.xlsx,target,nio/N03,蔚来:N03,1;2\n"
    assert case_bindings(header + row) == case_bindings(header + row.replace("1;2", "2;1"))
    with pytest.raises(ValueError, match="Oracle-Basis"):
        verify_change(state(cases_nio=header + row), state(cases_nio=header + row.replace("nio/N03", "nio/N04")), [CASES[1]], "Rebind")


def test_added_or_deleted_human_truth_is_not_an_escape():
    with pytest.raises(ValueError, match="separate commit"):
        verify_change(state(), state(oracle_li=LI), [ORACLES[0], "packages/audit/new.py"], "Oracle-Basis: Product owner confirmed the new source finding.")
    with pytest.raises(ValueError, match="Oracle-Basis"):
        verify_change(state(oracle_li=LI), state(), [ORACLES[0]], "Delete difficult case")


def test_one_answer_cannot_live_in_two_tables():
    with pytest.raises(ValueError, match="duplicate_oracle_identity"):
        state(oracle_li=LI, oracle_nio=LI)
