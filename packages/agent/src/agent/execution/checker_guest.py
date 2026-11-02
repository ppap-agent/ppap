"""Trial and execute one fixed checker program inside the guest."""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID, uuid4

from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.checker_spec import CheckerArtifact, CheckerJobSpec, CheckerProgramBundle, program_digest
from agent.execution.guest_model import ModelProtocolError

T = TypeVar("T", bound=BaseModel)
MAX_TRIALS = 6
MAX_FILE_BYTES = 256 * 1024
GUIDE = Path(__file__).resolve().parents[1] / "skill" / "references" / "checker-development.md"
TEMPLATE = Path(__file__).resolve().parents[1] / "skill" / "references" / "checker-template.py"


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


def _drop_task_path(directory: Path) -> None:
    try:
        sys.path.remove(str(directory))
    except ValueError:
        pass


def _load_factory(directory: Path) -> tuple[Callable, str]:
    module_name = f"ppap_task_checker_{uuid4().hex}"
    sys.modules.pop("resources", None)
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location(module_name, directory / "checker.py")
        if spec is None or spec.loader is None:
            raise CheckerTrialError("无法加载 checker.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        factory = getattr(module, "create_audit", None)
        if not callable(factory):
            raise CheckerTrialError("程序缺少 create_audit")
    except CheckerTrialError:
        sys.modules.pop(module_name, None)
        sys.modules.pop("resources", None)
        _drop_task_path(directory)
        raise
    except Exception as exc:
        sys.modules.pop(module_name, None)
        sys.modules.pop("resources", None)
        _drop_task_path(directory)
        raise CheckerTrialError(f"程序无法加载: {type(exc).__name__}: {exc}") from None
    return factory, module_name


def _release_module(module_name: str) -> None:
    sys.modules.pop(module_name, None)
    sys.modules.pop("resources", None)


def _fault_detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ")[:500]


def _fault_result(checker: object, exc: Exception) -> AuditResultItem:
    rule_item = getattr(checker, "rule_item", None)
    clause_id = str(getattr(rule_item, "id", "") or "").strip()
    if not clause_id:
        raise CheckerTrialError(f"试跑失败: {_fault_detail(exc)}")
    category = str(getattr(rule_item, "category", "") or "")
    text = str(getattr(rule_item, "rule_to_check", "") or "")
    priority = str(getattr(rule_item, "priority", "") or "") or "P0"
    return AuditResultItem(
        classification="规则审核",
        item=category or text or f"条款 {clause_id}",
        priority=priority,
        status="ERR",
        suggestions=f"条款 {clause_id} 技术失败: {_fault_detail(exc)}",
        clause_id=clause_id,
    )


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
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = directory / name
        if target.name != name:
            raise CheckerTrialError("程序路径无效")
        target.write_bytes(content)
    factory, module_name = _load_factory(directory)
    audit = None
    try:
        try:
            audit = factory(audit_file, rule, group_context)
        except TypeError:
            audit = factory(audit_file, rule)
        checks = getattr(audit, "checks", None)
        if not checks:
            raise CheckerTrialError("程序没有检查项。类必须设置 CLAUSE_BOUND_CHECKS = True，并用 @rule 的 clause_id 绑定每个生效条款。")
        results: list[AuditResultItem] = []
        selected = set(only_clauses) if only_clauses is not None else None
        ran = False
        for checker in checks:
            clause_id = str(getattr(getattr(checker, "rule_item", None), "id", "") or "").strip()
            if selected is not None and clause_id not in selected:
                continue
            ran = True
            binding_error = str(getattr(checker, "binding_error", "") or "").strip()
            if binding_error and not isolate_faults:
                clause_id = str(getattr(getattr(checker, "rule_item", None), "id", "") or "").strip() or "?"
                raise CheckerTrialError(f"条款 {clause_id} 绑定错误: {binding_error}; {_result_brief(results)}")
            try:
                result = checker.check()
                if result is None:
                    result = _missing_clause_result(checker)
                results.append(AuditResultItem.model_validate(result.model_dump()))
            except CheckerTrialError:
                raise
            except (AttributeError, ValidationError) as exc:
                if isolate_faults:
                    results.append(_fault_result(checker, exc))
                    continue
                clause_id = str(getattr(getattr(checker, "rule_item", None), "id", "") or "").strip() or "?"
                raise CheckerTrialError(f"条款 {clause_id} 检查结果无效: {_fault_detail(exc)}; {_result_brief(results)}") from None
            except Exception as exc:
                if isolate_faults:
                    results.append(_fault_result(checker, exc))
                    continue
                clause_id = str(getattr(getattr(checker, "rule_item", None), "id", "") or "").strip() or "?"
                raise CheckerTrialError(f"条款 {clause_id} 试跑失败: {_fault_detail(exc)}; {_result_brief(results)}") from None
        if selected is not None and not ran:
            raise CheckerTrialError(f"局部试跑没有命中检查入口: {', '.join(only_clauses or [])}")
        return results
    except CheckerTrialError:
        raise
    except Exception as exc:
        raise CheckerTrialError(f"试跑失败: {type(exc).__name__}: {exc}") from None
    finally:
        close = getattr(audit, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        _release_module(module_name)
        _drop_task_path(directory)


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


def _missing_clause_result(checker: object) -> AuditResultItem:
    rule_item = getattr(checker, "rule_item", None)
    clause_id = str(getattr(rule_item, "id", "") or "").strip()
    if not clause_id:
        raise CheckerTrialError("检查没有返回结果")
    category = str(getattr(rule_item, "category", "") or "")
    text = str(getattr(rule_item, "rule_to_check", "") or "")
    priority = str(getattr(rule_item, "priority", "") or "") or "P0"
    return AuditResultItem(
        classification="规则审核",
        item=category or text or f"条款 {clause_id}",
        priority=priority,
        status="ERR",
        suggestions=f"条款 {clause_id} 没有返回结果",
        clause_id=clause_id,
    )


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


def _development_previews(audit_file: AuditInput, group_context: GroupContextInput | None) -> str:
    """Preview the target and related files. Related cells stay on their own file."""
    blocks: list[str] = []
    target_version = str((audit_file.file_metadata or {}).get("file_version_id") or audit_file.audit_id)
    target = _workbook_preview(str(audit_file.file_url or ""))
    if target:
        blocks.append(f"目标文件 {target_version}\n{target}")
    if group_context is not None:
        shown = 0
        for audit_id in sorted(group_context.by_audit_id):
            if shown >= 4:
                blocks.append("还有更多关联文件，预览没有列出。")
                break
            item = group_context.by_audit_id[audit_id]
            preview = _workbook_preview(str(item.file_url or ""), max_sheets=1, max_rows=4)
            if not preview:
                continue
            role = str((item.file_metadata or {}).get("role") or "")
            blocks.append(f"关联文件 {audit_id}，角色 {role}。不要把这里的单元格记到目标文件。\n{preview}")
            shown += 1
    return "\n\n".join(blocks)[:8000]


def _input_view(audit_file: AuditInput, group_context: GroupContextInput | None) -> dict[str, object]:
    related: list[dict[str, str]] = []
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
                    "rules": [dict(clause) for clause in rule.rules] if rule is not None else [],
                    "appendix": [dict(item) for item in rule.appendix] if rule is not None else [],
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
            "不要把已完成条款的 NG、UNABLE 或 NA 改成 PASS。",
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
            "不要把已完成条款的 NG、UNABLE 或 NA 改成 PASS。",
        )
    )


def _prompt(
    rule: RuleInput,
    feedback: str | None,
    prior: dict[str, str] | None,
    audit_file: AuditInput,
    group_context: GroupContextInput | None,
    appendix: list[dict[str, Any]] | None = None,
    *,
    feedback_kind: str | None = None,
) -> str:
    guide = GUIDE.read_text(encoding="utf-8")
    parts = [
        guide,
        "",
        "本次生效条款：",
        _encoded({"rules": rule.model_dump(mode="json")["rules"]}).decode(),
        "",
        "本次文件：",
        _encoded(_input_view(audit_file, group_context)).decode(),
    ]
    preview = _development_previews(audit_file, group_context)
    if preview:
        parts.extend(["", preview])
    confirmed = list(appendix or [])
    if confirmed:
        parts.extend(["", "本次已确认附录。它是规则的一部分，不是上次结论或系统建议：", _encoded(confirmed).decode()])
    if prior:
        parts.extend(["", "可作起点的旧程序。必须按本次输入重新核对，不能直接沿用到其他文件：", _encoded(prior).decode()])
    if feedback and feedback_kind == "review":
        parts.extend(["", feedback])
    elif feedback and feedback_kind == "partial":
        parts.extend(["", feedback])
    elif feedback:
        parts.extend(["", "上次试跑失败：", feedback[:2000], "已完成条款的 NG、UNABLE 或 NA 不要改成 PASS。"])
    parts.extend(["", "NG、UNABLE 和 NA 可以是最终结果。不要为了让全部条款变成 PASS 而修改判断。"])
    return "\n".join(parts)


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
    appendix: list[dict[str, Any]] | None = None,
    observations: list[str] | None = None,
) -> CheckerArtifact:
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

    for trial in range(MAX_TRIALS):
        if time.monotonic() >= deadline:
            raise TimeoutError("Deep deadline exceeded")
        stage = work / f"trial-{trial}"
        stage.mkdir(parents=True, exist_ok=True)
        if accepted_files is not None:
            for name, source in accepted_files.items():
                (stage / f"current-{name}").write_text(source, encoding="utf-8")
        (stage / "template-checker.py").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            draft = runner.run(
                stage,
                _prompt(rule, feedback, prior_files, audit_file, group_context, appendix, feedback_kind=feedback_kind),
                CheckerDraft,
                deadline,
                reasoning_effort="medium",
            )
        except ModelProtocolError:
            if accepted_encoded is None:
                raise
            raise CheckerProgramError("确认轮没有返回程序，不能固定") from None
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
        if selection is None and accepted_files is not None and draft.files == accepted_files and accepted_encoded is not None:
            return seal(accepted_encoded, trial + 1)
        if _resources_changed(previous_files, draft.files) or _shared_definition_changed(previous_files, draft.files):
            selection = None
        previous_files = dict(draft.files)
        try:
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
