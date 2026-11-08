"""v4 L17 PDF workbook rules.

The PDF carrier delegates converted rows here. Rule bodies and the PSW helpers
they call are owned by this module. Shared engine functions stay in
``audit.engine``. This class does not subclass a versioned checker.
"""

from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal
from urllib.parse import unquote, urlparse

from documents.contracts import FileRef
from openpyxl import Workbook

from audit.checkers.v4.pfmea_local import _NON_DATA_SHEETS, _identity, _rows, pair_identity
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.contracts import AuditResultItem as AuditResult
from audit.document_tools import snapshot
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.checkers.excel_field_evidence import native_cell_evidence
from audit.engine.checkers.pdf_evidence import budget_exhausted, conversion_blocks_pass, evidence_from_conversion
from audit.engine.checkers.psw_evidence_trace import finalize_excel_psw_result
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table, get_doc_resource_by_doc_type
from audit.engine.rule_engine.contracts import DomainTokensModel, RuleResource
from audit.kinds import PpapDocumentType

logger = logging.getLogger(__name__)

_EMPTY_MARKERS = {"", "none", "nan"}
_RULE6_LABEL_NOISE_RAW = (
    "重量(kg)",
    "重量",
    "零件名称",
    "理想汽车零件号",
    "供方料号",
    "图纸号",
    "模具穴号",
    "工程更改版本",
    "版本日期",
    "供应商名称/供应商代码",
    "街道地址",
    "城市",
    "省",
    "国家",
    "顾客名称",
    "设计开发",
    "采购工程",
    "PTC",
    "customername",
    "streetaddress",
    "city",
    "province",
    "country",
    "dre",
    "pur",
    "tpm",
)
DEFAULT_NG_COLLECTION_TOPN = 5
NG_COLLECTION_TOPN_ENV = "PPAP_NG_TOPN"
RuleEvidencePhase = Literal["decision", "guard", "error"]


def rule(description: str, clause_id: str | None = None) -> Callable[[Any], Any]:
    """Mark one workbook clause. Method order is not the clause identity."""

    def decorator(func: Any) -> Any:
        func.__rule_description__ = description
        if clause_id is not None:
            text = str(clause_id).strip()
            if not text:
                raise ValueError("clause_id must not be empty")
            func.__rule_clause_id__ = text
        return func

    return decorator


def _merge_domain_tokens(base: DomainTokensModel, explicit: DomainTokensModel) -> DomainTokensModel:
    filled = {str(token).strip().lower(): str(token).strip() for token in base.filled_tokens if str(token).strip()}
    missing = {str(token).strip().lower(): str(token).strip() for token in base.missing_tokens if str(token).strip()}
    for token in explicit.filled_tokens:
        text = str(token).strip()
        if not text:
            continue
        key = text.lower()
        missing.pop(key, None)
        filled[key] = text
    for token in explicit.missing_tokens:
        text = str(token).strip()
        if not text:
            continue
        key = text.lower()
        filled.pop(key, None)
        missing[key] = text
    return DomainTokensModel(filled_tokens=tuple(filled.values()), missing_tokens=tuple(missing.values()))


def merge_rule_resources(registered: RuleResource | None, explicit: RuleResource | None) -> RuleResource | None:
    if explicit is None:
        return registered
    if registered is None:
        return explicit

    def merged_map(base: Any, overlay: Any) -> MappingProxyType[str, Any]:
        data = dict(base)
        data.update(overlay)
        return MappingProxyType(data)

    return RuleResource(
        sheets=merged_map(registered.sheets, explicit.sheets),
        sections=merged_map(registered.sections, explicit.sections),
        columns=merged_map(registered.columns, explicit.columns),
        field_blocks=merged_map(registered.field_blocks, explicit.field_blocks),
        fields=merged_map(registered.fields, explicit.fields),
        tokens=_merge_domain_tokens(registered.tokens, explicit.tokens),
    )


def _load_workbook_auto(file_url: str) -> Workbook:
    """Open one related workbook. A read failure stays a read failure."""
    from documents.conversion import load_workbook_auto as load_workbook_pipeline

    from audit.engine.runtime import get_runtime

    try:
        runtime = get_runtime()
        if runtime is not None:
            workbook = runtime.load_workbook_auto(FileRef(location=file_url))
        else:
            workbook = load_workbook_pipeline(file_url, enable_cache=True, cleanup=True)
        if not isinstance(workbook, Workbook):
            raise TypeError(f"Expected openpyxl Workbook, got {type(workbook).__name__}")
        return workbook
    except Exception as exc:
        logger.warning("关联资料加载失败: %s", type(exc).__name__)
        raise OSError("本轮冻结关联资料无法读取，不能完成本项核对。") from None


class LiAutoPswPdfWorkbookAgent:
    """Converted L17 workbook checks for the v4 PDF carrier."""

    DOC_TYPE = PpapDocumentType.PSW
    SOURCE_CLAUSE_IDS = True
    CLAUSE_BOUND_CHECKS = False
    EXPECTED_RULE_CATEGORIES = (
        "生产件批准交付物文件清单附件检查",
        "重量字段格式检查",
        "印章及授权声明检查",
        "供应商名称格式检查",
        "签字与盖章完整性检查",
        "关键字段非空检查",
        "提交原因选项检查",
        "认可情况及结果勾选检查",
        "条件批准日期时效检查",
        "条件批准补充评论检查",
    )

    def __init__(self, audit_file: AuditInput, rule: RuleInput, group_context: GroupContextInput | None = None, *, resource: RuleResource | None = None) -> None:
        self.audit_file = audit_file
        self.rule = rule
        self.group_context = group_context
        self.logger = logging.getLogger(type(self).__name__)
        self.agent_rules: list[str] = []
        self.rule_checkers: list[Callable[..., tuple[str, str]]] = []
        for method_name, description in discover_declared_rules(type(self)):
            method = getattr(self, method_name)
            self.agent_rules.append(description)

            def make_checker(bound: Callable[[RuleContext], tuple[str, str]]) -> Callable[..., tuple[str, str]]:
                def checker(workbook: Workbook, *, ctx: RuleContext | None = None) -> tuple[str, str]:
                    rule_ctx = ctx if ctx is not None else self.rule_ctx(workbook)
                    return bound(rule_ctx)

                return checker

            self.rule_checkers.append(make_checker(method))
        self._validate_hardcoded_rule_contract()
        self._workbook_cache: Workbook | None = None
        self._native_facts_workbook: Workbook | None = None
        self.native_tables: dict[str, Any] = {}
        self._related_conversion_artifacts: dict[str, Any] = {}
        self._conversion_artifact: Any = None
        self._shared_rule_context: RuleContext | None = None
        self._psw_field_evidence: dict[int, Any] = {}
        self._doc_type = PpapDocumentType.PSW
        registered = get_doc_resource_by_doc_type(self._doc_type)
        self._rule_resource = merge_rule_resources(registered, resource)
        self._source_path = self._resolve_local_file_path(str(getattr(self.audit_file, "file_url", "") or ""))
        self.checks = None

    def _validate_hardcoded_rule_contract(self) -> None:
        expected = tuple(str(item).strip() for item in type(self).EXPECTED_RULE_CATEGORIES)
        actual = tuple(str(item).strip() for item in self.agent_rules)
        if actual != expected:
            raise RuntimeError(f"{type(self).__name__} hardcoded rule contract mismatch: checker_categories={actual!r}, expected={expected!r}")

    @property
    def document_type_name(self) -> str:
        return "LiAutoPsw"

    def log_audit_start(self, ith: int) -> None:
        self.logger.debug("开始审核 %s - 规则: %s", self.document_type_name, ith + 1)

    def log_audit_result(self, result: AuditResult, ith: int) -> None:
        self.logger.debug("审核完成 %s - 规则: %s, 状态: %s", self.document_type_name, ith + 1, result.status)

    def create_error_result(self, error_msg: str, ith: int = -1) -> AuditResult:
        return AuditResult(
            classification=self.document_type_name,
            item=f"{self.document_type_name} - 规则{ith + 1}",
            priority="P0",
            status="ERR",
            suggestions=f"审核过程中出现错误: {error_msg}",
        )

    def close(self) -> None:
        workbook = self._workbook_cache
        self._workbook_cache = None
        self._native_facts_workbook = None
        if workbook is None:
            return
        close_method = getattr(workbook, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:
                logger.debug("关闭工作簿缓存失败", exc_info=True)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def _resolve_local_file_path(self, file_url: str) -> str | None:
        parsed = urlparse(str(file_url or ""))
        if parsed.scheme in ("", "file"):
            raw_path = parsed.path if parsed.scheme == "file" else str(file_url or "")
            local_path = Path(unquote(raw_path))
            if local_path.exists():
                return str(local_path)
        return None

    def _file_version_label(self) -> str:
        metadata = getattr(self.audit_file, "file_metadata", {}) or {}
        return str(metadata.get("file_version_id") or metadata.get("sha256") or self.audit_file.audit_id)

    def _load_grid(self) -> Workbook:
        from documents.excel import load_excel_workbook

        from audit.engine.runtime import get_runtime

        file_url = str(getattr(self.audit_file, "file_url"))
        if file_url.lower().split("?", 1)[0].endswith(".pdf"):
            raise RuntimeError("v4 L17 PDF 表格检查只读取已转换的工作簿")
        runtime = get_runtime()
        if runtime is not None:
            workbook = runtime.load_workbook_auto(FileRef(location=file_url))
        else:
            workbook = load_excel_workbook(file_url)
        if not isinstance(workbook, Workbook):
            raise TypeError(f"Expected openpyxl Workbook, got {type(workbook).__name__}")
        return workbook

    def _load_workbook(self) -> Workbook:
        if self._workbook_cache is None:
            self._workbook_cache = self._load_grid()
        workbook = self._workbook_cache
        if self._native_facts_workbook is workbook:
            return workbook
        self.native_tables = {}
        source = getattr(workbook, "_ppap_source_path", None) or self._source_path
        if source and Path(source).suffix.lower() in {".xlsx", ".xlsm"}:
            native = snapshot(source)
            self.native_tables = {table["id"]: table for table in native["tables"]}
            for table in native["tables"]:
                for address, cell in table["cells"].items():
                    if cell["state"] in {"unknown_formula_cache", "unknown_excel_error"}:
                        workbook[table["id"]][address] = "#N/A"
                        workbook[table["id"]][address].data_type = "e"
        self._native_facts_workbook = workbook
        return workbook

    def rule_ctx(self, workbook: Any) -> RuleContext:
        resource = self._rule_resource
        if resource is None:
            raise RuntimeError("文档类型PSW缺少代码资源定义")
        version = self._file_version_label()
        cached = self._shared_rule_context
        if cached is not None and cached.workbook is workbook and cached.resource is resource:
            return cached
        ctx = RuleContext(ops=None, workbook=workbook, resource=resource, source_path=self._source_path, file_version_id=version)
        ctx.bind_workbook(workbook, version)
        self._shared_rule_context = ctx
        return ctx

    def _resolve_ng_collection_topn(self) -> int:
        env_value = str(os.getenv(NG_COLLECTION_TOPN_ENV, "") or "").strip()
        if env_value:
            try:
                parsed = int(env_value)
            except ValueError:
                return DEFAULT_NG_COLLECTION_TOPN
            if parsed > 0:
                return parsed
        return DEFAULT_NG_COLLECTION_TOPN

    def _finalize_rule_result(self, *, status: str, message: str, ctx: RuleContext) -> tuple[str, str]:
        if ctx.has_unresolved():
            return "UNABLE", "存在未算出的公式或不可读取单元格，本条结果尚未核实。"
        if str(status).upper() == "PASS" and ctx.finished_scans_yielded_nothing():
            return "UNABLE", "本次数据范围为空，不能按空范围通过。"
        normalized_status = (status or "").upper()
        normalized_message = str(message or "")
        if normalized_status == "ERR":
            return "ERR", normalized_message
        if not ctx.has_collected_ng():
            if normalized_status == "PASS" and ctx.has_unreadable_cells():
                return "UNABLE", "单元格证据尚不能确认，不能据此作业务判断。"
            return normalized_status, normalized_message
        if normalized_status == "PASS":
            return "NG", ctx.build_ng_summary()
        if normalized_status == "NG":
            summary = ctx.build_ng_summary()
            text = normalized_message.strip()
            if text and text not in summary and summary not in text:
                summary = ctx.build_ng_summary(existing=text)
            return "NG", summary or text
        if normalized_status == "UNABLE":
            text = normalized_message.strip()
            return "NG", ctx.build_ng_summary(existing=text or None)
        return normalized_status, normalized_message

    def _evidence_from_reads(self, ctx: RuleContext) -> Any:
        return native_cell_evidence(ctx, str(self._source_path or self.audit_file.file_url))

    def _check_sources(self, evidence: Any) -> tuple[Any, ...]:
        if evidence is None:
            return ()
        kept = []
        for source in evidence.sources:
            locator = source.locator
            if isinstance(locator, str) and locator.startswith("{"):
                kept.append(source)
        return tuple(kept)

    def _conversion_blocks(self, artifact: object) -> tuple[bool, str]:
        from documents.contracts import ConversionArtifact

        if not isinstance(artifact, ConversionArtifact):
            return False, ""
        if conversion_blocks_pass(artifact):
            note = "预算用尽，不能判为通过。" if budget_exhausted(artifact) else "原件定位或转换未完成，不能判为通过"
            return True, note
        evidence = evidence_from_conversion(artifact, file_version_id=self._file_version_label())
        if any(item.severity == "error" for item in evidence.diagnostics):
            return True, "原件定位或转换未完成，不能判为通过"
        return False, ""

    def _with_source_evidence(self, result: AuditResult) -> AuditResult:
        artifacts = [item for item in [self._conversion_artifact, *self._related_conversion_artifacts.values()] if item is not None]
        if not artifacts:
            return result
        evidence = result.evidence
        status = result.status
        suggestions = result.suggestions
        primary = self._conversion_artifact
        if primary is not None:
            evidence = evidence_from_conversion(primary, file_version_id=self._file_version_label())
            reads = self._check_sources(result.evidence)
            diagnostics = evidence.diagnostics
            if result.evidence is not None and any(item.code == "read_locations_truncated" for item in result.evidence.diagnostics):
                diagnostics = diagnostics + tuple(item for item in result.evidence.diagnostics if item.code == "read_locations_truncated")
            if reads or diagnostics != evidence.diagnostics:
                evidence = evidence.model_copy(update={"sources": evidence.sources + reads, "diagnostics": diagnostics})
        for version, artifact in self._related_conversion_artifacts.items():
            related = evidence_from_conversion(artifact, file_version_id=version)
            if evidence is None:
                evidence = related
            else:
                evidence = evidence.model_copy(
                    update={
                        "sources": evidence.sources + related.sources,
                        "diagnostics": evidence.diagnostics + related.diagnostics,
                        "conversion_strategies": tuple(dict.fromkeys(evidence.conversion_strategies + related.conversion_strategies)),
                    }
                )
        for artifact in artifacts:
            blocked, note = self._conversion_blocks(artifact)
            if status == "PASS" and blocked:
                status = "UNABLE"
                suggestions = f"{suggestions}；{note}" if suggestions else note
        return result.model_copy(update={"status": status, "suggestions": suggestions, "evidence": evidence})

    def _finalize_audit_result(self, result: AuditResult, *, rule_no: int, phase: RuleEvidencePhase, ctx: RuleContext | None = None) -> AuditResult:
        if ctx is not None and result.evidence is None:
            evidence = self._evidence_from_reads(ctx)
            if evidence is not None:
                result = result.model_copy(update={"evidence": evidence})
        result = self._with_source_evidence(result)
        result = finalize_excel_psw_result(result, audit=self.audit_file, rule_no=rule_no, phase=phase)
        field_evidence = self._psw_field_evidence.get(rule_no)
        return result.model_copy(update={"evidence": field_evidence}) if field_evidence is not None else result

    def _failure_result(self, ctx: RuleContext | None, rule_no: int, message: str) -> AuditResult:
        del ctx
        return self.create_error_result(message, rule_no - 1)

    def _get_rule_priority(self, rule_no: int) -> str:
        del rule_no
        return "P1"

    def _result_item(self, rule_no: int) -> str:
        if 0 < rule_no <= len(self.agent_rules):
            return self.agent_rules[rule_no - 1]
        return f"[{self.document_type_name}]规则检查-{rule_no}"

    def _execute_rule_check(self, rule_no: int) -> AuditResult:
        if rule_no < 1 or rule_no > len(self.rule_checkers):
            return self.create_error_result(f"无效的规则编号: {rule_no}，必须是 1 ~ {len(self.rule_checkers)} 之间的整数", rule_no - 1)
        self._related_conversion_artifacts.clear()
        ctx: RuleContext | None = None
        try:
            workbook = self._load_workbook()
            ctx = self.rule_ctx(workbook)
            ctx.enable_ng_collection(topn=self._resolve_ng_collection_topn())
            outcome = self.rule_checkers[rule_no - 1](workbook, ctx=ctx)
            if not isinstance(outcome, tuple) or len(outcome) != 2 or any(not isinstance(part, str) for part in outcome):
                return self._finalize_audit_result(
                    self._failure_result(ctx, rule_no, "检查没有返回状态和说明。方法必须 return 状态, 说明。"),
                    rule_no=rule_no,
                    phase="error",
                    ctx=ctx,
                )
            status, message = self._finalize_rule_result(status=outcome[0], message=outcome[1], ctx=ctx)
            return self._finalize_audit_result(
                AuditResult(
                    classification=self.document_type_name,
                    item=self._result_item(rule_no),
                    priority=self._get_rule_priority(rule_no),
                    status=status.upper(),
                    suggestions=message,
                    checks_performed=ctx.execution_count,
                ),
                rule_no=rule_no,
                phase="decision",
                ctx=ctx,
            )
        except GuardStop as stop:
            if ctx is None:
                status, suggestions, performed = stop.status, stop.message, None
            else:
                status, suggestions = self._finalize_rule_result(status=stop.status, message=stop.message, ctx=ctx)
                performed = ctx.execution_count
            return self._finalize_audit_result(
                AuditResult(
                    classification=self.document_type_name,
                    item=self._result_item(rule_no),
                    priority=self._get_rule_priority(rule_no),
                    status=status,
                    suggestions=suggestions,
                    checks_performed=performed,
                ),
                rule_no=rule_no,
                phase="guard",
                ctx=ctx,
            )
        except Exception as exc:
            logger.exception("检查规则 %s 时出错: %s", rule_no, exc)
            return self._finalize_audit_result(self._failure_result(ctx, rule_no, f"检查规则{rule_no}时出错: {exc}"), rule_no=rule_no, phase="error", ctx=ctx)

    def check(self, ith: int) -> AuditResult:
        self.log_audit_start(ith)
        try:
            result = self._execute_rule_check(ith + 1)
            self.log_audit_result(result, ith)
            return result
        except Exception as exc:
            logger.exception("%s审核失败: %s", self.document_type_name, exc)
            return self.create_error_result(str(exc), ith)

    @staticmethod
    def _delivery_sheets(workbook):
        return [sheet for sheet in workbook.worksheets if any(name in sheet.title for name in ("生产件批准交付物文件清单", "生产件批准提交清单", "PSW交付物清单"))]

    @staticmethod
    def _identity_field(ctx, labels, version, sheets):
        keys = {_identity(label) for label in labels}
        declared = False
        values = set()
        for sheet in sheets:
            if any(token in sheet.title.casefold() for token in ("履历", *_NON_DATA_SHEETS)):
                continue
            if sheet.max_row * sheet.max_column > 250000:
                raise GuardStop(Decision.UNABLE, "身份声明超出本次有界读取范围，尚不能确认没有另行声明。")
            for row in sheet.iter_rows():
                for cell in row:
                    if _identity(cell.value).rstrip(":：") not in keys:
                        continue
                    declared = True
                    ctx.note_cell(sheet.title, cell.row, cell.column, file_version_id=version)
                    right = cell.column + 1
                    for merged in sheet.merged_cells.ranges:
                        if cell.coordinate in merged:
                            right = merged.max_col + 1
                            break
                    value = sheet.cell(cell.row, right)
                    ctx.note_cell(sheet.title, value.row, value.column, file_version_id=version)
                    identity = _identity(value.value)
                    if value.data_type in {"e", "f"}:
                        raise GuardStop(Decision.UNABLE, "已声明的身份字段无法可靠读取，不能继承保证书身份。")
                    values.add(identity)
        if len(values) > 1:
            raise GuardStop(Decision.UNABLE, "同一原件存在冲突的身份声明，不能配对。")
        return declared, next(iter(values), "")

    @staticmethod
    def _could_contain_delivery_list(kind):
        if not kind or _identity(kind) in {"unknown", "未知", "未知类型"}:
            return True
        if any(name in str(kind) for name in ("生产件批准交付物文件清单", "生产件批准提交清单", "PSW交付物清单")):
            return True
        typed = PpapDocumentType.from_string(str(kind))
        if typed != PpapDocumentType.UNKNOWN:
            return typed == PpapDocumentType.PSW
        if str(kind) == "T17":
            return True
        from audit.presets import customer_presets

        if any(preset.source_type == kind for preset in customer_presets()):
            return False
        # An unclassified source may contain an embedded delivery table.
        return True

    @rule("生产件批准交付物文件清单附件检查")
    def rule_1(self, ctx):
        candidates = []
        opened = []
        local = self._delivery_sheets(ctx.workbook)
        if local:
            candidates.append((ctx.workbook, self.audit_file.audit_id, local))
        try:
            if not local and self.group_context:
                if any(self._could_contain_delivery_list(kind) for kind in self.group_context.unavailable_files.values()):
                    raise OSError("本轮关联原件读取失败，不能核实唯一交付物清单。")
                references = dict(self.group_context.by_audit_id)
                known_types = {}
                for kind, entries in self.group_context.by_doc_type.items():
                    for ref in entries:
                        references[ref.audit_id] = ref
                        known_types.setdefault(ref.audit_id, []).append(kind)
                for ref in references.values():
                    if ref.audit_id == self.audit_file.audit_id:
                        continue
                    if ref.audit_id in known_types and not any(self._could_contain_delivery_list(kind) for kind in known_types[ref.audit_id]):
                        continue
                    workbook = _load_workbook_auto(ref.file_url)
                    opened.append(workbook)
                    sheets = self._delivery_sheets(workbook)
                    if sheets:
                        candidates.append((workbook, ref.audit_id, sheets))
            if len(candidates) != 1 or len(candidates[0][2]) != 1:
                return "UNABLE", "缺少唯一生产件批准交付物文件清单，需确认本轮原件。"
            workbook, version, sheets = candidates[0]
            ctx.bind_workbook(workbook, version)
            embedded = workbook is ctx.workbook
            main_sheets = [sheet for sheet in ctx.workbook.worksheets if sheet not in local] if embedded else ctx.workbook.worksheets
            for original, original_version, scope in [(ctx.workbook, self.audit_file.audit_id, main_sheets), (workbook, version, sheets)]:
                _, customer = self._identity_field(ctx, ("客户", "客户名称", "Customer", "Customer Name"), original_version, scope)
                if customer and _identity(customer) not in {"理想", "理想汽车", "liauto", "li"}:
                    return "UNABLE", "原件客户与本轮理想模式冲突，不能配对。"
            fields = [
                (("理想汽车零件号", "零件号", "零件编号"), True),
                (("工程更改版本", "版本", "版本号"), False),
                (("项目名称", "车型项目", "项目编号", "项目代号", "项目标识", "UAES项目编号"), False),
            ]
            # A standalone delivery-list original has no warrant to pair.
            # Its attachment-completeness clause remains a unary check.
            for labels, required in fields if main_sheets else ():
                _, own = self._identity_field(ctx, labels, self.audit_file.audit_id, main_sheets)
                declared, other = self._identity_field(ctx, labels, version, sheets)
                # An embedded checklist may inherit the warrant's identity only
                # when it has no separate declaration of that identity field.
                if embedded and not declared:
                    other = own
                if required or own or other or (embedded and declared):
                    ctx.record_execution()
                    if not pair_identity(own) or pair_identity(own) != pair_identity(other):
                        return "UNABLE", "保证书与交付物清单零件号、项目或版本缺失或不一致，不能配对。"
            rows = _rows(ctx, workbook, {"item": ["项目", "交付物", "提交项目"], "attachment": ["附件"]}, ("item", "attachment"), version, sheets=sheets)
            checked = 0
            for row in rows:
                if not row["item"] or _identity(row["item"]) == _identity("零件认证相关"):
                    continue
                checked += 1
                ctx.record_execution()
                if not row["attachment"]:
                    return "NG", "交付物清单业务行的附件未填写。"
            return ("PASS", "已核实交付物附件。") if checked else ("UNABLE", "清单没有可核实的业务行。")
        finally:
            for workbook in opened:
                workbook.close()

    @rule("重量字段格式检查")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则2：识别“零件提交保证书”中是否包含“重量”字段，如果不包含则不通过，否则继续识别；
        当“重量”对应的内容为数值，且数值保留小数点后四位则通过，否则不通过。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        field_block = table.field_block("PSW主表字段区")
        field_block.expect("重量字段", context="PSW主表字段区")

        weight_field = field_block.field("重量字段")
        weight_value = str(weight_field.value() or "").replace("\n", "").replace("\r", "").strip()

        numeric_match = re.search(r"[-+]?\d+(?:\.\d+)?", weight_value.replace(",", ""))
        if not numeric_match:
            logger.error("规则2检查未通过，“重量”对应的内容不是有效数值")
            shown_value = weight_value or "空"
            return "NG", f"重量字段非数值，识别值={shown_value}"

        number_text = numeric_match.group(0)
        decimal_part = number_text.split(".", 1)[1] if "." in number_text else ""
        if len(decimal_part) == 4:
            logger.info("")
            return "PASS", ""

        logger.error("规则2检查未通过，“重量”数值未保留小数点后四位")
        return "NG", f"重量小数位数应为4位，识别值={number_text}，小数位={len(decimal_part)}"

    @rule("印章及授权声明检查")
    def rule_3(self, ctx):
        return "UNABLE", "尚未核实章面文字及部门章授权中的公司章；图像形状或红色区域不能证明本条通过。"

    @rule("供应商名称格式检查")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则4：识别“零件提交保证书”中“供应商名称/供应商代码”的内容是否为“总公司名称/代码”这种格式。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        main_sheet = table.sheet("PSW主表")
        supplier_value = self._extract_supplier_name_code(main_sheet)
        if not supplier_value:
            logger.error("规则4检查未通过，“供应商名称/供应商代码”对应的内容为空")
            return "NG", "供应商名称/供应商代码字段为空。"

        if "/" in supplier_value:
            parts = [part.strip() for part in supplier_value.split("/") if part.strip()]
            if len(parts) == 2:
                logger.info("")
                return "PASS", ""
        logger.error("规则4检查未通过，“供应商名称/供应商代码”内容格式不正确，应为“总公司名称/代码”格式")
        return "NG", f"供应商名称/供应商代码格式错误，识别值={supplier_value}"

    @rule("签字与盖章完整性检查")
    def rule_5(self, ctx):
        return "UNABLE", "尚未分别核实实际授权签名与盖章；同一印章不能同时充当签名证据。"

    @rule("关键字段非空检查")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则6：当“零件名称”、“理想汽车零件号”、“供方料号”、“图纸号”、“模具穴号”、“工程更改版本”、“版本日期”、“重量(kg)”、“供应商名称/供应商代码”、“街道地址”、“城市”、“省”、“国家”、“顾客名称”、“设计开发”、“采购工程”、“PTC”的内容均不为空则通过，否则不通过。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        main_sheet = table.sheet("PSW主表")
        field_block = table.field_block("PSW主表字段区")
        field_missing_tokens = {field_name: {str(token).strip().lower() for token in ctx.resource.fields[field_name].missing_tokens if str(token).strip()} for field_name in ctx.resource.fields}
        failed_fields = []
        field_specs = [
            ("重量(kg)", "重量字段", (("重量(kg)",), ("weight", "kg"), ("重量",))),
            ("零件名称", "零件名称字段", (("零件名称",), ("product",))),
            ("理想汽车零件号", "理想汽车零件号字段", (("理想汽车零件号",), ("lipartnumber",))),
            ("供方料号", "供方料号字段", (("供方料号",), ("vendorpartnumber",), ("vendorpar",))),
            ("图纸号", "图纸号字段", (("图纸号",), ("drawingnumber",), ("shownondrawingnumber",))),
            ("模具穴号", "模具穴号字段", (("模具穴号",), ("toolcavity",))),
            ("工程更改版本", "工程更改版本字段", (("工程更改版本",), ("工程", "更改"), ("改版",), ("changerevision",))),
            ("版本日期", "版本日期字段", (("版本日期",), ("ecdated",))),
            ("街道地址", "街道地址字段", (("街道地址",), ("streetaddress",))),
            ("城市", "城市字段", (("城市",), ("city",))),
            ("省", "省字段", (("省",), ("province",))),
            ("国家", "国家字段", (("国家",), ("country",))),
            ("顾客名称", "顾客名称字段", (("顾客", "名称"), ("customername",), ("customer", "name"))),
            ("设计开发", "设计开发字段", (("设计开发",), ("dre",))),
            ("采购工程", "采购工程字段", (("采购工程",), ("pur",))),
            ("PTC", "PTC字段", (("ptc",), ("tpm",))),
        ]
        nearby_fallback_spans = {
            "顾客名称字段": (2, 2),
            "设计开发字段": (1, 2),
            "采购工程字段": (1, 2),
            "PTC字段": (1, 2),
        }
        label_noise_tokens = self._build_label_tokens(_RULE6_LABEL_NOISE_RAW)

        for field_name, resource_field_name, anchors in field_specs:
            field = field_block.field(resource_field_name)
            missing_tokens = field_missing_tokens.get(resource_field_name, set())
            if field.has_business_value() and self._is_effective_business_value(
                field.value(),
                missing_tokens=missing_tokens,
                label_noise_tokens=label_noise_tokens,
            ):
                continue
            label_cell = main_sheet.find_label(
                anchors,
                row_max=120,
                col_max=45,
            )
            if label_cell is not None:
                fallback_cell = label_cell.find_right(
                    max_offset=12,
                    use_merged_anchor=True,
                )
                fallback_value = ""
                if fallback_cell is not None:
                    fallback_value = str(fallback_cell.value() or "")
                if self._is_effective_business_value(
                    fallback_value,
                    missing_tokens=missing_tokens,
                    label_noise_tokens=label_noise_tokens,
                ):
                    continue

                nearby_span = nearby_fallback_spans.get(resource_field_name)
                if nearby_span is not None:
                    row_span, col_span = nearby_span
                    nearby_hit = False
                    for nearby_cell in label_cell.scan_nearby(
                        row_span=row_span,
                        col_span=col_span,
                    ):
                        if self._is_effective_business_value(
                            nearby_cell.value(),
                            missing_tokens=missing_tokens,
                            label_noise_tokens=label_noise_tokens,
                        ):
                            nearby_hit = True
                            break
                    if nearby_hit:
                        continue
            failed_fields.append(field_name)
            ctx.record_ng(f"必填字段缺失或为空：{field_name}")

        supplier_value = self._extract_supplier_name_code(main_sheet)
        if not supplier_value:
            failed_fields.append("供应商名称/供应商代码")

        if failed_fields:
            logger.error("规则6检查未通过，字段缺失或为空: %s", ", ".join(failed_fields))
            return "NG", f"以下字段缺失或为空：{', '.join(failed_fields)}"

        logger.info("")
        return "PASS", ""

    @rule("提交原因选项检查")
    def rule_7(self, ctx: RuleContext) -> tuple[str, str]:
        from audit.engine.checkers.excel_field_evidence import check_psw_submission_reason

        return check_psw_submission_reason(self, ctx)

    @rule("认可情况及结果勾选检查")
    def rule_8(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则8：识别PSW是否包含“认可情况”字段，如果不包含则不通过，否则继续识别；“认可情况”字段是否有且仅有一个选项被勾选，如果不是则不通过，否则继续识别；当“认可情况”的“批准”选项被勾选，且“提交结果”区域的选项“尺寸测量”、“外观标准”、“材料和功能试验”、“统计过程数据”均被勾选则通过，且“这些结果均满足所有图纸和规范要求”也有选项被勾选则通过，否则不通过

        Returns:
            bool: 规则是否通过
        """
        main_sheet = Table(ctx).sheet("PSW主表")
        checked_controls = self._checked_controls(main_sheet)
        disposition, disposition_checks, disposition_row = self._resolve_warrant_disposition(main_sheet, checked_controls)
        if disposition == "missing_field":
            return "NG", "未找到“认可情况”字段。"
        if disposition == "invalid_selection":
            return "NG", "“认可情况”需且仅需勾选一个选项。"

        if disposition == "approved":
            all_checked = self._is_submission_result_all_checked(main_sheet, checked_controls)
            ctx.record_execution()
            if all_checked is True:
                return "PASS", ""
            if all_checked is None:
                return "UNABLE", "无法将提交结果的全部选项与各自的勾选状态绑定。"
            return "NG", "“批准”状态下提交结果勾选不完整。"

        if disposition in {"conditional", "rejected"}:
            return "PASS", ""

        logger.error("规则8无法识别认可情况选项: %s / %s", disposition, disposition_checks)
        if disposition_row is None:
            return "NG", "认可情况定位异常，无法判断勾选状态。"
        return "UNABLE", "无法确定控件对应的“认可情况”选项。"

    @rule("条件批准日期时效检查")
    def rule_9(self, ctx):
        main_sheet = Table(ctx).sheet("PSW主表")
        disposition, _, row = self._resolve_warrant_disposition(main_sheet, self._checked_controls(main_sheet))
        ctx.record_execution()
        if disposition in {"missing_field", "invalid_selection"}:
            return "NG", "认可情况缺失或勾选不唯一。"
        if disposition == "unknown":
            return "UNABLE", "无法确定控件对应的认可情况选项。"
        if disposition != "conditional":
            return "PASS", "本条条件未触发。"
        if row is None:
            return "UNABLE", "不能定位条件批准日期。"
        approved = self._extract_conditional_approval_date(main_sheet, row)
        if approved is None:
            return "UNABLE", "条件批准日期不可可靠读取。"
        today = datetime.now().date()
        offset = today.year * 12 + today.month - 1 - 3
        year, month = divmod(offset, 12)
        month += 1
        earliest = today.replace(year=year, month=month, day=min(today.day, calendar.monthrange(year, month)[1]))
        ctx.record_execution()
        return ("PASS", "条件批准日期在三个日历月内。") if earliest <= approved <= today else ("NG", "条件批准日期不在当前日期之前的三个日历月内。")

    @rule("条件批准补充评论检查")
    def rule_10(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则10：当认可情况勾选“条件批准”，且补充评论内容不为空则通过，否则不通过

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        main_sheet = table.sheet("PSW主表")
        checked_controls = self._checked_controls(main_sheet)
        disposition, disposition_checks, _ = self._resolve_warrant_disposition(main_sheet, checked_controls)
        if disposition == "missing_field":
            return "NG", "未找到“认可情况”字段。"
        if disposition == "invalid_selection":
            return "NG", "“认可情况”需且仅需勾选一个选项。"
        if disposition == "unknown":
            return "UNABLE", "无法确定控件对应的“认可情况”选项。"
        if disposition != "conditional":
            return "PASS", ""

        comments_value = self._extract_comments_value(table, main_sheet)
        ctx.record_execution()
        if str(comments_value or "").strip().lower() not in _EMPTY_MARKERS:
            return "PASS", ""
        return "NG", "条件批准需填写补充评论。"

    def _extract_supplier_name_code(self, sheet: Sheet) -> str:
        section = sheet.section("PSW供应商信息区")
        columns = section.columns("供应商名称/供应商代码")
        for row in section.rows_from(columns):
            value_compact = row.value("供应商名称/供应商代码", compact=True).lower()
            if value_compact in _EMPTY_MARKERS:
                continue
            if "organizationname" in value_compact and "vendorcode" in value_compact:
                continue
            value = str(row.value("供应商名称/供应商代码", compact=True))
            return value
        return ""

    def _classify_disposition_option(
        self,
        sheet: Sheet,
        *,
        checked_row: int,
        checked_col: int,
        disposition_row: int,
    ) -> str:
        from audit.engine.checkers.excel_field_evidence import classify_psw_disposition

        return classify_psw_disposition(sheet, checked_row, checked_col, disposition_row)

    def _checked_controls(self, sheet: Sheet) -> list[tuple[int, int]]:
        return [(row, col) for row, col, checked in sheet.checkbox_controls() if checked]

    def _resolve_warrant_disposition(
        self,
        sheet: Sheet,
        checked_controls: list[tuple[int, int]],
    ) -> tuple[str, list[tuple[int, int]], int | None]:
        disposition_cell = sheet.find_label(
            (("认可", "情况"), ("warrant", "disposition")),
            row_max=120,
            col_max=25,
        )
        sheet.ctx.record_execution()  # Evaluate the located disposition and unique selection.
        if disposition_cell is None:
            return "missing_field", [], None
        disposition_row = disposition_cell.row_index

        disposition_checks = [(row, col) for row, col in checked_controls if max(1, disposition_row - 2) <= row <= disposition_row + 3]
        if len(disposition_checks) != 1:
            return "invalid_selection", disposition_checks, disposition_row

        checked_row, checked_col = disposition_checks[0]
        option = self._classify_disposition_option(
            sheet,
            checked_row=checked_row,
            checked_col=checked_col,
            disposition_row=disposition_row,
        )
        return option, disposition_checks, disposition_row

    @staticmethod
    def _compact_token(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    @classmethod
    def _compact_label_token(cls, value: object) -> str:
        compact = cls._compact_token(value)
        return re.sub(r"[:：#()（）\\/_.\-]", "", compact)

    @classmethod
    def _build_label_tokens(cls, raw_values: tuple[str, ...]) -> set[str]:
        return {cls._compact_label_token(value) for value in raw_values if str(value).strip()}

    @classmethod
    def _is_effective_business_value(
        cls,
        value: object,
        *,
        missing_tokens: set[str],
        label_noise_tokens: set[str] | None = None,
        reject_date_like: bool = False,
    ) -> bool:
        compact = cls._compact_token(value)
        if compact in _EMPTY_MARKERS:
            return False

        text = str(value or "").strip().lower()
        if text in missing_tokens or compact in missing_tokens:
            return False

        if label_noise_tokens and cls._compact_label_token(value) in label_noise_tokens:
            return False

        if reject_date_like and re.fullmatch(r"[0-9年月日:/.-]+", compact):
            return False

        return True

    def _parse_date_from_text(self, value: object) -> date | None:
        normalized_date = normalize_excel_date(value)
        if normalized_date is not None:
            return normalized_date

        text = str(value or "").replace("\n", "").replace("\r", "").strip()
        if not text:
            return None

        patterns = [
            r"(20\d{2})\D{0,3}(1[0-2]|0?[1-9])\D{0,3}(3[01]|[12]\d|0?[1-9])",
            r"(20\d{2})[./-](1[0-2]|0?[1-9])[./-](3[01]|[12]\d|0?[1-9])",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            year, month, day = (int(group) for group in match.groups())
            try:
                return datetime(year, month, day).date()
            except ValueError:
                continue
        return None

    def _extract_conditional_approval_date(self, sheet: Sheet, disposition_row: int) -> date | None:
        candidate_values: list[str] = []
        row_min = max(1, disposition_row - 2)
        row_max = disposition_row + 8
        col_min = 15
        for candidate_cell in sheet.scan_non_empty(
            row_min=row_min,
            row_max=row_max,
            col_min=col_min,
            col_max=50,
        ):
            candidate_values.append(str(candidate_cell.value() or ""))

        date_label = sheet.find_label(
            (("日期",), ("date",)),
            row_max=row_max,
            col_max=50,
        )
        if date_label is not None:
            if date_label.row_index >= row_min and date_label.col_index >= col_min:
                right_value_cell = date_label.find_right(
                    max_offset=8,
                    use_merged_anchor=True,
                )
                if right_value_cell is not None:
                    candidate_values.append(str(right_value_cell.value() or ""))
                for candidate_cell in date_label.scan_nearby(
                    row_span=2,
                    col_span=8,
                ):
                    candidate_values.append(str(candidate_cell.value() or ""))

        for value in candidate_values:
            parsed_date = self._parse_date_from_text(value)
            if parsed_date is not None:
                return parsed_date
        return None

    def _extract_comments_value(self, table: Table, sheet: Sheet) -> str:
        comments_field = table.field_block("PSW主表字段区").field("补充评论字段")
        if comments_field.has_business_value():
            return str(comments_field.value(compact=True))

        label_cell = sheet.find_label(
            (("补充评论",), ("comments",)),
            row_max=120,
            col_max=25,
        )
        if label_cell is None:
            return ""

        right_value_cell = label_cell.find_right(
            max_offset=15,
            use_merged_anchor=True,
        )
        if right_value_cell is not None:
            right_value = str(right_value_cell.value() or "").replace("\n", "").replace("\r", "").strip()
            if right_value:
                return right_value

        for candidate_cell in label_cell.scan_nearby(row_span=2, col_span=16):
            value = str(candidate_cell.value() or "").replace("\n", "").replace("\r", "").strip()
            if value:
                return value
        return ""

    def _is_submission_result_all_checked(
        self,
        sheet: Sheet,
        checked_controls: list[tuple[int, int]],
    ) -> bool | None:
        from audit.engine.checkers.excel_field_evidence import psw_submission_results

        return psw_submission_results(sheet)
