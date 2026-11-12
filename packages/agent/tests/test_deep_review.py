"""Deep review: Pi reads the program results and the originals; only checked corrections apply.

The guest runs in-process with a controlled Pi runner; no model is called.
"""

import hashlib
import io
import json
import uuid
from types import SimpleNamespace

import pytest
from agent.client import DeepError
from agent.core_program import AuditJobSpec, CoreRuntime, run_audit_manifest
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

    def run(self, job, inputs, work, deadline, cancelled):
        self.modes.append(job.analysis_mode)
        grant = self.root / f"grant-{uuid.uuid4().hex}"
        grant.mkdir()
        (grant / "model.json").write_text(json.dumps({"base_url": "http://127.0.0.1:1/v1", "token": "local-short-lived-capability-1234567890", "model": "test-model", "wall_seconds": 100}))
        run_audit_manifest(inputs, work, grant)


def controlled_pi(monkeypatch, write):
    """``write(target, program_rows, stage)`` returns the reviews Pi would write, or raises."""
    from agent.execution import guest_model

    seen = []

    class Runner:
        wall_seconds = 100

        def __init__(self, _grant):
            self.input_tokens = self.output_tokens = 0

        def run(self, stage, prompt, schema, deadline, *, reasoning_effort):
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
            self.input_tokens += 100
            return DeepReview.model_validate({"file_version_id": target, "reviews": write(target, rows, stage)})

    monkeypatch.setattr(guest_model, "PiRunner", Runner)
    return seen


def run(tmp_path, snapshot, sources, spec, suite, guest=None):
    controller = Controller(
        CoreRuntime(guest or Guest(tmp_path), lambda _spec: snapshot, lambda *_: suite), lambda identity, limit: sources[identity], executor_revision="runtime", protocol_revision="protocol"
    )
    return controller.handle(spec)


def by_clause(result):
    return {(row.locator["file_version_id"], row.locator["rule_item_id"]): row for row in result.findings}


def confirm(target, rows, stage):
    return [{"clause_id": row["clause_id"], "decision": "confirmed", "reason": "原件与程序结论相符"} for row in rows]


def test_execution_ids_are_unchanged_by_deep_review():
    snapshot, *_ = build()
    assert AuditJobSpec.from_snapshot(snapshot).execution_id == uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-audit:standard:True:{snapshot.input_digest}")
    assert AuditJobSpec.from_snapshot(snapshot, analysis_mode="deep").execution_id == uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-audit:deep:True:{snapshot.input_digest}")


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
    assert corrected.status == "PASS" and corrected.suggestions.startswith(CORRECTED_PREFIX + "（程序原结论 UNABLE：No evidence）")
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


def test_a_clause_the_user_rejected_is_never_corrected(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    rejected = {"A": {"clause_id": "A", "rejections": [{"scope": "clause", "reason": "无可判定标准"}]}}
    targets = tuple(type(target)(**{**target.__dict__, "rules": {**target.rules, "formalizations": rejected}}) for target in snapshot.targets)
    snapshot = type(snapshot)(**{**snapshot.__dict__, "targets": targets})
    spec = spec.model_copy(update={"checker_snapshot_digest": snapshot.input_digest})

    def write(target, rows, stage):
        reviews = confirm(target, rows, stage)
        reviews[0] = {
            "clause_id": "A",
            "decision": "corrected",
            "status": "PASS",
            "reason": "满足",
            "evidence": [{"file_version_id": "file-a", "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}, "excerpt": "★"}],
        }
        return reviews

    controlled_pi(monkeypatch, write)
    found = by_clause(run(tmp_path, snapshot, sources, spec, suite))
    assert found[("file-a", "A")].status == "UNABLE"
    assert found[("file-a", "A")].locator["semantic"]["deep_review"]["rejection"] == "clause_rejected_by_user"


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


def test_a_failed_deep_guest_falls_back_to_the_suite_programs_without_repeating_deep(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    controlled_pi(monkeypatch, confirm)

    class Failing(Guest):
        def run(self, job, inputs, work, deadline, cancelled):
            if job.analysis_mode == "deep":
                self.modes.append("deep")
                raise ControllerFault("MODEL_TIMEOUT")
            return super().run(job, inputs, work, deadline, cancelled)

    guest = Failing(tmp_path)
    result = run(tmp_path, snapshot, sources, spec, suite, guest)
    assert guest.modes == ["deep", "standard"]
    assert {row["file_version_id"]: row["deep_review"] for row in result.coverage.checker_execution["targets"]} == {
        "file-a": {"state": "degraded", "reason": "deep_runtime_failed"},
        "file-b": {"state": "degraded", "reason": "deep_runtime_failed"},
    }


def test_a_standard_round_never_runs_or_accepts_a_review(tmp_path, monkeypatch):
    snapshot, sources, spec, suite = build()
    seen = controlled_pi(monkeypatch, confirm)
    result = run(tmp_path, snapshot, sources, spec.model_copy(update={"analysis_mode": "standard"}), suite)
    assert seen == []
    assert "analysis_mode" not in result.coverage.checker_execution
    assert all("deep_review" not in row for row in result.coverage.checker_execution["targets"])

    class Injecting(Guest):
        def run(self, job, inputs, work, deadline, cancelled):
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
