"""Only validated V4 source identities may extend legacy type/carrier routing."""

import pytest
from audit.contracts import AuditInput, AuditResultItem, RuleInput
from audit.engine import engine
from audit.kinds import PpapDocumentType
from audit.presets import preset_rule_body


@pytest.mark.parametrize(("entry", "filename"), [("U09", "包装方案.docx"), ("U13", "样品.png")])
def test_source_e_type_reaches_the_existing_resolver_without_changing_the_old_enum(monkeypatch, entry, filename):
    routed = []
    selected = type("RegisteredChecker", (), {})

    def resolve(kind, path, profile, **scope):
        routed.append((kind, profile, scope))
        return selected

    def execute(agent, audit, rule, group, runtime):
        assert agent is selected
        return [AuditResultItem(classification="contract", item="route", priority="P0", status="UNABLE", suggestions="Routing proof only, not a document verdict.")]

    monkeypatch.setattr(engine, "resolve_engine_agent_class", resolve)
    monkeypatch.setattr(engine, "_execute_agent", execute)
    rule = RuleInput(rule_id="source", **preset_rule_body("UAES", entry))
    result = engine.run_audit(AuditInput(audit_id="file", file_url=filename), rule)
    assert result[0].status == "UNABLE"
    assert routed == [(PpapDocumentType.UNKNOWN, "v4", {"preset_key": entry, "preset_customer": "UAES"})]
    assert PpapDocumentType.from_string(rule.type) is PpapDocumentType.UNKNOWN


@pytest.mark.parametrize("change", [{"type": "unregistered"}, {"customers": ["理想"]}])
def test_wrong_source_identity_cannot_authorize_an_image_carrier(monkeypatch, change):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid source must not reach a Checker")

    monkeypatch.setattr(engine, "resolve_engine_agent_class", forbidden)
    body = {**preset_rule_body("UAES", "U13"), **change}
    result = engine.run_audit(AuditInput(audit_id="file", file_url="sample.png"), RuleInput(rule_id="source", **body))
    assert result[0].status == "ERR"


def test_non_photo_source_does_not_relax_the_global_carrier_gate():
    with pytest.raises(ValueError, match="不支持"):
        engine.run_audit(AuditInput(audit_id="file", file_url="drawing.png"), RuleInput(rule_id="source", **preset_rule_body("UAES", "U09")))


def test_photo_scope_does_not_authorize_an_executable_carrier():
    with pytest.raises(ValueError, match="不支持"):
        engine.run_audit(AuditInput(audit_id="file", file_url="sample.exe"), RuleInput(rule_id="source", **preset_rule_body("UAES", "U13")))
