"""Trial and execute one fixed checker program inside the guest."""

from __future__ import annotations

import ast
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID

from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.checker_spec import CheckerArtifact, CheckerJobSpec, CheckerProgramBundle, program_digest
from agent.execution.checker_context import task_reference, write_starting_point
from agent.execution.guest_model import ModelProtocolError

T = TypeVar("T", bound=BaseModel)
MAX_TRIALS = 6
MAX_FILE_BYTES = 256 * 1024
GUIDE = Path(__file__).resolve().parents[1] / "skill" / "references" / "checker-development.md"
PDF_GUIDE = Path(__file__).resolve().parents[1] / "skill" / "references" / "pdf-reading.md"


class CheckerProgramError(ValueError):
    """The checker program could not be fixed. This is not an empty success."""


class CheckerTrialError(CheckerProgramError):
    """One candidate failed. The development loop may revise it."""


class ProgramRunner(Protocol):
    input_tokens: int
    output_tokens: int

    def run(self, work: Path, prompt: str, schema: type[T], deadline: float, *, reasoning_effort: str) -> T: ...


class CheckerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint: str = Field(pattern=r"^checker:create_audit$")
    files: dict[str, str]
    clauses: list[str] = Field(default_factory=list)
    # A typed runner may submit a draft directly. File readers must distinguish
    # explicit completion from files recovered after an interrupted model call.
    submitted: bool = True


def _encoded(value: object) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _clause_ids(rule: RuleInput) -> list[str]:
    ids: list[str] = []
    for raw in rule.rules:
        if isinstance(raw, dict):
            clause_id = str(raw.get("id") or "").strip()
        else:
            clause_id = str(getattr(raw, "id", "") or "").strip()
        if not clause_id:
            raise CheckerProgramError("生效规则缺少条款 ID")
        ids.append(clause_id)
    if not ids:
        raise CheckerProgramError("生效规则没有检查项")
    if len(ids) != len(set(ids)):
        raise CheckerProgramError("生效规则包含重复条款 ID")
    return ids


def _validate_source(files: dict[str, str]) -> dict[str, bytes]:
    if set(files) - {"checker.py", "resources.py"} or "checker.py" not in files:
        raise CheckerTrialError("程序只能包含 checker.py 和可选的 resources.py")
    encoded: dict[str, bytes] = {}
    for name, source in files.items():
        if not isinstance(source, str) or not source.strip():
            raise CheckerTrialError(f"{name} 没有可执行内容")
        raw = source.encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            raise CheckerTrialError(f"{name} 超出程序大小限制")
        try:
            compile(source, name, "exec")
        except SyntaxError as exc:
            raise CheckerTrialError(f"{name} 语法错误: {exc.msg}") from exc
        encoded[name] = raw
    return encoded


def run_program(
    files: dict[str, bytes],
    directory: Path,
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None,
    *,
    isolate_faults: bool = False,
    only_clauses: list[str] | None = None,
) -> list[AuditResultItem]:
    """Adapt authored trials to the same guest Core; never start another process.

    Supplied files already belong to the authorized guest. A partial trial
    binds only explicitly requested IDs and makes no claim about other clauses.
    """
    import hashlib

    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest, clause_ids, rule_digest
    from audit.standard_suites import library_revision

    from agent.execution.audit_runtime import AuditRunError, run_audit

    directory.mkdir(parents=True, exist_ok=True)
    try:
        body = rule.model_dump(mode="json")
        ids = clause_ids(body)
        selected = tuple(only_clauses) if only_clauses is not None else ids
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(ids):
            raise CheckerTrialError("局部试跑没有命中检查入口或包含重复条款")

        def identity(item: AuditInput) -> str:
            return str((item.file_metadata or {}).get("file_version_id") or item.audit_id)

        by_id = {identity(item): item for item in (group_context.by_audit_id.values() if group_context else ())}
        by_id[identity(audit_file)] = audit_file
        by_type: dict[str, str | None] = {
            identity(item): str(kind.get_type_name() if hasattr(kind, "get_type_name") else kind) for kind, items in (group_context.by_doc_type.items() if group_context else ()) for item in items
        }
        by_type[identity(audit_file)] = rule.type
        sources, inputs = {}, []
        for version, item in by_id.items():
            path = Path(item.file_url)
            data = path.read_bytes()
            expected = (item.file_metadata or {}).get("sha256")
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise CheckerTrialError("原件摘要与冻结版本不一致")
            related_rule = group_context.rules_by_id.get(version) if group_context else None
            sources[version] = data
            inputs.append(
                FrozenInput(
                    version,
                    hashlib.sha256(data).hexdigest(),
                    "target" if version == identity(audit_file) else "reference",
                    str((item.file_metadata or {}).get("original_filename") or path.name),
                    document_type=by_type.get(version),
                    file_id=item.audit_id,
                    customer=item.customer,
                    model=item.model,
                    part=item.part,
                    project_id=item.project_id,
                    rule_revision_id=related_rule.rule_id if related_rule else "",
                    rules=related_rule.model_dump(mode="json") if related_rule else None,
                    appendix=tuple(related_rule.appendix) if related_rule else (),
                )
            )
        digest = checker_digest(files)
        target = FrozenTarget(
            identity(audit_file),
            rule.rule_id or "authored-trial",
            "authored-trial",
            rule_digest(body, rule.appendix),
            body,
            RuleBinding("authored-trial", digest, dict.fromkeys(selected, "checker:create_audit")),
            tuple(rule.appendix),
        )
        snapshot = ExecutionSnapshot(
            "authorized-guest", "guest-initiator", tuple(inputs), (target,), "authored-trial", library_revision(), {}, round_id=(group_context.task_group_id or "") if group_context else ""
        )
        result = run_audit(snapshot, sources, {digest: files})
        results = [AuditResultItem.model_validate(item) for item in result["findings"]]
        failures = [item for item in results if item.status == "ERR" and (item.suggestions.startswith(("checker_failure:", "authored_")))]
        diagnostics = result.get("diagnostics") or []
        for diagnostic in diagnostics:
            results.append(
                AuditResultItem(
                    classification="规则审核",
                    item=diagnostic["clause_id"] or "未命名条款",
                    priority="P0",
                    status="ERR",
                    suggestions=_trial_diagnostic(diagnostic["clause_id"], diagnostic["code"]),
                    clause_id=diagnostic["clause_id"],
                )
            )
        if not isolate_faults and (failures or diagnostics):
            messages = [_trial_diagnostic(item.clause_id, item.suggestions) for item in failures]
            messages.extend(_trial_diagnostic(item["clause_id"], item["code"]) for item in diagnostics)
            raise CheckerTrialError("条款结果与生效规则不一致；" + "; ".join(messages) + "; " + _result_brief(results))
        results = [item.model_copy(update={"suggestions": _trial_diagnostic(item.clause_id, item.suggestions)}) if item.status == "ERR" else item for item in results]
        return results
    except CheckerTrialError:
        raise
    except AuditRunError as exc:
        if str(exc) == "checker_entrypoint_missing":
            raise CheckerTrialError("程序缺少 create_audit") from None
        raise CheckerTrialError(f"试跑合同失败: {str(exc)}") from None
    except Exception as exc:
        # Provider/original payloads in exception messages are not model prompts.
        raise CheckerTrialError(f"试跑合同或输入失败: {type(exc).__name__}") from None


def _trial_diagnostic(clause: str | None, code: str) -> str:
    prefix = f"条款 {clause or '?'}"
    messages = {
        "authored_pdf_spec_required": "目标 PDF 缺少类属性 PDF_SPEC。不要依赖自动预设。",
        "authored_pdf_spec_invalid": "PDF_SPEC 不合法",
        "authored_checker_missing_checks": "程序没有检查项。使用显式条款 ID 绑定检查入口。",
        "authored_checker_missing_clause_id": "检查入口缺少条款 ID",
        "authored_checker_extra_clause": f"{prefix} 绑定错误: 不在本次生效规则中",
        "authored_checker_missing_binding": f"{prefix} 绑定错误: 没有对应的检查入口",
        "authored_checker_duplicate_binding": f"{prefix} 绑定错误: 绑定了多个检查入口",
        "authored_checker_invalid_binding": f"{prefix} 绑定错误",
        "authored_checker_invalid_result": f"{prefix} 检查结果无效",
        "authored_checker_result_clause_mismatch": f"{prefix} 返回的条款 ID 与绑定不一致",
        "authored_checker_duplicate_result": f"{prefix} 有多个结果",
        "authored_checker_missing_result": f"{prefix} 没有结果",
        "checker_missing_result": f"{prefix} 没有返回结果",
    }
    if code.startswith("checker_failure:"):
        return f"{prefix} 试跑失败（技术失败）: {code}"
    return messages.get(code, code)


def _result_brief(results: list[AuditResultItem]) -> str:
    parts: list[str] = []
    for item in results:
        locator = ""
        if item.evidence is not None and item.evidence.sources:
            locator = item.evidence.sources[0].locator
        text = (item.suggestions or "").replace("\n", " ")[:80]
        parts.append(f"{item.clause_id or ''}|{item.status}|{locator}|{text}")
    return "已完成=" + "; ".join(parts)[:1500]


def _rule_clause(rule: RuleInput, clause_id: str, occurrence: int) -> dict:
    seen = 0
    for raw in rule.rules:
        if isinstance(raw, dict):
            row = raw
        else:
            row = {
                "id": getattr(raw, "id", ""),
                "category": getattr(raw, "category", ""),
                "rule_to_check": getattr(raw, "rule_to_check", ""),
                "priority": getattr(raw, "priority", None),
            }
        if str(row.get("id") or "").strip() != clause_id:
            continue
        if seen == occurrence:
            return row
        seen += 1
    return {}


def _coverage_result(rule: RuleInput, clause_id: str, suggestions: str, occurrence: int = 0) -> AuditResultItem:
    row = _rule_clause(rule, clause_id, occurrence)
    category = str(row.get("category") or "").strip()
    text = str(row.get("rule_to_check") or "").strip()
    priority = str(row.get("priority") or "").strip() or "P0"
    return AuditResultItem(
        classification="规则审核",
        item=category or text or f"条款 {clause_id}",
        priority=priority,
        status="ERR",
        suggestions=suggestions,
        clause_id=clause_id,
    )


def _align_fixed_coverage(results: list[AuditResultItem], rule: RuleInput, expected: list[str]) -> list[AuditResultItem]:
    """Keep one result for each expected occurrence. Report gaps without dropping the rest."""
    expected_counts = Counter(expected)
    grouped: dict[str, list[AuditResultItem]] = {clause_id: [] for clause_id in expected_counts}
    unnamed: list[AuditResultItem] = []
    extras: list[AuditResultItem] = []
    for result in results:
        clause_id = str(result.clause_id or "").strip()
        if not clause_id:
            unnamed.append(result)
        elif clause_id in grouped:
            grouped[clause_id].append(result)
        else:
            extras.append(result)
    multiple = {clause_id for clause_id, need in expected_counts.items() if len(grouped[clause_id]) > need}
    cursors = {clause_id: 0 for clause_id in expected_counts}
    aligned: list[AuditResultItem] = []
    emitted: set[str] = set()
    for clause_id in expected:
        if clause_id in multiple:
            if clause_id in emitted:
                continue
            emitted.add(clause_id)
            for occurrence in range(expected_counts[clause_id]):
                aligned.append(_coverage_result(rule, clause_id, f"条款 {clause_id} 有多个结果", occurrence))
            continue
        found = grouped[clause_id]
        position = cursors[clause_id]
        cursors[clause_id] = position + 1
        if position < len(found):
            aligned.append(found[position])
        else:
            aligned.append(_coverage_result(rule, clause_id, f"条款 {clause_id} 没有结果", position))
    aligned.extend(unnamed)
    aligned.extend(extras)
    return aligned


def _require_coverage(results: list[AuditResultItem], rule: RuleInput, *, allow_extra: bool = False) -> list[AuditResultItem]:
    expected = _clause_ids(rule)
    actual = [result.clause_id or "" for result in results]
    expected_counts = Counter(expected)
    if allow_extra:
        actual_counts = Counter(str(clause_id or "").strip() for clause_id in actual if str(clause_id or "").strip() in expected_counts)
        if actual_counts == expected_counts:
            return results
        return _align_fixed_coverage(results, rule, expected)
    if Counter(actual) != Counter(expected):
        raise CheckerTrialError(f"条款结果与生效规则不一致: expected={expected}, actual={actual}; {_result_brief(results)}")
    return results


def _write_bundle(work: Path, bundle: CheckerProgramBundle) -> None:
    output = work / "result"
    output.mkdir(parents=True, exist_ok=True)
    target = output / "bundle.json"
    payload = bundle.model_dump_json().encode()
    with target.open("xb") as stream:
        stream.write(payload)


def _preview_cell(value: object) -> str:
    if value is None:
        return "空白"
    return str(value).replace("\n", " ").replace("\r", " ")[:40]


def _workbook_preview(file_url: str, *, max_sheets: int = 2, max_rows: int = 6) -> str | None:
    """Show the start of a local workbook. This is not a scan and not a verdict."""
    path = Path(str(file_url or ""))
    if not path.is_file():
        return None
    if path.suffix.lower() == ".pdf":
        return "这是 PDF。这里没有单元格预览。保留页码和区域，转换缺口不能判为通过。"
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return None
    try:
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=False)
    except Exception:
        return None
    lines = ["这是文件开头的预览，不是完整扫描，也不能当作判定。"]
    try:
        for sheet in book.worksheets[:max_sheets]:
            lines.append(f"工作表 {sheet.title}")
            shown = 0
            for index, row in enumerate(sheet.iter_rows(max_row=max_rows, max_col=6, values_only=True), start=1):
                lines.append(f"{index}: " + " | ".join(_preview_cell(value) for value in row))
                shown = index
            total = sheet.max_row if isinstance(sheet.max_row, int) else shown
            if total > shown:
                lines.append(f"预览停在第 {shown} 行，后面的行仍须由程序扫描。")
        if len(book.worksheets) > max_sheets:
            lines.append("还有更多工作表，预览没有列出。")
    finally:
        book.close()
    return "\n".join(lines)[:2000]


def _development_previews(
    audit_file: AuditInput,
    group_context: GroupContextInput | None,
    converted: Mapping[str, str] | None = None,
) -> str:
    """Preview the target and related files. Related cells stay on their own file."""
    blocks: list[str] = []
    converted_paths = converted or {}
    target_version = _file_version(audit_file)
    target_path = converted_paths.get(target_version) or str(audit_file.file_url or "")
    target = _workbook_preview(target_path)
    if target:
        blocks.append(f"目标文件 {target_version}\n{target}")
    if group_context is not None:
        shown = 0
        for audit_id in sorted(group_context.by_audit_id):
            if shown >= 4:
                blocks.append("还有更多关联文件，预览没有列出。")
                break
            item = group_context.by_audit_id[audit_id]
            if item.audit_id == audit_file.audit_id:
                continue
            version = _file_version(item)
            preview = _workbook_preview(converted_paths.get(version) or str(item.file_url or ""), max_sheets=1, max_rows=4)
            if not preview:
                continue
            role = str((item.file_metadata or {}).get("role") or "")
            blocks.append(f"关联文件 {audit_id}，角色 {role}。不要把这里的单元格记到目标文件。\n{preview}")
            shown += 1
    return "\n\n".join(blocks)[:8000]


def _input_view(audit_file: AuditInput, group_context: GroupContextInput | None) -> dict[str, object]:
    related: list[dict[str, object]] = []
    if group_context is not None:
        for audit_id in sorted(group_context.by_audit_id):
            item = group_context.by_audit_id[audit_id]
            metadata = item.file_metadata or {}
            rule = group_context.rules_by_id.get(audit_id)
            related.append(
                {
                    "audit_id": audit_id,
                    "file_url": item.file_url,
                    "role": str(metadata.get("role") or ""),
                    "original_filename": str(metadata.get("original_filename") or ""),
                    "v4_source_type": metadata.get("v4_source_type"),
                    "rule": rule.model_dump(mode="json") if rule is not None else None,
                }
            )
    metadata = audit_file.file_metadata or {}
    return {
        "audit_id": audit_file.audit_id,
        "file_url": audit_file.file_url,
        "role": str(metadata.get("role") or ""),
        "related": related,
    }


def _rule_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id == "rule"
    return isinstance(target, ast.Attribute) and target.attr == "rule"


def _shared_surface(source: str) -> str:
    """Return checker structure without clause-method bodies.

    A change here is a shared definition. The host cannot name every affected
    clause, so the next trial runs the full effective set.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    def normalize(node: ast.AST) -> None:
        for child in list(ast.iter_child_nodes(node)):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(_rule_decorator(item) for item in child.decorator_list):
                child.body = [ast.Pass()]
                continue
            normalize(child)

    normalize(tree)
    return ast.dump(tree, include_attributes=False)


def _resources_changed(previous: dict[str, str] | None, current: dict[str, str]) -> bool:
    """Adding, removing, or editing resources.py can affect every clause."""
    return (previous or {}).get("resources.py") != current.get("resources.py")


def _shared_definition_changed(previous: dict[str, str] | None, current: dict[str, str]) -> bool:
    before = (previous or {}).get("checker.py")
    after = current.get("checker.py")
    if before is None or after is None or before == after:
        return False
    return _shared_surface(before) != _shared_surface(after)


def _trial_selection(clauses: list[str], rule: RuleInput) -> list[str] | None:
    """Return named clauses for a partial trial. None runs the full effective set."""
    cleaned = [item.strip() for item in clauses if isinstance(item, str) and item.strip()]
    if not cleaned:
        return None
    expected = _clause_ids(rule)
    unknown = [item for item in cleaned if item not in expected]
    if unknown:
        raise CheckerTrialError(f"局部试跑条款不在本次生效规则中: {', '.join(unknown)}")
    selected: list[str] = []
    for item in cleaned:
        if item not in selected:
            selected.append(item)
    if Counter(selected) == Counter(expected):
        return None
    return selected


def _partial_feedback(clause_ids: list[str], results: list[AuditResultItem]) -> str:
    return "\n".join(
        (
            f"本次已有结果的条款：{', '.join(clause_ids)}",
            _result_brief(results),
            "修复读取或实现错误后可以改变判定；业务标准仍以本次条款为准。",
        )
    )


def _observation_questions(results: list[AuditResultItem]) -> list[str]:
    """Keep each asked question, its candidates and sources, and the fact that no answer was chosen."""
    encoded_items: list[str] = []
    for result in results:
        for question in result.clarifications or []:
            if not isinstance(question, dict):
                continue
            encoded_items.append(
                _encoded(
                    {
                        "answer": None,
                        "candidates": question.get("candidates") or [],
                        "clause_id": str(result.clause_id or question.get("clause_id") or ""),
                        "id": question.get("id") or "",
                        "question": question.get("question") or "",
                        "sources": question.get("sources") or [],
                    }
                ).decode()
            )
    return encoded_items


def _review_feedback(results: list[AuditResultItem]) -> str:
    return "\n".join(
        (
            "上次试跑结果：",
            _result_brief(results),
            "这些结果只说明程序跑完，不说明业务判断正确。",
            "修复读取或实现错误后可以改变判定；业务标准仍以本次条款为准。",
        )
    )


def _prompt(
    rule: RuleInput,
    feedback: str | None,
    prior: dict[str, str] | None,
    audit_file: AuditInput,
    group_context: GroupContextInput | None,
    *,
    feedback_kind: str | None = None,
    layout: PdfLayout | None = None,
    starting_point: str = "",
    development_feedback: str | None = None,
    selected_source: bool = False,
) -> str:
    guide = GUIDE.read_text(encoding="utf-8")
    rule_heading = (
        "本次完整规则输入（rules 与 appendix 是本次生效规则；只修订 selected/ 中的已选 Checker，不要改用其他版本）："
        if selected_source
        else "本次完整规则输入（rules 与 appendix 是本次生效规则，预置来源只用于选择参考实现）："
    )
    parts = [
        guide,
        starting_point,
        "",
        rule_heading,
        _encoded(rule.model_dump(mode="json")).decode(),
        "",
        "本次文件：",
        _encoded(_input_view(audit_file, group_context)).decode(),
    ]
    preview = _development_previews(audit_file, group_context, None if layout is None else layout.previews)
    if preview:
        parts.extend(["", preview])
    if development_feedback:
        parts.extend(["", "用户反馈与已确认训练样本：", development_feedback])
    if rule.appendix:
        parts.extend(["", "上述 appendix 是本次已确认附录，是规则的一部分，不是上次结论或系统建议。"])
    if prior:
        parts.extend(["", "可作起点的旧程序。必须按本次输入重新核对，不能直接沿用到其他文件：", _encoded(prior).decode()])
    if feedback and feedback_kind == "review":
        parts.extend(["", feedback])
    elif feedback and feedback_kind == "partial":
        parts.extend(["", feedback])
    elif feedback:
        parts.extend(["", "上次试跑失败：", feedback[:6000], "修复读取或实现错误后，按同一规则重新判断；保留修订依据。"])
    parts.extend(["", "NG、UNABLE 和 NA 可以是最终结果。不要为了让全部条款变成 PASS 而修改判断。"])
    if layout is not None:
        parts.extend(["", "" if layout.native else PDF_GUIDE.read_text(encoding="utf-8"), "", layout.instructions])
    return "\n".join(parts)


def _is_pdf(file_url: str) -> bool:
    return str(file_url or "").lower().split("?", 1)[0].endswith(".pdf")


NO_TEXT_REASON = "PDF 没有可用文字网格，不能审核"


@dataclass
class PdfLayout:
    snippet: str
    previews: dict[str, str]
    instructions: str
    native: bool = False


def _file_version(audit_file: AuditInput) -> str:
    return str((audit_file.file_metadata or {}).get("file_version_id") or audit_file.audit_id)


def _pdf_documents(audit_file: AuditInput, group_context: GroupContextInput | None) -> list[tuple[str, str, bool]]:
    found: list[tuple[str, str, bool]] = []
    if _is_pdf(str(audit_file.file_url or "")):
        found.append((_file_version(audit_file), str(audit_file.file_url or ""), True))
    if group_context is None:
        return found
    for audit_id in sorted(group_context.by_audit_id):
        item = group_context.by_audit_id[audit_id]
        if item.audit_id == audit_file.audit_id or not _is_pdf(str(item.file_url or "")):
            continue
        found.append((_file_version(item), str(item.file_url or ""), False))
    return found


def _pdf_text(path: str) -> str:
    file = Path(path)
    if not file.is_file():
        return ""
    try:
        from documents.extractors.pdf import PDFExtractor

        pages = PDFExtractor().extract_pages_text(file.read_bytes())
    except Exception:
        return ""
    return "\n".join(page.strip() for page in pages if page and page.strip())


def _preset_catalog() -> tuple[tuple[str, Any], ...]:
    import documents.pdf as pdf

    found: list[tuple[str, Any]] = []
    for name in sorted(dir(pdf)):
        if not name.startswith("PPAP_") or not name.endswith("_SPEC"):
            continue
        value = getattr(pdf, name)
        if isinstance(value, pdf.PdfSpec):
            found.append((name, value))
    return tuple(found)


def _known_preset(text: str) -> tuple[str, Any] | None:
    """A preset is known only when its own markers occur in this file. Document type is not used."""
    from documents.pdf._grouping import _matches_rule, _normalize_rule, _normalize_text

    normalized = _normalize_text(text)
    if not normalized:
        return None
    matches: list[tuple[str, Any]] = []
    for name, spec in _preset_catalog():
        rules = spec.grouping.section_rules
        markers = tuple(marker.strip() for marker in spec.grouping.title_markers if marker.strip())
        rule_match = False
        for rule in rules:
            normalized_rule = _normalize_rule(rule)
            tokens = normalized_rule.any_of + normalized_rule.all_of
            if not _matches_rule(normalized, normalized_rule):
                continue
            if any(len(token) >= 4 and token in normalized for token in tokens):
                rule_match = True
                break
        marker_match = bool(markers) and all(len(_normalize_text(marker)) >= 4 and _normalize_text(marker) in normalized for marker in markers)
        if rules and not rule_match:
            continue
        if markers and not marker_match:
            continue
        if not rules and not markers:
            continue
        matches.append((name, spec))
    if len(matches) == 1:
        return matches[0]
    return None


def _safe_stem(version: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in version)[:80]
    return cleaned or "pdf"


def _workbook_has_value(path: Path) -> bool:
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=False)
    try:
        for sheet in book.worksheets:
            for row in sheet.iter_rows(max_row=200, max_col=40, values_only=True):
                if any(cell not in (None, "") for cell in row):
                    return True
    finally:
        book.close()
    return False


def _convert_grid(path: str, spec: Any, work: Path, version: str) -> tuple[bool, str, str | None]:
    """Convert with the given spec only. Do not fall back to OCR or a document-type preset."""
    from documents.pdf import convert_pdf_to_excel

    destination = work / "converted" / f"{_safe_stem(version)}.xlsx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        convert_pdf_to_excel(path, str(destination), spec=spec)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", None
    if not destination.is_file() or not _workbook_has_value(destination):
        return False, "转换后没有可用表格", None
    return True, "", str(destination)


def _unable_checker(reason: str) -> str:
    """Use the existing single-check contract without opening an unreadable PDF."""
    return f"""from audit.contracts import AuditResultItem, RuleItem
from audit.engine.checkers.rule_check import RuleCheck
from documents.pdf import PdfSpec

class TaskAudit:
    PDF_SPEC = PdfSpec()
    def __init__(self, audit_file, rule, group_context=None):
        self.checks = tuple(RuleCheck(RuleItem.model_validate({{"category": "", "rule_to_check": "", "type_of_rule": "", **row}}), lambda row=row: AuditResultItem(
            classification="规则审核", item=row.get("category") or row.get("rule_to_check") or row["id"],
            priority=row.get("priority") or "P0", status="UNABLE", suggestions={reason!r}, clause_id=row["id"],
        )) for row in rule.rules)
    def close(self):
        pass

def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def _prepare_pdf_layout(
    audit_file: AuditInput,
    group_context: GroupContextInput | None,
    work: Path,
    *,
    native: bool = False,
) -> PdfLayout | None:
    """Native observations and suggested specs; the checker loop can revise both."""
    documents = _pdf_documents(audit_file, group_context)
    if not documents:
        return None
    previews: dict[str, str] = {}
    notes: list[str] = []
    lines = ["from documents.pdf import PdfSpec", "", "SPECS = {}"]
    for version, path, is_target in documents:
        text = _pdf_text(path)
        if not text.strip():
            if is_target:
                raise _TargetUnread(NO_TEXT_REASON)
            note = f"{version} 没有可用文字网格。依赖它且未能读取的条款保持 UNABLE。"
            notes.append(note)
            lines.append(f"# {note}")
            continue
        notes.append(f"文件 {version} 的原生文字预览（最多 6000 字，非完整范围）：\n{text[:6000]}")
        if native:
            continue
        preset = _known_preset(text)
        if preset is None:
            lines.append(f"SPECS[{version!r}] = PdfSpec()  # 初始配置，按这份原件修订")
            continue
        ok, detail, preview = _convert_grid(path, preset[1], work, version)
        if ok and preview is not None:
            previews[version] = preview
            lines.extend([f"from documents.pdf import {preset[0]}", f"SPECS[{version!r}] = {preset[0]}"])
        else:
            lines.append(f"SPECS[{version!r}] = PdfSpec()")
            notes.append(f"{version} 的候选预设转换失败：{detail[:500]}")
    if native:
        return PdfLayout(
            snippet="",
            previews={},
            instructions="沿用 UAES NativeFacts 读取原始 PDF 文字与位置；需要表格网格时显式定义 PdfSpec。视觉事实无可重放证据时为 UNABLE。\n" + "\n\n".join(notes),
            native=True,
        )
    notes.append("预设只由文件自身标记提供候选；不要按文档类型或文件名选择预设。非空表格不证明列、范围和原件映射正确。")
    snippet = "\n".join(lines) + "\n"
    return PdfLayout(
        snippet=snippet,
        previews=previews,
        instructions="PDF 配置与 checker 在同一个循环中修订。template-pdf-spec.py 是候选；修改资源后重新转换并完整试跑。\n" + "\n\n".join(notes),
    )


class _TargetUnread(CheckerTrialError):
    """The target PDF has no usable text grid. Do not start or continue the checker model."""


_SELECTED_SOURCE_NAMES = frozenset({"checker.py", "rules.json", "resources.py", "appendix.json", "templates.json", "tests.json"})


def _selected_checker_bytes(source_files: Mapping[str, str] | None) -> dict[str, str]:
    if not source_files:
        return {}
    if not isinstance(source_files, Mapping) or "checker.py" not in source_files or not set(source_files) <= _SELECTED_SOURCE_NAMES:
        raise CheckerProgramError("已选 Checker 源文件不完整")
    if any(not isinstance(name, str) or not isinstance(body, str) or not body.strip() for name, body in source_files.items()):
        raise CheckerProgramError("已选 Checker 源文件不完整")
    return dict(source_files)


def develop_checker(
    *,
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None,
    digest: str,
    executor_revision: str,
    runner: ProgramRunner,
    work: Path,
    deadline: float,
    prior_files: dict[str, str] | None = None,
    source_files: Mapping[str, str] | None = None,
    observations: list[str] | None = None,
    development_feedback: str | None = None,
    reasoning_effort: str = "medium",
) -> CheckerArtifact:
    if reasoning_effort not in {"xhigh", "high", "medium", "low"}:
        raise CheckerProgramError("不支持的 reasoning effort")
    _clause_ids(rule)
    spec = CheckerJobSpec(execution_id=UUID(int=0), operation="adapt-checker", input_digest=digest, executor_revision=executor_revision)
    execution_id = spec.expected_execution_id()
    feedback: str | None = None
    feedback_kind: str | None = None
    last_error: CheckerTrialError | None = None
    accepted_files: dict[str, str] | None = None
    accepted_encoded: dict[str, bytes] | None = None
    accepted_brief: str | None = None
    accepted_questions: list[str] = []
    previous_files: dict[str, str] | None = None
    latest_files: dict[str, str] | None = prior_files

    def seal(encoded_files: dict[str, bytes], requests: int) -> CheckerArtifact:
        artifact = CheckerArtifact(input_digest=digest, executor_revision=executor_revision, entrypoint="checker:create_audit", files=encoded_files)
        briefs = [accepted_brief] if accepted_brief else []
        briefs.extend(accepted_questions)
        if observations is not None:
            observations.extend(briefs)
        _write_bundle(
            work,
            CheckerProgramBundle(
                operation="adapt-checker",
                execution_id=execution_id,
                input_digest=digest,
                executor_revision=executor_revision,
                artifact=artifact,
                observations=briefs,
                input_tokens=int(getattr(runner, "input_tokens", 0) or 0),
                output_tokens=int(getattr(runner, "output_tokens", 0) or 0),
                model_requests=requests,
            ),
        )
        return artifact

    selected = _selected_checker_bytes(source_files)
    editing_selected = prior_files is not None or "checker.py" in selected
    if editing_selected:
        native = False
    else:
        from audit.checkers.v4.uaes.native import UaesSourceAgent

        reference = task_reference(audit_file, rule)
        native = reference is not None and issubclass(reference, UaesSourceAgent)
    try:
        layout = _prepare_pdf_layout(audit_file, group_context, work, native=native)
    except _TargetUnread as exc:
        encoded = _validate_source({"checker.py": _unable_checker(str(exc))})
        run_program(encoded, work / "unable-program", audit_file, rule, group_context)
        return seal(encoded, 0)
    if prior_files:
        try:
            prior_results = run_program(_validate_source(prior_files), work / "prior-trial", audit_file, rule, group_context)
            _require_coverage(prior_results, rule)
            feedback = "旧程序在本次输入上的诊断（尚未确认，仍须核对本次规则和文件）：\n" + _result_brief(prior_results)
        except CheckerProgramError as exc:
            feedback = str(exc)
        feedback_kind = "failure"
        previous_files = dict(prior_files)

    for trial in range(MAX_TRIALS):
        if time.monotonic() >= deadline:
            raise TimeoutError("Deep deadline exceeded")
        stage = work / f"trial-{trial}"
        stage.mkdir(parents=True, exist_ok=True)
        if latest_files is not None:
            for name, source in latest_files.items():
                (stage / f"current-{name}").write_text(source, encoding="utf-8")
        if selected:
            chosen = stage / "selected"
            chosen.mkdir()
            for name, source in selected.items():
                (chosen / name).write_text(source, encoding="utf-8")
        if editing_selected:
            starting_point = "修订这份已选 Checker。先读取 selected/（若存在）和 current-*.py（若存在）。不要改用其他版本的实现。"
        else:
            starting_point = write_starting_point(stage, audit_file, rule)
            if latest_files is not None:
                starting_point += "\n先读取 current-checker.py 和 current-resources.py（若存在）。这是最近候选，按下面诊断继续修改；它尚未固定。"
        if layout is not None:
            (stage / "template-pdf-spec.py").write_text(layout.snippet, encoding="utf-8")
        try:
            draft = runner.run(
                stage,
                _prompt(
                    rule,
                    feedback,
                    prior_files if trial == 0 else None,
                    audit_file,
                    group_context,
                    feedback_kind=feedback_kind,
                    layout=layout,
                    starting_point=starting_point,
                    development_feedback=development_feedback,
                    selected_source=editing_selected,
                ),
                CheckerDraft,
                deadline,
                reasoning_effort=reasoning_effort,
            )
        except ModelProtocolError:
            if accepted_encoded is None:
                raise
            raise CheckerProgramError("确认轮没有返回程序，不能固定") from None
        latest_files = dict(draft.files)
        try:
            selection = _trial_selection(list(draft.clauses), rule)
        except CheckerTrialError as exc:
            previous_files = dict(draft.files)
            if draft.files != accepted_files:
                accepted_files = None
                accepted_encoded = None
                accepted_brief = None
                accepted_questions.clear()
            feedback = str(exc)
            feedback_kind = "failure"
            last_error = exc
            continue
        if draft.submitted and selection is None and accepted_files is not None and draft.files == accepted_files and accepted_encoded is not None:
            return seal(accepted_encoded, trial + 1)
        if _resources_changed(previous_files, draft.files) or _shared_definition_changed(previous_files, draft.files):
            selection = None
        previous_files = dict(draft.files)
        try:
            template_checker = stage / "template-checker.py"
            if template_checker.is_file() and draft.files.get("checker.py") == template_checker.read_text(encoding="utf-8"):
                raise CheckerTrialError("模板尚未实现本次条款，不能固定")
            encoded = _validate_source(draft.files)
            results = run_program(
                encoded,
                stage / "program",
                audit_file,
                rule,
                group_context,
                only_clauses=selection,
            )
            if selection is None:
                _require_coverage(results, rule)
        except CheckerProgramError as exc:
            if not isinstance(exc, CheckerTrialError):
                raise
            accepted_files = None
            accepted_encoded = None
            accepted_brief = None
            accepted_questions.clear()
            feedback = str(exc)
            feedback_kind = "failure"
            last_error = exc
            continue
        except ValidationError as exc:
            accepted_files = None
            accepted_encoded = None
            accepted_brief = None
            accepted_questions.clear()
            feedback = str(exc)
            feedback_kind = "failure"
            last_error = CheckerTrialError("程序输出不符合 checker 程序合同")
            continue
        if selection is not None:
            if accepted_files is not None and draft.files != accepted_files:
                accepted_files = None
                accepted_encoded = None
                accepted_brief = None
                accepted_questions.clear()
            feedback = _partial_feedback(selection, results)
            feedback_kind = "partial"
            continue
        accepted_files = dict(draft.files)
        accepted_encoded = encoded
        accepted_brief = _review_feedback(results)
        accepted_questions[:] = _observation_questions(results)
        feedback = accepted_brief
        feedback_kind = "review"
    # The last trial can succeed and still be unconfirmed. Only an unchanged
    # resubmit without a clause filter fixes the program. A missing confirmation
    # response does not.
    if accepted_encoded is not None:
        raise CheckerProgramError("程序已覆盖全部生效条款，但确认轮没有完成，不能固定")
    raise CheckerProgramError(str(last_error or "checker program was not fixed"))


def execute_fixed_checker(
    artifact: CheckerArtifact,
    *,
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None,
    work: Path,
    digest: str,
    executor_revision: str,
) -> list[AuditResultItem]:
    if artifact.input_digest != digest or artifact.executor_revision != executor_revision or artifact.entrypoint != "checker:create_audit":
        raise CheckerProgramError("固定程序与本次输入不一致")
    if program_digest(dict(artifact.files)) != artifact.program_digest:
        raise CheckerProgramError("固定程序摘要与程序字节不一致")
    results = run_program(dict(artifact.files), work / "fixed-program", audit_file, rule, group_context, isolate_faults=True)
    results = _require_coverage(results, rule, allow_extra=True)
    spec = CheckerJobSpec(execution_id=UUID(int=0), operation="execute-checker", input_digest=digest, executor_revision=executor_revision)
    _write_bundle(
        work,
        CheckerProgramBundle(
            operation="execute-checker",
            execution_id=spec.expected_execution_id(),
            input_digest=digest,
            executor_revision=executor_revision,
            results=[result.model_dump(mode="json") for result in results],
        ),
    )
    return results
