import json

import pytest
from ppap_core.contracts import AuditInput, ConversionArtifact, RuleInput
from ppap_excel_audit_engine.checkers.agents.psw_pdf_agent import PswPDFAgent as V2PswPDFAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.psw_pdf_agent import PswPDFAgent as V3PswPDFAgent
from ppap_excel_audit_engine.runtime import bind_runtime


class VisualRuntime:
    def __init__(self, response: dict[str, object] | str | Exception):
        self.response = response

    def convert_pdf_to_excel(self, file_ref, *, enable_cache=True):
        return ConversionArtifact(success=False)

    def load_workbook_auto(self, file_ref):
        raise AssertionError("No converted workbook is available")

    def render_pdf_images(self, file_url, *, render_all_pages=True):
        return ["rendered-original-page"]

    def run_image_prompt(self, **kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        if isinstance(self.response, str):
            return self.response
        return json.dumps(self.response, ensure_ascii=False)


@pytest.fixture(params=[("v2", V2PswPDFAgent), ("v3", V3PswPDFAgent)], ids=["v2", "v3"])
def check_stamp(request, monkeypatch):
    profile, agent_type = request.param
    # V2 calls the external converter directly; V3 uses the runtime boundary.
    monkeypatch.setattr(
        "ppap_excel_audit_engine.checkers.agents.pdf_agent.convert_pdf_to_excel_auto",
        lambda *args, **kwargs: ConversionArtifact(success=False),
    )
    rules = RuleInput(
        rule_id="confirmed-psw-rules",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 11)],
    )

    def check(response):
        with bind_runtime(VisualRuntime(response)):
            agent = agent_type(AuditInput(audit_id="psw", file_url="original.pdf"), rules)
            result = agent.check(2)
        assert result is not None
        return result

    return check


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
        ("not json", "ERR"),
        ("[]", "ERR"),
    ],
    ids=["no-authorization", "no-authorization-seal", "unreadable-authorization-seal", "unreadable-stamp", "verdict-without-facts", "invalid-json", "non-object"],
)
def test_stamp_facts_distinguish_business_ng_from_unavailable_recognition(check_stamp, observation, expected):
    result = check_stamp(observation)

    assert result.status == expected


def test_model_service_failure_remains_an_execution_error(check_stamp):
    result = check_stamp(RuntimeError("model service unavailable"))

    assert result.status == "ERR"
