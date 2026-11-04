"""Shadow facts use existing Checkers without changing formal audit results."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import fitz
import pytest
from audit.checkers.v4.paddle_evidence import PaddleShadowSession, ShadowTarget, paddle_shadow_scope
from audit.checkers.v4.uaes.bindings import BINDINGS
from audit.checkers.v4.uaes.control_plan import UaesControlPlanAgent
from audit.contracts import AuditInput, RuleInput


def source(tmp_path, *, text=""):
    path = tmp_path / "source.pdf"
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=595, height=842)
            if text:
                page.insert_text((40, 50), text, fontname="china-s", fontsize=12)
        doc.save(path)
    return path


def response():
    return {
        "dataInfo": {"pages": [{"width": 595, "height": 842}]},
        "layoutParsingResults": [
            {
                "prunedResult": {
                    "layout_det_res": {
                        "boxes": [
                            {"label": "seal", "coordinate": [10, 10, 60, 60]},
                            {"label": "table", "coordinate": [80, 80, 400, 300]},
                        ]
                    },
                    "seal_res_list": [{"rec_texts": ["存档资料"]}],
                    "parsing_res_list": [],
                    "table_res_list": [{"pred_html": "<table><tr><td>零件号</td><td>P-42</td></tr></table>", "cell_box_list": [[80, 80, 400, 300]]}],
                }
            }
        ],
    }


class Service:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def analyze_page(self, path, page, *, timeout=None):
        self.calls.append((str(path), page))
        if self.failure:
            raise self.failure("DO NOT EXPOSE secret or document contents")
        return response()


def agent(path, *, project="project-a"):
    return UaesControlPlanAgent(AuditInput(audit_id="frozen-version", file_url=str(path), project_id=project), RuleInput(rule_id="rule", rule_profile="v4", preset_key="U08"))


def targets(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return [ShadowTarget("frozen-version", digest, clause, 2) for clause, method in BINDINGS["v4"].items() if clause.startswith("U08:") and method in {"rule_3", "rule_6"}]


def test_existing_checker_shadow_preserves_formal_result_and_reuses_page(tmp_path):
    path = source(tmp_path)
    service = Service()
    checker = agent(path)
    # Both selected clauses lack native fields, so formal execution stops before
    # any existing visual/model boundary. Only shadow extraction is requested.
    baseline = [checker.check(2).model_dump(), checker.check(5).model_dump()]
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        actual = [checker.check(2).model_dump(), checker.check(5).model_dump()]
    assert actual == baseline
    assert len(service.calls) == 1 and service.calls[0][1] == 2
    assert [item.state for item in session.observations] == ["known", "known"]
    assert session.observations[0].facts[0]["detected"] is True
    assert session.observations[1].facts[0]["value"] == "P-42"
    assert all(json.loads(s.locator)["page"] == 2 for item in session.observations for s in item.evidence.sources)
    assert session.closed


def test_native_field_is_sufficient_and_makes_zero_requests(tmp_path):
    path = source(tmp_path, text="零件号：P-NATIVE")
    service = Service()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert service.calls == []
    assert session.observations[0].state == "skipped"
    assert session.observations[0].reason == "native_sufficient"


@pytest.mark.parametrize("failure", [TimeoutError, OSError, ValueError])
def test_failed_attempt_is_not_resent_by_another_rule(tmp_path, failure):
    path = source(tmp_path)
    service = Service(failure)
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        checker = agent(path)
        checker.check(2)
        checker.check(5)
    assert len(service.calls) == 1
    assert [item.state for item in session.observations] == ["error", "error"]
    assert "secret" not in repr(session.observations)


def test_cancelled_scope_never_sends(tmp_path):
    path = source(tmp_path)
    service = Service()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path), cancelled=lambda: True)
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert service.calls == []
    assert session.observations[0].state == "error"


def test_wrong_project_or_changed_original_cannot_supply_facts(tmp_path):
    path = source(tmp_path)
    for mode in ("project", "content"):
        service = Service()
        session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
        if mode == "content":
            path.write_bytes(path.read_bytes() + b"\n")
        with paddle_shadow_scope(session):
            agent(path, project="other" if mode == "project" else "project-a").check(5)
        assert service.calls == []
        assert session.observations[0].state == "error"


def test_default_scope_does_not_request_ocr(tmp_path):
    path = source(tmp_path)
    result = agent(path).check(5)
    assert result.status == "UNABLE"


def test_concurrent_consumers_share_one_attempt(tmp_path):
    path = source(tmp_path)
    service = Service()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: session.observe(agent(path), "rule_6"), range(2)))
    assert len(service.calls) == 1
    assert all(item.state == "known" for item in result)


def test_late_response_after_cancel_does_not_publish_sources(tmp_path):
    path = source(tmp_path)
    cancelled = False

    class CancellingService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            nonlocal cancelled
            result = super().analyze_page(path, page, timeout=timeout)
            cancelled = True
            return result

    service = CancellingService()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path), cancelled=lambda: cancelled)
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert len(service.calls) == 1
    assert session.observations[0].state == "error"
    assert session.observations[0].evidence.sources == ()


def test_concurrent_different_pages_reserve_the_request_budget(tmp_path):
    path = source(tmp_path)
    service = Service()
    selected = targets(path)
    selected[0] = ShadowTarget(selected[0].file_version_id, selected[0].source_sha256, selected[0].clause_id, 1)
    session = PaddleShadowSession(service, project_id="project-a", targets=selected, max_requests=1)
    with paddle_shadow_scope(session):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda m: session.observe(agent(path), m), ["rule_3", "rule_6"]))
    assert session.request_count == len(service.calls) == 1
    assert sorted(item.state for item in results) == ["error", "known"]
    assert any(item.reason == "request_budget_exhausted" for item in results)


def test_source_replaced_during_request_discards_the_late_result(tmp_path):
    path = source(tmp_path)

    class ReplacingService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            result = super().analyze_page(path, page, timeout=timeout)
            with open(path, "ab") as original:
                original.write(b"\n")
            return result

    service = ReplacingService()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert session.observations[0].reason == "source_changed"
    assert session.observations[0].evidence.sources == ()


def test_bad_original_page_does_not_consume_an_external_request(tmp_path):
    path = source(tmp_path)
    t = targets(path)[0]
    service = Service()
    session = PaddleShadowSession(service, project_id="project-a", targets=[ShadowTarget(t.file_version_id, t.source_sha256, t.clause_id, 99)])
    with paddle_shadow_scope(session):
        agent(path).check(2)
    assert session.request_count == 0 and service.calls == []
    assert session.observations[0].reason == "source_page_invalid"


def test_elapsed_scope_time_does_not_discard_valid_returned_facts(tmp_path, monkeypatch):
    from audit.checkers.v4 import paddle_evidence

    clock = [0.0]
    monkeypatch.setattr(paddle_evidence, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    path = source(tmp_path)

    class SlowService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            assert timeout == 20
            value = super().analyze_page(path, page, timeout=timeout)
            clock[0] = 700
            return value

    session = PaddleShadowSession(SlowService(), project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert session.observations[0].state == "known"
    assert session.observations[0].evidence.sources
    assert session.observations[0].elapsed_seconds == 700


def test_bad_table_does_not_erase_a_good_seal(tmp_path):
    path = source(tmp_path)

    class BadTable(Service):
        def analyze_page(self, path, page, *, timeout=None):
            raw = super().analyze_page(path, page, timeout=timeout)
            raw["layoutParsingResults"][0]["prunedResult"]["table_res_list"][0]["pred_html"] = '<table><tr><td colspan="2"'
            return raw

    service = BadTable()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        checker = agent(path)
        checker.check(2)
        checker.check(5)
    assert len(service.calls) == 1
    seal, table = session.observations
    assert seal.state == "known" and seal.facts[0]["text"] == ["存档资料"]
    assert seal.evidence.sources
    assert table.state == "error" and table.evidence.sources == ()
    assert any(d.code == "paddle.table.invalid" for d in table.evidence.diagnostics)


def test_earlier_known_facts_survive_later_cancellation(tmp_path):
    path = source(tmp_path)
    stopped = False
    session = PaddleShadowSession(Service(), project_id="project-a", targets=targets(path), cancelled=lambda: stopped)
    with paddle_shadow_scope(session):
        checker = agent(path)
        checker.check(2)
        stopped = True
        checker.check(5)
    assert [x.state for x in session.observations] == ["known", "error"]
    assert session.observations[0].evidence.sources
    assert session.observations[1].evidence.sources == ()


def test_scope_close_during_inflight_request_does_not_publish_sources(tmp_path):
    path = source(tmp_path)
    entered, release = Event(), Event()

    class WaitingService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            value = super().analyze_page(path, page, timeout=timeout)
            entered.set()
            assert release.wait(5)
            return value

    session = PaddleShadowSession(WaitingService(), project_id="project-a", targets=targets(path))
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with paddle_shadow_scope(session):
                pending = pool.submit(session.observe, agent(path), "rule_6")
                assert entered.wait(5)
        finally:
            release.set()
        observation = pending.result(timeout=5)
    assert observation.reason == "scope_closed"
    assert observation.evidence.sources == ()


def test_service_configuration_cannot_change_within_a_scope(tmp_path):
    path = source(tmp_path)
    service = Service()
    service.timeout = 30
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        checker = agent(path)
        checker.check(2)
        service.timeout = 300
        checker.check(5)
    assert len(service.calls) == 1
    assert session.observations[0].state == "known"
    assert session.observations[1].reason == "service_changed"


@pytest.mark.parametrize("label", ["零件号 Part Number", "零件号/最新更改水平"])
def test_bilingual_field_uses_the_existing_native_header_semantics(tmp_path, label):
    path = source(tmp_path)

    class BilingualService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            raw = super().analyze_page(path, page, timeout=timeout)
            table = raw["layoutParsingResults"][0]["prunedResult"]["table_res_list"][0]
            table["pred_html"] = table["pred_html"].replace("零件号", label)
            return raw

    session = PaddleShadowSession(BilingualService(), project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert session.observations[0].state == "known"
    assert session.observations[0].facts[0]["value"] == "P-42"


def test_unlocatable_seal_is_unknown_not_a_technical_verdict(tmp_path):
    path = source(tmp_path)

    class Unlocated(Service):
        def analyze_page(self, path, page, *, timeout=None):
            raw = super().analyze_page(path, page, timeout=timeout)
            raw["layoutParsingResults"][0]["prunedResult"]["layout_det_res"]["boxes"][0]["coordinate"] = [10, 10, 600, 900]
            return raw

    session = PaddleShadowSession(Unlocated(), project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(2)
    assert session.observations[0].state == "unknown"
    assert session.observations[0].evidence.sources == ()


@pytest.mark.parametrize(
    ("markup", "state", "value", "reason"),
    [
        ('<table><tr><td colspan="2">零件号</td><td>P-42</td></tr></table>', "known", "P-42", "facts_observed"),
        ('<table><tr><td>备注</td><td colspan="2">零件号</td><td colspan="2">P-42</td></tr></table>', "known", "P-42", "facts_observed"),
        ("<table><tr><td>零件号</td><td></td><td>P-42</td></tr></table>", "unknown", None, "fact_unconfirmed"),
        ('<table><tr><td colspan="2">零件号</td><td>P-42</td></tr><tr><td colspan="2">零件号</td><td>P-43</td></tr></table>', "unknown", None, "field_ambiguous"),
    ],
)
def test_field_follows_the_label_merge_extent_without_skipping_unmerged_blanks(tmp_path, markup, state, value, reason):
    path = source(tmp_path)

    class MergedLabelService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            raw = super().analyze_page(path, page, timeout=timeout)
            raw["layoutParsingResults"][0]["prunedResult"]["table_res_list"][0]["pred_html"] = markup
            return raw

    service = MergedLabelService()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert session.request_count == len(service.calls) == 1
    observed = session.observations[0]
    assert observed.state == state
    assert observed.reason == reason
    if state == "known":
        assert observed.facts[0]["value"] == value
        assert observed.evidence.sources
    else:
        assert observed.facts == ()
        assert observed.evidence.sources == ()


def psw_rules(profile="v2"):
    from audit.presets import preset_rule_body

    if profile == "v4":
        return RuleInput(rule_id="frozen-psw-rule", **preset_rule_body("理想", "L17"))
    return RuleInput(
        rule_id="frozen-psw-rule",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": f"original-clause-{i}", "category": f"PSW条款{i}", "rule_to_check": f"冻结PSW条款{i}", "type_of_rule": "image"} for i in range(1, 11)],
    )


def psw_targets(path, rule, numbers=(3, 6)):
    from audit.checkers.v4.paddle_evidence import psw_shadow_clause_id

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return [ShadowTarget("frozen-version", digest, psw_shadow_clause_id(rule, number), 2) for number in numbers]


class PswRuntime:
    """Only external conversion/model boundaries are controlled in this test."""

    def __init__(self):
        self.prompt_calls = []

    def convert_pdf_to_excel(self, file_ref, **kwargs):
        from documents.contracts import ConversionArtifact

        return ConversionArtifact(success=False, diagnostics={"error_code": "controlled_missing_grid"})

    def render_pdf_images(self, file_url, **kwargs):
        return ["eA=="]

    def run_image_prompt(self, **kwargs):
        self.prompt_calls.append(kwargs)
        return json.dumps({"status": "UNABLE"})


def psw_agent(path, rule):
    from unittest.mock import patch

    from audit.engine.routing import resolve_engine_agent_class
    from audit.engine.runtime import get_runtime
    from audit.kinds import PpapDocumentType

    cls = resolve_engine_agent_class(PpapDocumentType.PSW, str(path), rule.rule_profile, preset_key=rule.preset_key, preset_customer="理想" if rule.preset_key else None)
    runtime = get_runtime()
    assert isinstance(runtime, PswRuntime)
    # V2 converts at this external boundary directly; V3 uses bind_runtime.
    with patch("audit.checkers.v2.pdf_agent.convert_pdf_to_excel_auto", side_effect=runtime.convert_pdf_to_excel):
        return cls(AuditInput(audit_id="frozen-version", file_url=str(path), project_id="project-a"), rule)


@pytest.mark.parametrize("profile", ["v2", "v3", "v4"])
def test_existing_psw_checkers_shadow_preserves_formal_result_and_reuses_page(tmp_path, profile):
    from audit.engine.runtime import bind_runtime

    path, rule, runtime = source(tmp_path), psw_rules(profile), PswRuntime()
    service = Service()
    with bind_runtime(runtime):
        checker = psw_agent(path, rule)
        baseline = [checker.check(2).model_dump(), checker.check(5).model_dump()]
        formal_calls = len(runtime.prompt_calls)
        session = PaddleShadowSession(service, project_id="project-a", targets=psw_targets(path, rule))
        with paddle_shadow_scope(session):
            actual = [checker.check(2).model_dump(), checker.check(5).model_dump()]
    assert actual == baseline
    assert len(runtime.prompt_calls) == formal_calls * 2
    assert session.request_count == len(service.calls) == 1
    assert [item.state for item in session.observations] == ["known", "known"]
    assert [item.reused for item in session.observations] == [False, True]
    assert session.observations[1].facts[0]["value"] == "P-42"
    assert all(json.loads(item.locator)["page"] == 2 for observation in session.observations for item in observation.evidence.sources)


@pytest.mark.parametrize("profile", ["v2", "v3", "v4"])
def test_psw_native_part_number_is_sufficient_without_paddle(tmp_path, profile):
    from audit.engine.runtime import bind_runtime

    path, rule = source(tmp_path, text="理想汽车零件号：P-NATIVE"), psw_rules(profile)
    service = Service()
    with bind_runtime(PswRuntime()):
        checker = psw_agent(path, rule)
    session = PaddleShadowSession(service, project_id="project-a", targets=psw_targets(path, rule, (6,)))
    with paddle_shadow_scope(session):
        observation = session.observe_psw(checker, 6)
    assert session.request_count == 0 and service.calls == []
    assert observation.state == "skipped" and observation.reason == "native_sufficient"


@pytest.mark.parametrize("number", [5, 7, 8, 9, 10])
def test_psw_unsupported_signature_and_selection_facts_are_unknown_without_dispatch(tmp_path, number):
    from audit.engine.runtime import bind_runtime

    path, rule, service = source(tmp_path), psw_rules(), Service()
    with bind_runtime(PswRuntime()):
        checker = psw_agent(path, rule)
    session = PaddleShadowSession(service, project_id="project-a", targets=psw_targets(path, rule, (number,)))
    with paddle_shadow_scope(session):
        observation = session.observe_psw(checker, number)
    assert service.calls == [] and session.request_count == 0
    assert observation.state == "unknown" and observation.reason == "unsupported_visual_fact"
    assert observation.evidence.sources == () and observation.facts == ()


def test_psw_rule_nine_formal_result_is_unchanged_and_shadow_does_not_add_model_calls(tmp_path):
    from audit.engine.runtime import bind_runtime

    path, rule, runtime, service = source(tmp_path), psw_rules(), PswRuntime(), Service()
    with bind_runtime(runtime):
        checker = psw_agent(path, rule)
        baseline = checker.check(8).model_dump()
        session = PaddleShadowSession(service, project_id="project-a", targets=psw_targets(path, rule, (9,)))
        with paddle_shadow_scope(session):
            actual = checker.check(8).model_dump()
    assert actual == baseline
    assert runtime.prompt_calls == []
    assert service.calls == [] and session.request_count == 0
    assert session.observations[0].state == "unknown"


def test_psw_failed_page_attempt_is_not_repeated_by_the_field_consumer(tmp_path):
    from audit.engine.runtime import bind_runtime

    path, rule, service = source(tmp_path), psw_rules(), Service(TimeoutError)
    with bind_runtime(PswRuntime()):
        checker = psw_agent(path, rule)
    session = PaddleShadowSession(service, project_id="project-a", targets=psw_targets(path, rule))
    with paddle_shadow_scope(session):
        first = session.observe_psw(checker, 3)
        second = session.observe_psw(checker, 6)
    assert session.request_count == len(service.calls) == 1
    assert [first.state, second.state] == ["error", "error"]
    assert second.reused


@pytest.mark.parametrize("change", ["rule_id", "rule_profile", "clause_body", "document_type", "preset_key"])
def test_psw_late_result_cannot_be_attributed_to_changed_frozen_rules(tmp_path, change):
    from audit.engine.runtime import bind_runtime

    path, rule = source(tmp_path), psw_rules()
    with bind_runtime(PswRuntime()):
        checker = psw_agent(path, rule)

    class ChangingRule(Service):
        def analyze_page(self, path, page, *, timeout=None):
            raw = super().analyze_page(path, page, timeout=timeout)
            if change == "clause_body":
                rule.rules[2]["rule_to_check"] = "different frozen clause"
            elif change == "document_type":
                rule.type = "控制计划"
            elif change == "preset_key":
                rule.preset_key = "U08"
            else:
                setattr(rule, change, "another-rule" if change == "rule_id" else "v3")
            return raw

    session = PaddleShadowSession(ChangingRule(), project_id="project-a", targets=psw_targets(path, rule, (3,)))
    with paddle_shadow_scope(session):
        observation = session.observe_psw(checker, 3)
    assert session.request_count == 1
    assert observation.state == "error" and observation.reason == "rule_version_mismatch"
    assert observation.evidence.sources == ()


def test_uaes_psw_existing_checker_consumes_selected_stamp_and_field_shadows(tmp_path):
    from audit.checkers.v4.paddle_evidence import psw_shadow_clause_id
    from audit.checkers.v4.uaes.warrant import UaesWarrantAgent
    from audit.presets import preset_rule_body

    path = source(tmp_path)
    rule = RuleInput(rule_id="frozen-uaes-psw", **preset_rule_body("UAES", "U17"))
    checker = UaesWarrantAgent(AuditInput(audit_id="frozen-version", file_url=str(path), project_id="project-a"), rule)
    baseline = [checker.check(0).model_dump(), checker.check(2).model_dump()]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    selected = [ShadowTarget("frozen-version", digest, psw_shadow_clause_id(rule, number), 2) for number in (1, 3)]
    service = Service()
    session = PaddleShadowSession(service, project_id="project-a", targets=selected)
    with paddle_shadow_scope(session):
        actual = [checker.check(0).model_dump(), checker.check(2).model_dump()]
    assert actual == baseline
    assert session.request_count == len(service.calls) == 1
    assert [item.state for item in session.observations] == ["known", "known"]


@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("change", ["elapsed", "cancel", "source"])
def test_native_read_must_revalidate_the_scope_before_skip_or_dispatch(tmp_path, monkeypatch, known, change):
    from audit.checkers.v4 import paddle_evidence as module
    from audit.checkers.v4.uaes.native import NativeFacts, UnknownFact

    path, service, clock = source(tmp_path), Service(), [0.0]
    cancelled = [False]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def delayed_native(self, labels):
        if change == "elapsed":
            clock[0] = 700
        elif change == "cancel":
            cancelled[0] = True
        else:
            path.write_bytes(path.read_bytes() + b"\n")
        if known:
            return "P-NATIVE"
        raise UnknownFact("native field unconfirmed")

    monkeypatch.setattr(NativeFacts, "field", delayed_native)
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path), cancelled=lambda: cancelled[0])
    with paddle_shadow_scope(session):
        observation = session.observe(agent(path), "rule_6")
    if change == "elapsed":
        assert observation.state == ("skipped" if known else "known")
        assert session.request_count == len(service.calls) == (0 if known else 1)
    else:
        assert service.calls == [] and session.request_count == 0
        assert observation.state == "error"
        assert observation.reason == {"cancel": "cancelled", "source": "source_changed"}[change]
        assert observation.evidence.sources == ()


class RegionWordService(Service):
    def analyze_page(self, path, page, *, timeout=None):
        raw = super().analyze_page(path, page, timeout=timeout)
        pruned = raw["layoutParsingResults"][0]["prunedResult"]
        pruned["overall_ocr_res"] = {"rec_texts": ["部门", "公司名称（公司章）"], "rec_boxes": [[20, 20, 40, 30], [45, 45, 80, 70]]}
        return raw


@pytest.mark.parametrize("profile", ["v2", "v3", "v4"])
def test_psw_region_words_keep_separate_origins_without_changing_formal_results(tmp_path, profile):
    from audit.engine.runtime import bind_runtime

    path, rule, runtime = source(tmp_path), psw_rules(profile), PswRuntime()
    service = RegionWordService()
    with bind_runtime(runtime):
        checker = psw_agent(path, rule)
        baseline = checker.check(2).model_dump()
        session = PaddleShadowSession(service, project_id="project-a", targets=psw_targets(path, rule, (3,)))
        with paddle_shadow_scope(session):
            actual = checker.check(2).model_dump()
    assert actual == baseline and runtime.prompt_calls == []
    assert session.request_count == len(service.calls) == 1
    observation = session.observations[0]
    assert observation.state == "known"
    fact = observation.facts[0]
    assert fact["detected"] is True and fact["text"] == ["存档资料"]
    fragments = fact["region_text_fragments"]
    assert [(word["source"], word["index"], word["text"]) for word in fragments] == [("paddle_overall_ocr", 0, "部门")]
    assert fragments[0]["page"] == 2 and fragments[0]["pdf_bbox"] == [20.0, 20.0, 40.0, 30.0]
    assert fragments[0]["association"] == "geometric_containment_only"
    words = [(item, json.loads(item.locator)) for item in observation.evidence.sources if json.loads(item.locator)["element"] == "ocr_word"]
    assert len(words) == 1 and words[0][0].excerpt == "部门"
    locator = words[0][1]
    assert locator["source"] == "paddle_overall_ocr" and locator["element_index"] == 0 and locator["seal_index"] == 0
    assert locator["bbox"] == fragments[0]["pdf_bbox"] and locator["page"] == 2
    assert locator["file_version_id"] == "frozen-version" and locator["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not any("专用章" in json.dumps(value, ensure_ascii=False) for value in observation.facts)


def test_u08_region_words_reuse_the_page_without_becoming_dedicated_seal_text(tmp_path):
    path, service = source(tmp_path), RegionWordService()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        checker = agent(path)
        checker.check(2)
        checker.check(5)
    assert session.request_count == 1 and len(service.calls) == 1
    stamp, field = session.observations
    assert stamp.facts[0]["text"] == ["存档资料"]
    assert [item["text"] for item in stamp.facts[0]["region_text_fragments"]] == ["部门"]
    assert field.facts[0]["value"] == "P-42" and field.reused
    assert "region_text_fragments" not in field.facts[0]


@pytest.mark.parametrize("invalid", ["missing", "negative", "boolean", "duplicate_word", "outside", "unlocated", "ambiguous_seal"])
def test_bad_supplemental_indices_do_not_erase_seals_or_publish_authoritative_words(tmp_path, monkeypatch, invalid):
    import copy

    import documents.ocr.paddle as parser

    real_build = parser.build_evidence

    def malformed_association(*args, **kwargs):
        value = real_build(*args, **kwargs)
        page = value["pages"][0]
        seal = page["seals"][0]
        word = next(item for item in page["texts"] if item.get("source") == "paddle_overall_ocr" and item.get("index") == 0)
        if invalid == "missing":
            seal["supplemental_text_indices"] = [999]
        elif invalid == "negative":
            seal["supplemental_text_indices"] = [-1]
        elif invalid == "boolean":
            seal["supplemental_text_indices"] = [False]
        elif invalid == "duplicate_word":
            other = copy.deepcopy(word)
            other["text"] = "不能任取的另一候选"
            page["texts"].append(other)
        elif invalid == "outside":
            word["pdf_bbox"] = [70, 70, 90, 90]
        elif invalid == "unlocated":
            word["pdf_bbox"] = None
        else:
            page["seals"].append(copy.deepcopy(seal))
        return value

    monkeypatch.setattr(parser, "build_evidence", malformed_association)
    path, service = source(tmp_path), RegionWordService()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        agent(path).check(2)
    observation = session.observations[0]
    assert session.request_count == 1 and observation.state == "known"
    assert all(item["detected"] and item["text"] == ["存档资料"] for item in observation.facts)
    assert not any(item.get("region_text_fragments") for item in observation.facts)
    assert not any(json.loads(item.locator)["element"] == "ocr_word" for item in observation.evidence.sources)


@pytest.fixture(autouse=True)
def no_live_transports(monkeypatch):
    """Caught provider errors must not hide accidental live requests."""
    import subprocess

    import httpx
    import requests

    attempted = []

    def reject(*args, **kwargs):
        attempted.append("sync")
        raise AssertionError("Shadow tests must not perform network requests")

    async def reject_async(*args, **kwargs):
        attempted.append("async")
        raise AssertionError("Shadow tests must not perform network requests")

    def reject_process(*args, **kwargs):
        attempted.append("process")
        raise AssertionError("Shadow tests must not start external processes")

    monkeypatch.setattr(subprocess, "Popen", reject_process)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)
    monkeypatch.setattr(requests.sessions.Session, "request", reject)
    yield
    assert attempted == [], "A caught exception hid a live transport attempt"


@pytest.mark.parametrize("consumer", ["u08", "psw"])
@pytest.mark.parametrize("stop", ["elapsed", "cancel"])
def test_identity_read_cannot_dispatch_after_the_scope_stops(tmp_path, monkeypatch, consumer, stop):
    from audit.checkers.v4 import paddle_evidence as module
    from audit.engine.runtime import bind_runtime

    path, service, clock, cancelled = source(tmp_path), Service(), [0.0], [False]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    if consumer == "psw":
        rule = psw_rules()
        with bind_runtime(PswRuntime()):
            checker = psw_agent(path, rule)
        selected = psw_targets(path, rule, (3,))
    else:
        checker, selected = agent(path), targets(path)
    session = PaddleShadowSession(service, project_id="project-a", targets=selected, cancelled=lambda: cancelled[0])
    read_identity, reads = session._identity, []

    def delayed_identity(agent, target):
        result = read_identity(agent, target)
        reads.append(target)
        if len(reads) == 2:
            if stop == "elapsed":
                clock[0] = 700
            else:
                cancelled[0] = True
        return result

    monkeypatch.setattr(session, "_identity", delayed_identity)
    with paddle_shadow_scope(session):
        observation = session.observe_psw(checker, 3) if consumer == "psw" else session.observe(checker, "rule_3")
    if stop == "elapsed":
        assert session.request_count == len(service.calls) == 1
        assert observation.state == "known"
        assert observation.evidence.sources
    else:
        assert session.request_count == 0 and service.calls == []
        assert observation.state == "error"
        assert observation.reason == "cancelled"
        assert observation.evidence.sources == ()


def test_dispatch_uses_one_twenty_second_http_timeout(tmp_path, monkeypatch):
    from audit.checkers.v4 import paddle_evidence as module

    class Capture(Service):
        def __init__(self):
            super().__init__()
            self.timeouts = []

        def analyze_page(self, path, page, *, timeout=None):
            self.timeouts.append(timeout)
            return super().analyze_page(path, page, timeout=timeout)

    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)
    path, service = source(tmp_path), Capture()
    session = PaddleShadowSession(service, project_id="project-a", targets=targets(path))
    with paddle_shadow_scope(session):
        observation = session.observe(agent(path), "rule_3")
    assert service.timeouts == [20]
    assert session.request_count == 1 and observation.state == "known"
