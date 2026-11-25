"""Deep review: Pi reads the program results and the originals; only checked corrections apply.

The guest runs in-process with a controlled Pi runner; no model is called.
"""

import hashlib
import io
import json
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from agent.client import DeepError
from agent.core_program import CoreRuntime, run_audit_manifest
from agent.deep_review import CORRECTED_PREFIX, CitedEvidence, DeepReview, OriginalReader, check_evidence
from agent.protocol import ExecuteRequest, FrozenFile
from agent.run import Controller
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.protocol import ControllerFault

CODE = b"""from audit.contracts import AuditResultItem

def check(context):
    return AuditResultItem(classification="rule", item=context.clause_id, priority="P0", status="UNABLE",
                           suggestions="No evidence", clause_id=context.clause_id)
"""


def workbook() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "CP"
    sheet["A1"], sheet["B1"], sheet["C1"] = "工序", "特殊特性", "合并标题"
    sheet.merge_cells("C1:D1")
    sheet["A2"], sheet["B2"], sheet["C2"] = "OP10", "★", 12.0
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def pdf(text: str | None) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 72), text, fontname="china-s")
    return document.tobytes()


def build(target_bytes: bytes | None = None, target_name: str = "cp.xlsx"):
    programs = {"checker.py": CODE}
    digest = checker_digest(programs)
    sources = {"file-a": target_bytes or workbook(), "file-b": b"b", "reference": pdf("签字：张三")}
    names = {"file-a": target_name, "file-b": "b.txt", "reference": "ref.pdf"}
    inputs = tuple(FrozenInput(key, hashlib.sha256(raw).hexdigest(), "reference" if key == "reference" else "target", names[key]) for key, raw in sources.items())
    rules = {"type": "控制计划", "rules": [{"id": "A", "rule_to_check": "A"}, {"id": "B", "rule_to_check": "B"}]}
    targets = tuple(
        FrozenTarget(row.file_version_id, "revision", "UAES:U08", "a" * 64, rules, RuleBinding("UAES:U08", digest, {"A": "checker:check", "B": "checker:check"}))
        for row in inputs
        if row.role == "target"
    )
    snapshot = ExecutionSnapshot("tenant", "initiator", inputs, targets, "runtime", "library", {"provider": "responses-compatible", "model": "test-model"}, round_id="round")
    spec = ExecuteRequest(
        protocol_revision="protocol",
        executor="ppap-checker-core",
        executor_revision="runtime",
        round_id="round",
        tenant_id="tenant",
        initiating_user_id="initiator",
        job_id="job",
        checker_snapshot_digest=snapshot.input_digest,
        model_identity=dict(snapshot.model_identity),
        analysis_mode="deep",
        files=[FrozenFile(file_version_id=row.file_version_id, role=row.role, filename=row.filename, sha256=row.sha256, rule_body=rules if row.role == "target" else None) for row in inputs],
    )
    suite = SimpleNamespace(revision=SimpleNamespace(resolved_digest="a" * 64), files=programs)
    return snapshot, sources, spec, suite


class Guest:
    def __init__(self, tmp_path):
        self.root, self.modes = tmp_path, []

    def ready(self, operation="audit"):
        return True

    def run(self, job, inputs, work, deadline, cancelled, on_progress=None, on_activity=None):
        self.modes.append(job.analysis_mode)
        grant = self.root / f"grant-{uuid.uuid4().hex}"
        grant.mkdir()
        (grant / "model.json").write_text(json.dumps({"base_url": "http://127.0.0.1:1/v1", "token": "local-short-lived-capability-1234567890", "model": "test-model", "wall_seconds": 100}))
        run_audit_manifest(inputs, work, grant)


def controlled_pi(monkeypatch, write, deadlines=None):
    """``write(target, program_rows, stage)`` returns the reviews Pi would write, or raises.

    ``deadlines`` collects the time each review is given.
    """
    from agent.execution import guest_model

    seen = []

    class Runner:
        wall_seconds = 100

        def __init__(self, _grant):
            self.input_tokens = self.output_tokens = 0

        def run(self, stage, prompt, schema, deadline):
            assert schema is DeepReview and "深度复核" in prompt
            listed = json.loads((stage / "files.json").read_text())
            target = next(row["file_version_id"] for row in listed if row["role"] == "本次审核目标")
            # Pi gets every file of the round, the frozen clauses and the program results; never the program.
            assert {row["file_version_id"] for row in listed} == {"file-a", "file-b", "reference"}
            assert all((stage / row["path"]).read_bytes() for row in listed)
            assert json.loads((stage / "rules.json").read_text())["rules"][0]["id"] == "A"
            assert not (stage / "checker.py").exists()
            rows = json.loads((stage / "program-results.json").read_text())
            seen.append((target, [row["status"] for row in rows]))
            if deadlines is not None:
                deadlines.append(round(deadline - time.monotonic()))
            self.input_tokens += 100
            return DeepReview.model_validate({"file_version_id": target, "reviews": write(target, rows, stage)})

    monkeypatch.setattr(guest_model, "PiRunner", Runner)
    return seen


def run(tmp_path, snapshot, sources, spec, suite, guest=None):
    def read_source(identity, limit):
        if identity not in sources:
            raise FileNotFoundError(identity)
        return sources[identity]

    controller = Controller(CoreRuntime(guest or Guest(tmp_path), lambda _spec: snapshot, lambda *_: suite), read_source, executor_revision="runtime", protocol_revision="protocol")
    return controller.handle(spec)


def by_clause(result):
    return {(row.locator["file_version_id"], row.locator["rule_item_id"]): row for row in result.findings}


def confirm(target, rows, stage):
    return [{"clause_id": row["clause_id"], "decision": "confirmed", "reason": "原件与程序结论相符"} for row in rows]


@pytest.mark.parametrize("analysis_mode", ["standard", "deep"])
@pytest.mark.parametrize("missing_original", [False, True])
def test_unbound_targets_are_not_execution_errors_or_deep_review_failures(tmp_path, monkeypatch, analysis_mode, missing_original):
    snapshot, sources, spec, suite = build()
    snapshot = replace(snapshot, targets=tuple(replace(target, binding=replace(target.binding, entrypoints={})) for target in snapshot.targets))
    spec = spec.model_copy(update={"analysis_mode": analysis_mode, "checker_snapshot_digest": snapshot.input_digest})
    if missing_original:
        sources.pop("file-a")

    seen = controlled_pi(monkeypatch, confirm)
    result = run(tmp_path, snapshot, sources, spec, suite)

    assert not seen
    assert result.findings == [] and result.coverage.checked == []
    assert result.coverage.unchecked == [
        "ref.pdf（参考资料，不单独作完整审核）",
        "cp.xlsx（原件不可用，未检查）" if missing_original else "cp.xlsx（未登记检查程序，暂未检查）",
        "b.txt（未登记检查程序，暂未检查）",
    ]
    assert result.coverage.locator_gap is False
    assert {(row["file_version_id"], row["rule_item_id"]) for row in result.coverage.rule_items} == {(name, clause) for name in ("file-a", "file-b") for clause in ("A", "B")}
    for row in result.coverage.rule_items:
        assert row["coverage"] == "unsupported"
        assert row["reason"] == "未登记检查程序，暂未检查。"
        assert not {"status", "item_key", "verification_scope"} & row.keys()
    execution = result.coverage.checker_execution
    assert execution["unavailable_file_version_ids"] == (["file-a"] if missing_original else [])
    reviews = {row["file_version_id"]: row.get("deep_review") for row in execution["targets"]}
    assert reviews == {
        "file-a": {"state": "degraded", "reason": "deep_input_missing"} if analysis_mode == "deep" and missing_original else None,
        "file-b": None,
    }


def test_unbound_clause_does_not_prevent_review_of_a_bound_clause(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    first, second = snapshot.targets
    snapshot = replace(snapshot, targets=(replace(first, binding=replace(first.binding, entrypoints={})), replace(second, binding=replace(second.binding, entrypoints={"A": "checker:check"}))))
    spec = spec.model_copy(update={"checker_snapshot_digest": snapshot.input_digest})

    seen = controlled_pi(monkeypatch, confirm)
    result = run(tmp_path, snapshot, sources, spec, suite)

    assert seen == [("file-b", ["UNABLE"])]
    assert result.coverage.checked == ["b.txt"]
    assert set(by_clause(result)) == {("file-b", "A")}
    assert {row["file_version_id"]: row.get("deep_review") for row in result.coverage.checker_execution["targets"]} == {"file-a": None, "file-b": {"state": "completed"}}


def test_a_checked_correction_replaces_the_verdict_and_keeps_the_program_one(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()

    def write(target, rows, stage):
        reviews = confirm(target, rows, stage)
        if target == "file-a":
            evidence = [
                {"file_version_id": "file-a", "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}, "excerpt": "★"},
                {"file_version_id": "reference", "locator": {"kind": "pdf_page", "page": 1}, "excerpt": "签字： 张三"},
            ]
            reviews[0] = {"clause_id": "A", "decision": "corrected", "status": "PASS", "reason": "B2 标有特殊特性，参考文件有签字", "evidence": evidence}
        return reviews

    seen = controlled_pi(monkeypatch, write)
    result = run(tmp_path, snapshot, sources, spec, suite)

    assert seen == [("file-a", ["UNABLE", "UNABLE"]), ("file-b", ["UNABLE", "UNABLE"])]
    found = by_clause(result)
    corrected = found[("file-a", "A")]
    assert corrected.status == "PASS" and corrected.suggestions.startswith(CORRECTED_PREFIX + "（程序原结论 无法判断：No evidence）")
    semantic = corrected.locator["semantic"]
    assert semantic["deep_review"] == {"decision": "corrected", "program_status": "UNABLE", "status": "PASS", "reason": "B2 标有特殊特性，参考文件有签字"}
    assert [(source["file_version_id"], source["locator"]) for source in semantic["sources"]] == [
        ("file-a", {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}),
        ("reference", {"kind": "pdf_page", "page": 1}),
    ]
    assert found[("file-a", "B")].status == "UNABLE" and found[("file-a", "B")].locator["semantic"]["deep_review"]["decision"] == "confirmed"
    targets = {row["file_version_id"]: row for row in result.coverage.checker_execution["targets"]}
    assert targets["file-a"]["deep_review"] == targets["file-b"]["deep_review"] == {"state": "completed"}
    # History keeps naming the validated Suite program: nothing was generated.
    assert {row["suite_revision_id"] for row in targets.values()} == {"revision"}


@pytest.mark.parametrize(
    ("status", "evidence", "rejection"),
    [
        ("PASS", [], "evidence_missing"),
        ("PASS", [{"file_version_id": "file-a", "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}, "excerpt": "☆"}], "excerpt_not_in_original"),
        ("NG", [{"file_version_id": "file-a", "locator": {"kind": "xlsx_cell", "sheet": "缺失", "cell": "B2"}, "excerpt": "★"}], "locator_not_in_original"),
        ("NG", [{"file_version_id": "foreign", "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}, "excerpt": "★"}], "file_not_in_round"),
        ("PASS", [{"file_version_id": "file-a", "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2", "x": 1}, "excerpt": "★"}], "locator_invalid"),
    ],
)
def test_a_correction_whose_evidence_does_not_check_out_keeps_the_program_verdict(tmp_path, monkeypatch, status, evidence, rejection):
    snapshot, sources, spec, suite = build()

    def write(target, rows, stage):
        reviews = confirm(target, rows, stage)
        if target == "file-a":
            reviews[0] = {"clause_id": "A", "decision": "corrected", "status": status, "reason": "看起来满足", "evidence": evidence}
        return reviews

    controlled_pi(monkeypatch, write)
    found = by_clause(run(tmp_path, snapshot, sources, spec, suite))
    kept = found[("file-a", "A")]
    assert kept.status == "UNABLE" and kept.suggestions == "No evidence"
    assert kept.locator["semantic"]["deep_review"] == {"decision": "rejected", "program_status": "UNABLE", "reason": "看起来满足", "proposed_status": status, "rejection": rejection}


def test_a_target_whose_review_fails_or_differs_keeps_its_program_result_and_says_so(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    calls: list[str] = []

    def write(target, rows, stage):
        calls.append(target)
        if target == "file-a":
            return confirm(target, rows, stage)[:1]  # One clause missing, twice.
        raise TimeoutError("Model stage deadline exceeded")

    controlled_pi(monkeypatch, write)
    result = run(tmp_path, snapshot, sources, spec, suite)
    assert calls == ["file-a", "file-a", "file-b"]
    targets = {row["file_version_id"]: row["deep_review"] for row in result.coverage.checker_execution["targets"]}
    assert targets == {"file-a": {"state": "degraded", "reason": "deep_review_invalid"}, "file-b": {"state": "degraded", "reason": "deep_timeout"}}
    assert {row.status for row in result.findings} == {"UNABLE"}
    assert all("deep_review" not in row.locator["semantic"] for row in result.findings)


def test_one_guest_shares_the_round_and_gives_every_file_its_own_time(tmp_path, monkeypatch):
    from agent.execution.budget import DEEP_TOTAL_SECONDS

    snapshot, sources, spec, suite = build()
    deadlines: list[int] = []
    controlled_pi(monkeypatch, confirm, deadlines)
    reported = []

    class Reporting(Guest):
        def run(self, job, inputs, work, deadline, cancelled, on_progress=None, on_activity=None):
            super().run(job, inputs, work, deadline, cancelled)
            reported.extend(json.loads(line) for line in (work / "progress.jsonl").read_text().splitlines())

    guest = Reporting(tmp_path)
    run(tmp_path, snapshot, sources, spec, suite, guest)
    # One guest for the round, so the files share its context.
    assert guest.modes == ["deep"]
    # Each review has the whole of its own time, not a share of the round's.
    assert deadlines == [DEEP_TOTAL_SECONDS, DEEP_TOTAL_SECONDS]
    # Two files checked and two reviewed: four tasks, each reported as it starts, then the summary.
    assert [(row.get("stage"), row.get("target"), row["completed_count"]) for row in reported] == [
        ("check", "file-a", 0),
        ("check", "file-b", 1),
        ("review", "file-a", 2),
        ("review", "file-b", 3),
        (None, None, 4),
    ]


def test_a_deep_round_whose_guest_fails_fails_and_is_not_run_again(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    controlled_pi(monkeypatch, confirm)

    class Failing(Guest):
        def run(self, job, inputs, work, deadline, cancelled, on_progress=None, on_activity=None):
            self.modes.append(job.analysis_mode)
            raise ControllerFault("MODEL_TIMEOUT")

    guest = Failing(tmp_path)
    with pytest.raises(DeepError, match="deep_timeout"):
        run(tmp_path, snapshot, sources, spec, suite, guest)
    assert guest.modes == ["deep"]


def test_a_standard_round_never_runs_or_accepts_a_review(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    seen = controlled_pi(monkeypatch, confirm)
    result = run(tmp_path, snapshot, sources, spec.model_copy(update={"analysis_mode": "standard"}), suite)
    assert seen == []
    assert "analysis_mode" not in result.coverage.checker_execution
    assert all("deep_review" not in row for row in result.coverage.checker_execution["targets"])

    class Injecting(Guest):
        def run(self, job, inputs, work, deadline, cancelled, on_progress=None, on_activity=None):
            super().run(job, inputs, work, deadline, cancelled)
            bundle = work / "result" / "bundle.json"
            bundle.write_text(json.dumps({**json.loads(bundle.read_text()), "deep_reviews": {}}))

    with pytest.raises(DeepError, match="deep_output_invalid"):
        run(tmp_path, snapshot, sources, spec.model_copy(update={"analysis_mode": "standard"}), suite, Injecting(tmp_path))


def reader_for(name: str, raw: bytes, tmp_path):
    snapshot = SimpleNamespace(inputs=(FrozenInput("v", hashlib.sha256(raw).hexdigest(), "target", name),))
    return OriginalReader(snapshot, {"v": raw}, tmp_path)  # type: ignore[arg-type]


def cite(locator, excerpt, kind="structure"):
    return CitedEvidence(file_version_id="v", kind=kind, locator=locator, excerpt=excerpt)


def test_excerpts_are_checked_against_the_original(tmp_path):
    reader = reader_for("cp.xlsx", workbook(), tmp_path)
    ok = [
        cite({"kind": "xlsx_cell", "sheet": "CP", "cell": "A2"}, "OP10"),
        cite({"kind": "xlsx_cell", "sheet": "CP", "cell": "D1"}, "合并 标题"),  # Merged cells read their anchor.
        cite({"kind": "xlsx_cell", "sheet": "CP", "cell": "C2"}, "12.0"),
        cite({"kind": "xlsx_range", "sheet": "CP", "start": "A2", "end": "B2"}, "OP10★"),
    ]
    assert [check_evidence(item, reader, frozenset())[1] for item in ok] == ["", "", "", ""]
    assert check_evidence(ok[0], reader, frozenset({"v"}))[1] == "file_not_in_round"
    assert check_evidence(cite({"kind": "xlsx_cell", "sheet": "CP", "cell": "A2"}, "OP20"), reader, frozenset())[1] == "excerpt_not_in_original"
    assert check_evidence(cite({"kind": "pdf_page", "page": 1}, "OP10"), reader, frozenset())[1] == "locator_not_in_original"


def test_a_scanned_pdf_page_cannot_carry_a_correction(tmp_path):
    reader = reader_for("scan.pdf", pdf(None), tmp_path)
    assert check_evidence(cite({"kind": "pdf_page", "page": 1}, "张三"), reader, frozenset())[1] == "scanned_page"
    assert check_evidence(cite({"kind": "pdf_page", "page": 1}, "有签章", "visual"), reader, frozenset())[1] == "visual_needs_image_or_text_page"
    text = reader_for("signed.pdf", pdf("签字：张三"), tmp_path)
    assert check_evidence(cite({"kind": "pdf_page", "page": 1}, "有红色签章", "visual"), text, frozenset())[1] == ""
    assert check_evidence(cite({"kind": "pdf_page", "page": 2}, "张三"), text, frozenset())[1] == "locator_not_in_original"


def test_images_carry_visual_evidence_and_word_documents_carry_text(tmp_path):
    import docx

    image = reader_for("seal.png", b"\x89PNG\r\n\x1a\n", tmp_path)
    assert check_evidence(cite({"kind": "pdf_page", "page": 1}, "公司印章", "visual"), image, frozenset())[1] == ""
    assert check_evidence(cite({"kind": "pdf_page", "page": 2}, "公司印章", "visual"), image, frozenset())[1] == "locator_invalid"
    document = docx.Document()
    document.add_paragraph("批准：李四")
    output = io.BytesIO()
    document.save(output)
    word = reader_for("psw.docx", output.getvalue(), tmp_path)
    assert check_evidence(cite({"kind": "docx_paragraph", "paragraph": 1}, "李四"), word, frozenset())[1] == ""
    assert check_evidence(cite({"kind": "docx_paragraph", "paragraph": 1}, "王五"), word, frozenset())[1] == "excerpt_not_in_original"


def test_a_corrected_technical_failure_shows_no_diagnostic_text():
    from agent.deep_review import _corrected_item

    finding = {"clause_id": "A", "file_version_id": "file-a", "status": "ERR", "suggestions": "原件读取失败（SubprocessError），尚未取得业务结论。"}
    evidence = [{"kind": "structure", "locator": json.dumps({"file_version_id": "file-a", "kind": "xlsx_cell", "sheet": "CP", "cell": "A2"}), "excerpt": "生产"}]
    record = {"program_status": "ERR", "status": "NG", "reason": "A2 勾选量产，原件无受控章", "evidence": evidence}
    item = _corrected_item(finding, record, "cp.xlsx")
    assert item["suggestions"] == CORRECTED_PREFIX + "（程序原结论 技术错误）：A2 勾选量产，原件无受控章"


def test_the_host_review_time_reaches_the_guest_through_the_job_spec(monkeypatch):
    import importlib

    import agent.execution.budget as budget
    from agent.core_program import AuditJobSpec

    snapshot, *_ = build()
    try:
        monkeypatch.setenv("PPAP_DEEP_REVIEW_SECONDS", "900")
        importlib.reload(budget)
        # The host's configured time, and the task time derived from it.
        assert (budget.DEEP_TOTAL_SECONDS, budget.TASK_SECONDS) == (900, 960)
        job = AuditJobSpec.from_snapshot(snapshot, analysis_mode="deep", review_seconds=budget.DEEP_TOTAL_SECONDS)
        # The guest reads no configuration: the spec carries the time, and the guest's own check keeps it.
        assert job.review_seconds == 900
        assert job == AuditJobSpec.from_snapshot(snapshot, job.allow_model_calls, frozenset(job.unavailable_sources), job.analysis_mode, job.review_seconds)
        monkeypatch.setenv("PPAP_DEEP_REVIEW_SECONDS", "1801")
        with pytest.raises(ValueError):
            importlib.reload(budget)
    finally:
        monkeypatch.delenv("PPAP_DEEP_REVIEW_SECONDS", raising=False)
        importlib.reload(budget)
