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


def test_cancelled_or_expired_scope_never_sends(tmp_path):
    path = source(tmp_path)
    for kwargs in ({"cancelled": lambda: True}, {"deadline_seconds": 0}):
        service = Service()
        session = PaddleShadowSession(service, project_id="project-a", targets=targets(path), **kwargs)
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


def test_deadline_after_dispatch_discards_the_returned_facts(tmp_path, monkeypatch):
    from audit.checkers.v4 import paddle_evidence

    clock = [0.0]
    monkeypatch.setattr(paddle_evidence, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    path = source(tmp_path)

    class SlowService(Service):
        def analyze_page(self, path, page, *, timeout=None):
            assert 0 < timeout <= 10
            value = super().analyze_page(path, page, timeout=timeout)
            clock[0] = 11
            return value

    session = PaddleShadowSession(SlowService(), project_id="project-a", targets=targets(path), deadline_seconds=10)
    with paddle_shadow_scope(session):
        agent(path).check(5)
    assert session.observations[0].reason == "deadline_exceeded"
    assert session.observations[0].evidence.sources == ()


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
