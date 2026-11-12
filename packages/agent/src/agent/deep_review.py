"""Deep review: after the Suite programs ran, Pi checks each clause result against the originals.

The program is never changed. Pi confirms a result or proposes a correction with
locatable evidence. Guest code (not Pi) checks every cited excerpt against the
frozen original bytes; a correction whose evidence does not check out is
discarded and the program result stays. The host merges only checked corrections.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from audit.checker_core import ExecutionSnapshot, FrozenTarget, clause_rejections, rejected_whole
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

Decision = Literal["confirmed", "corrected"]
Verdict = Literal["PASS", "NG", "NA", "UNABLE"]
# Why one target was not deep-reviewed; it keeps its program result.
DEGRADE_REASONS = frozenset({"deep_timeout", "deep_review_failed", "deep_review_invalid", "deep_input_missing", "deep_runtime_failed"})
REVIEW_EFFORT = "medium"
REVIEW_ATTEMPTS = 2
MAX_EVIDENCE = 12
CORRECTED_PREFIX = "深度复核订正"
_IMAGES = frozenset({"png", "jpg", "jpeg", "webp"})
_WORKBOOKS = frozenset({"xlsx", "xls", "xlsm"})
_LOCATOR_KINDS: dict[str, dict[str, type]] = {
    "xlsx_cell": {"sheet": str, "cell": str},
    "xlsx_range": {"sheet": str, "start": str, "end": str},
    "pdf_page": {"page": int},
    "docx_paragraph": {"paragraph": int},
    "docx_cell": {"table": int, "row": int, "column": int},
}
_CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


class CitedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_version_id: str = Field(min_length=1, max_length=128)
    kind: Literal["structure", "visual"] = "structure"
    locator: dict[str, Any]
    excerpt: str = Field(min_length=1, max_length=500)


class ClauseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(min_length=1, max_length=256)
    decision: Decision
    status: Verdict | None = None
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[CitedEvidence] = Field(default_factory=list, max_length=MAX_EVIDENCE)


class DeepReview(BaseModel):
    """What Pi writes to execution.json for one target."""

    model_config = ConfigDict(extra="forbid")

    file_version_id: str = Field(min_length=1, max_length=128)
    reviews: list[ClauseReview] = Field(max_length=512)


# ---------------------------------------------------------------- guest side


def _normalize(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", "", "" if value is None else str(value))


def _same_number(left: str, right: str) -> bool:
    try:
        return float(left) == float(right)
    except ValueError:
        return False


def _suffix(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


class OriginalReader:
    """Reads frozen originals for excerpt checks. Each original is parsed at most once."""

    def __init__(self, snapshot: ExecutionSnapshot, sources: Mapping[str, bytes], directory: Path) -> None:
        self.inputs = {item.file_version_id: item for item in snapshot.inputs}
        self.sources, self.directory = sources, directory
        self._parsed: dict[str, Any] = {}

    def _path(self, version: str) -> Path:
        item = self.inputs[version]
        path = self.directory / f"{len(self._parsed)}-{abs(hash(version))}.{_suffix(item.filename) or 'bin'}"
        path.write_bytes(self.sources[version])
        return path

    def _open(self, version: str) -> Any:
        if version not in self._parsed:
            kind = _suffix(self.inputs[version].filename)
            if kind in _WORKBOOKS:
                from documents.excel import load_excel_workbook

                self._parsed[version] = load_excel_workbook(str(self._path(version)))
            elif kind == "pdf":
                import fitz

                with fitz.open(stream=self.sources[version], filetype="pdf") as document:
                    self._parsed[version] = [page.get_text() for page in document]
            elif kind == "docx":
                import docx

                self._parsed[version] = docx.Document(str(self._path(version)))
            else:
                self._parsed[version] = None
        return self._parsed[version]

    def _cell(self, sheet: Any, ref: str) -> object:
        for merged in sheet.merged_cells.ranges:
            if ref in merged:
                return sheet.cell(merged.min_row, merged.min_col).value
        return sheet[ref].value

    def text_at(self, version: str, locator: Mapping[str, Any]) -> str | None:
        """The original text at a locator, or None when it cannot be read there."""
        kind = _suffix(self.inputs[version].filename)
        opened = self._open(version)
        if locator["kind"] in {"xlsx_cell", "xlsx_range"}:
            if kind not in _WORKBOOKS or locator["sheet"] not in opened.sheetnames:
                return None
            sheet = opened[locator["sheet"]]
            if locator["kind"] == "xlsx_cell":
                return _normalize(self._cell(sheet, locator["cell"]))
            from openpyxl.utils.cell import range_boundaries

            low_col, low_row, high_col, high_row = range_boundaries(f"{locator['start']}:{locator['end']}")
            if (high_col - low_col + 1) * (high_row - low_row + 1) > 4000:
                return None
            from openpyxl.utils import get_column_letter

            return "".join(_normalize(self._cell(sheet, f"{get_column_letter(col)}{row}")) for row in range(low_row, high_row + 1) for col in range(low_col, high_col + 1))
        if locator["kind"] == "pdf_page":
            if kind != "pdf" or not 1 <= locator["page"] <= len(opened):
                return None
            return _normalize(opened[locator["page"] - 1])
        if locator["kind"] == "docx_paragraph":
            if kind != "docx" or not 1 <= locator["paragraph"] <= len(opened.paragraphs):
                return None
            return _normalize(opened.paragraphs[locator["paragraph"] - 1].text)
        if locator["kind"] == "docx_cell":
            if kind != "docx" or not 1 <= locator["table"] <= len(opened.tables):
                return None
            table = opened.tables[locator["table"] - 1]
            if not 1 <= locator["row"] <= len(table.rows) or not 1 <= locator["column"] <= len(table.columns):
                return None
            return _normalize(table.cell(locator["row"] - 1, locator["column"] - 1).text)
        return None


def _typed_locator(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    fields = _LOCATOR_KINDS.get(str(raw.get("kind")))
    if fields is None or set(raw) != {"kind", *fields}:
        return None
    for key, expected in fields.items():
        value = raw[key]
        if type(value) is not expected or isinstance(value, int) and value < 1 or isinstance(value, str) and not value.strip():
            return None
    if raw["kind"] == "xlsx_cell" and not _CELL.fullmatch(raw["cell"]):
        return None
    if raw["kind"] == "xlsx_range" and not (_CELL.fullmatch(raw["start"]) and _CELL.fullmatch(raw["end"])):
        return None
    return dict(raw)


def check_evidence(item: CitedEvidence, reader: OriginalReader, unavailable: frozenset[str]) -> tuple[dict[str, Any] | None, str]:
    """A product EvidenceSource for one cited excerpt, or why it does not check out."""
    version = item.file_version_id
    if version not in reader.inputs or version in unavailable:
        return None, "file_not_in_round"
    locator = _typed_locator(item.locator)
    if locator is None:
        return None, "locator_invalid"
    kind = _suffix(reader.inputs[version].filename)
    if item.kind == "visual":
        # A visual fact (seal, signature, picture) is read by Pi; its locator must still exist.
        # A scanned PDF page has no text layer and stays UNABLE (no visual reading of scans).
        if kind in _IMAGES:
            if locator != {"kind": "pdf_page", "page": 1}:
                return None, "locator_invalid"
        elif kind != "pdf" or locator["kind"] != "pdf_page" or not reader.text_at(version, locator):
            return None, "visual_needs_image_or_text_page"
    else:
        try:
            original = reader.text_at(version, locator)
        except Exception as exc:  # A damaged original is an unverifiable citation, not a crash.
            logger.warning("deep_review_original_unreadable %s: %s", type(exc).__name__, str(exc)[:200])
            return None, "original_unreadable"
        if original is None:
            return None, "locator_not_in_original"
        if locator["kind"] == "pdf_page" and not original:
            return None, "scanned_page"
        excerpt = _normalize(item.excerpt)
        if not excerpt or excerpt not in original and not (locator["kind"] == "xlsx_cell" and _same_number(excerpt, original)):
            return None, "excerpt_not_in_original"
    return {"kind": item.kind, "locator": json.dumps({**locator, "file_version_id": version}, ensure_ascii=False, sort_keys=True), "excerpt": item.excerpt[:500]}, ""


def check_review(
    review: DeepReview,
    *,
    target: FrozenTarget,
    program: Mapping[str, Mapping[str, Any]],
    reader: OriginalReader,
    unavailable: frozenset[str],
) -> list[dict[str, Any]]:
    """One record per program clause. Raises ValueError when the clause set differs."""
    by_clause = {item.clause_id: item for item in review.reviews}
    if review.file_version_id != target.file_version_id or len(by_clause) != len(review.reviews) or set(by_clause) != set(program):
        missing, extra = sorted(set(program) - set(by_clause)), sorted(set(by_clause) - set(program))
        raise ValueError(f"条款集合与程序结论不一致：缺少 {missing[:20]}，多出 {extra[:20]}，或文件 ID / 条款重复")
    records = []
    for clause, finding in program.items():
        item = by_clause[clause]
        record: dict[str, Any] = {"clause_id": clause, "program_status": finding["status"], "reason": item.reason.strip()}
        if item.decision == "confirmed" or item.status == finding["status"]:
            records.append({**record, "decision": "confirmed"})
            continue
        problem = ""
        sources: list[dict[str, Any]] = []
        if item.status is None:
            problem = "status_missing"
        elif rejected_whole(clause_rejections(target.rules, clause)):
            problem = "clause_rejected_by_user"
        elif not item.evidence:
            problem = "evidence_missing"
        for cited in item.evidence:
            if problem:
                break
            checked, problem = check_evidence(cited, reader, unavailable)
            if checked is not None:
                sources.append(checked)
        if problem:
            records.append({**record, "decision": "rejected", "proposed_status": item.status, "rejection": problem})
            continue
        records.append({**record, "decision": "corrected", "status": item.status, "evidence": sources})
    return records


def _safe_name(index: int, filename: str) -> str:
    stem = re.sub(r"[\\/\x00-\x1f]", "_", Path(filename).name)[:120] or "file"
    return f"{index:02d}-{stem}"


def stage_review(
    stage: Path,
    snapshot: ExecutionSnapshot,
    sources: Mapping[str, bytes],
    target: FrozenTarget,
    program: Mapping[str, Mapping[str, Any]],
    resources: bytes | None,
    unavailable: frozenset[str],
    feedback: str | None = None,
) -> str:
    """Write Pi's read-only inputs and return the prompt."""
    files = stage / "files"
    files.mkdir(parents=True)
    listed = []
    for index, item in enumerate(snapshot.inputs):
        entry: dict[str, Any] = {
            "file_version_id": item.file_version_id,
            "filename": item.filename,
            "role": "本次审核目标" if item.file_version_id == target.file_version_id else ("同批其他待审文件" if item.role == "target" else "参考文件"),
            "document_type": item.document_type or "",
        }
        if item.file_version_id in unavailable:
            entry["path"] = None
            entry["unavailable"] = "原件本次不可用"
        else:
            path = files / _safe_name(index, item.filename)
            path.write_bytes(sources[item.file_version_id])
            path.chmod(0o400)
            entry["path"] = str(path.relative_to(stage))
        listed.append(entry)
    rules = target.to_dict()
    (stage / "files.json").write_text(json.dumps(listed, ensure_ascii=False, indent=1), encoding="utf-8")
    (stage / "rules.json").write_text(json.dumps({"rules": rules["rules"].get("rules", []), "appendix": rules["appendix"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (stage / "program-results.json").write_text(json.dumps([_program_view(clause, finding) for clause, finding in program.items()], ensure_ascii=False, indent=1), encoding="utf-8")
    if resources:
        (stage / "resources-hint.py").write_bytes(resources)
    target_entry = next(item for item in listed if item["file_version_id"] == target.file_version_id)
    lines = [
        "本次任务：深度复核。按 AGENTS.md 逐条复核程序结论，结果写入 execution.json。",
        f"目标文件：{target_entry['filename']}（file_version_id={target.file_version_id}，路径 {target_entry['path']}）。",
        "输入：files.json（同批全部文件与路径）、rules.json（本次冻结条款与已确认附录）、program-results.json（程序对每条的结论、证据、诊断）"
        + ("、resources-hint.py（该类型检查程序的表名、表头锚点、列别名、PDF 配置，仅作“在哪找”的线索）。" if resources else "。"),
        f"program-results.json 共 {len(program)} 条，execution.json 的 reviews 必须恰好覆盖这些 clause_id，每条一项。",
    ]
    if feedback:
        lines.append("上一次提交没有通过宿主校验：" + feedback + "。请修正后重新写入完整的 execution.json。")
    return "\n".join(lines)


def _program_view(clause: str, finding: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("status", "suggestions", "evidence", "checks_performed", "scope", "empty_scope_basis", "clarifications", "fact_questions", "supply", "severity")
    return {"clause_id": clause, **{key: finding[key] for key in keep if finding.get(key) not in (None, "", [], {})}}


def review_targets(
    snapshot: ExecutionSnapshot,
    sources: Mapping[str, bytes],
    programs: Mapping[str, Mapping[str, bytes]],
    findings: Sequence[Mapping[str, Any]],
    *,
    runner: Any,
    work: Path,
    seconds: float,
    unavailable: frozenset[str],
) -> dict[str, dict[str, Any]]:
    """Review every target in order; one target's failure never stops the others.

    Each target gets an equal share of the time still left, so a slow file cannot starve the rest.
    """
    from agent.execution.guest_model import ModelProtocolError

    deadline = time.monotonic() + seconds
    states: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="ppap-deep-originals-") as scratch:
        reader = OriginalReader(snapshot, sources, Path(scratch))
        for index, target in enumerate(snapshot.targets):
            program = {str(item["clause_id"]): item for item in findings if item["file_version_id"] == target.file_version_id}
            if target.file_version_id in unavailable or not program:
                states[target.file_version_id] = {"state": "degraded", "reason": "deep_input_missing"}
                continue
            started, before = time.monotonic(), (runner.input_tokens, runner.output_tokens)
            share = started + max(0.0, deadline - started) / (len(snapshot.targets) - index)
            feedback: str | None = None
            state: dict[str, Any] = {"state": "degraded", "reason": "deep_review_failed"}
            for attempt in range(REVIEW_ATTEMPTS):
                if time.monotonic() >= share:
                    state = {"state": "degraded", "reason": "deep_timeout"}
                    break
                stage = work / f"review-{index}-{attempt}"
                stage.mkdir(parents=True)
                prompt = stage_review(stage, snapshot, sources, target, program, programs[target.binding.checker_digest].get("resources.py"), unavailable, feedback)
                try:
                    review = runner.run(stage, prompt, DeepReview, share, reasoning_effort=REVIEW_EFFORT)
                    state = {"state": "completed", "reviews": check_review(review, target=target, program=program, reader=reader, unavailable=unavailable)}
                    break
                except (ValidationError, ValueError) as exc:
                    # ModelProtocolError is a ValueError; a schema or clause-set problem gets one more try.
                    feedback = str(exc)[:1500]
                    timed_out = time.monotonic() >= share
                    state = {
                        "state": "degraded",
                        "reason": "deep_timeout" if timed_out else ("deep_review_failed" if isinstance(exc, ModelProtocolError) else "deep_review_invalid"),
                        "detail": f"{type(exc).__name__}: {exc}"[:300],
                    }
                except TimeoutError as exc:
                    state = {"state": "degraded", "reason": "deep_timeout", "detail": str(exc)[:300]}
                    break
                except Exception as exc:
                    state = {"state": "degraded", "reason": "deep_review_failed", "detail": f"{type(exc).__name__}: {exc}"[:300]}
                    break
            state["usage"] = {
                "seconds": round(time.monotonic() - started, 1),
                "input_tokens": runner.input_tokens - before[0],
                "output_tokens": runner.output_tokens - before[1],
            }
            states[target.file_version_id] = state
    return states


# ----------------------------------------------------------------- host side


def _evidence_format(filename: str) -> str | None:
    kind = _suffix(filename)
    return kind if kind in {"xlsx", "xls", "xlsm", "pdf", "docx", "doc", "png", "jpg", "jpeg", "webp", "txt", "md"} else None


def _host_record(raw: object, finding: Mapping[str, Any], target: FrozenTarget, known: set[str]) -> dict[str, Any]:
    """Re-check one guest record's shape and identities. The guest checked the excerpts."""
    if not isinstance(raw, dict) or raw.get("program_status") != finding["status"] or not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
        raise ValueError("record")
    decision = raw.get("decision")
    base = {"clause_id": raw["clause_id"], "program_status": finding["status"], "reason": raw["reason"][:2000]}
    if decision == "confirmed" and set(raw) == {"clause_id", "decision", "program_status", "reason"}:
        return {**base, "decision": "confirmed"}
    if decision == "rejected" and set(raw) == {"clause_id", "decision", "program_status", "reason", "proposed_status", "rejection"}:
        return {**base, "decision": "rejected", "proposed_status": raw["proposed_status"], "rejection": str(raw["rejection"])[:100]}
    if decision != "corrected" or set(raw) != {"clause_id", "decision", "program_status", "reason", "status", "evidence"}:
        raise ValueError("record")
    if raw["status"] not in {"PASS", "NG", "NA", "UNABLE"} or raw["status"] == finding["status"] or rejected_whole(clause_rejections(target.rules, raw["clause_id"])):
        raise ValueError("record")
    evidence = raw["evidence"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_EVIDENCE:
        raise ValueError("record")
    for source in evidence:
        if not isinstance(source, dict) or set(source) != {"kind", "locator", "excerpt"} or source["kind"] not in {"structure", "visual"}:
            raise ValueError("record")
        locator = json.loads(source["locator"]) if isinstance(source["locator"], str) else None
        if not isinstance(locator, dict) or locator.pop("file_version_id", None) not in known or _typed_locator(locator) is None:
            raise ValueError("record")
        if not isinstance(source["excerpt"], str) or not source["excerpt"].strip():
            raise ValueError("record")
    return {**base, "decision": "corrected", "status": raw["status"], "evidence": evidence}


def _corrected_item(finding: Mapping[str, Any], record: Mapping[str, Any], filename: str) -> dict[str, Any] | None:
    """The merged result item: the reviewer's verdict with its checked evidence, the program's verdict kept in the text."""
    fmt = _evidence_format(filename)
    if fmt is None:
        return None
    versions = {str(finding["file_version_id"])}
    for source in record["evidence"]:
        versions.add(json.loads(source["locator"])["file_version_id"])
    kinds = {source["kind"] for source in record["evidence"]}
    program = str(finding.get("suggestions") or "").strip()
    text = f"{CORRECTED_PREFIX}（程序原结论 {record['program_status']}{'：' + program[:300] if program else ''}）：{record['reason']}"
    item = {
        "clause_id": finding["clause_id"],
        "classification": finding.get("classification") or "",
        "item": finding.get("item") or "",
        "priority": finding.get("priority") or "",
        "status": record["status"],
        "suggestions": text,
        "evidence": {
            "schema_version": "1",
            "required": "structure_or_visual",
            "used": "both" if len(kinds) == 2 else kinds.pop(),
            "source_format": fmt,
            "prepared_format": "xlsx" if fmt == "doc" else fmt,
            "sources": record["evidence"],
            "diagnostics": [],
        },
        "checks_performed": len(record["evidence"]),
        # A meaning the user has not confirmed still cannot pass.
        "clarifications": finding.get("clarifications") or [],
        "evidence_file_version_ids": sorted(versions),
    }
    return item


def merge_reviews(snapshot: ExecutionSnapshot, result: dict[str, Any], raw: object) -> tuple[dict[str, Any], dict[str, dict[str, str]], dict[tuple[str, str], dict[str, Any]]]:
    """Apply checked corrections to the validated program result.

    Returns the merged result, each target's deep state (completed/degraded) and the
    per-clause review records. A target whose review output is malformed keeps its
    program result and is reported as not deep-reviewed.
    """
    from audit.checker_core import validate_results

    targets = {target.file_version_id: target for target in snapshot.targets}
    if not isinstance(raw, dict) or set(raw) != set(targets):
        raise ValueError("deep_output_invalid")
    known = {item.file_version_id for item in snapshot.inputs}
    filenames = {item.file_version_id: item.filename for item in snapshot.inputs}
    states: dict[str, dict[str, str]] = {}
    records: dict[tuple[str, str], dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    for version, target in targets.items():
        program = [dict(item) for item in result["findings"] if item["file_version_id"] == version]
        state = raw[version]
        if isinstance(state, dict) and isinstance(state.get("usage"), dict):
            logger.info("deep_review_usage %s %s", version, json.dumps(state["usage"]))
        if isinstance(state, dict) and state.get("state") == "degraded" and state.get("reason") in DEGRADE_REASONS:
            if state.get("detail"):
                logger.warning("deep_review_degraded %s %s: %s", version, state["reason"], str(state["detail"])[:300])
            states[version] = {"state": "degraded", "reason": state["reason"]}
            findings.extend(program)
            continue
        try:
            if not isinstance(state, dict) or state.get("state") != "completed" or not isinstance(state.get("reviews"), list):
                raise ValueError("state")
            by_clause = {str(item["clause_id"]): item for item in program}
            checked = [_host_record(item, by_clause.get(str(item.get("clause_id")) if isinstance(item, dict) else "") or {}, target, known) for item in state["reviews"]]
            if sorted(item["clause_id"] for item in checked) != sorted(by_clause):
                raise ValueError("clauses")
            merged = []
            for record in checked:
                finding = by_clause[record["clause_id"]]
                corrected = _corrected_item(finding, record, filenames[version]) if record["decision"] == "corrected" else None
                if record["decision"] == "corrected" and corrected is None:
                    record = {key: record[key] for key in ("clause_id", "program_status", "reason")} | {"decision": "rejected", "proposed_status": record["status"], "rejection": "evidence_format"}
                merged.append(corrected or finding)
                records[(version, record["clause_id"])] = record
            findings.extend(validate_results(merged, target=target, inputs=snapshot.inputs)["findings"])
            states[version] = {"state": "completed"}
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("deep_review_invalid %s: %s", version, str(exc)[:200])
            for clause in [key for key in records if key[0] == version]:
                records.pop(clause)
            states[version] = {"state": "degraded", "reason": "deep_review_invalid"}
            findings.extend(program)
    return {**result, "findings": findings}, states, records
