import pytest
from audit.engine.checkers.psw_visual_evidence import evaluate_psw_observation


@pytest.fixture
def check_stamp():
    return lambda observation: evaluate_psw_observation(3, observation)


def test_empty_company_seal_position_is_not_a_stamped_authorization(check_stamp):
    result = check_stamp(
        {
            "psw_stamp_ends_with_company": False,
            "authorization_table_present": True,
            "authorization_company_stamp_present": False,
            "classification": "印章信息检查",
            "item": "印章信息检查",
            "priority": "P0",
            "status": "PASS",
            "suggestions": "印章不以公司结尾，但授权表中有公司章位置。",
        }
    )

    assert result.status == "NG"


@pytest.mark.parametrize(
    "observation",
    [
        {"psw_stamp_ends_with_company": True},
        {"psw_stamp_ends_with_company": True, "authorization_table_present": False, "authorization_company_stamp_present": False},
        {"psw_stamp_ends_with_company": False, "authorization_table_present": True, "authorization_company_stamp_present": True},
        {"authorization_table_present": True, "authorization_company_stamp_present": True},
    ],
    ids=["company-stamp-only", "company-stamp-without-authorization", "stamped-authorization", "stamped-authorization-without-psw-facts"],
)
def test_either_accepted_stamp_branch_is_sufficient(check_stamp, observation):
    result = check_stamp({**observation, "status": "NG"})

    assert result.status == "PASS"


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        ({"psw_stamp_ends_with_company": False, "authorization_table_present": False}, "NG"),
        ({"psw_stamp_ends_with_company": False, "authorization_company_stamp_present": False}, "NG"),
        ({"psw_stamp_ends_with_company": False, "authorization_table_present": True}, "UNABLE"),
        ({"psw_stamp_ends_with_company": "unreadable", "authorization_table_present": False}, "UNABLE"),
        ({"status": "PASS"}, "UNABLE"),
    ],
    ids=["no-authorization", "no-authorization-seal", "unreadable-authorization-seal", "unreadable-stamp", "verdict-without-facts"],
)
def test_stamp_facts_distinguish_business_ng_from_unavailable_recognition(check_stamp, observation, expected):
    result = check_stamp(observation)

    assert result.status == expected
