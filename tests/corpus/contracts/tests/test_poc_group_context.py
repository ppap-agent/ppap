from pathlib import Path

from corpus_contracts.poc_oracle import OracleRuleExpectation, PocCase, build_group_context_for_bundle


def test_bundle_context_infers_type_from_filename_not_seq() -> None:
    case = PocCase(
        file_name="控制计划.xlsx",
        file_seq="9",
        file_path=Path("控制计划.xlsx"),
        bundle_id="bundle",
        expected={1: OracleRuleExpectation(1, "NG", "规则", "")},
    )

    context = build_group_context_for_bundle([case])

    assert list(context.by_doc_type) == ["控制计划"]
    assert context.by_doc_type["控制计划"][0].audit_id == "控制计划.xlsx"
