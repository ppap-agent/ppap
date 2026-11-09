"""An uncertain authorization remains unknown and names the evidence gap."""

import pytest
from audit.engine.checkers.psw_scope import _exists_authorization
from audit.engine.checkers.psw_visual_evidence import evaluate_psw_observation


@pytest.mark.parametrize("authorization", [(None, None), (True, None), (None, True)])
def test_unresolved_authorization_requests_manual_source_review_without_a_verdict(authorization):
    result = evaluate_psw_observation(3, {"psw_stamp_present": False, "authorization_table_present": authorization[0], "authorization_company_stamp_present": authorization[1]})
    assert result.status == "UNABLE"
    assert result.checks_performed == 0
    assert "无法确认" in result.suggestions and "人工核对授权页" in result.suggestions


def test_unknown_page_is_not_replaced_by_an_absent_authorization_on_another_page():
    authorization = _exists_authorization(
        [{"authorization_table_present": False, "authorization_company_stamp_present": False}, {"authorization_table_present": None, "authorization_company_stamp_present": None}], True
    )
    assert authorization == (None, None)
    result = evaluate_psw_observation(3, {"psw_stamp_present": False, "authorization_table_present": authorization[0], "authorization_company_stamp_present": authorization[1]})
    assert result.status == "UNABLE" and "授权页" in result.suggestions


@pytest.mark.parametrize("table,stamp,expected", [(False, False, "NG"), (True, False, "NG"), (True, True, "PASS")])
def test_established_authorization_facts_keep_their_existing_business_result(table, stamp, expected):
    result = evaluate_psw_observation(3, {"psw_stamp_present": False, "authorization_table_present": table, "authorization_company_stamp_present": stamp})
    assert result.status == expected
    assert result.checks_performed == 1
