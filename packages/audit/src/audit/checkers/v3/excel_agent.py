"""
Excel文件专用审核代理基类

在BaseAgent和具体Excel文档审核代理之间提供专门的Excel文件处理功能。
所有处理Excel文件的PPAP审核代理都应该继承此类。
"""

import json
import logging
import os
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, List, Literal, NamedTuple, Optional, Tuple, cast
from urllib.parse import unquote, urlparse

from documents.contracts import FileRef
from documents.excel import load_excel_workbook
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from audit.checkers.v2.base_agent import BaseAgent
from audit.contracts import (
    AuditEvidence,
    AuditInput,
    EvidenceDiagnostic,
    EvidenceSource,
    GroupContextInput,
    RuleInput,
    RuleItem,
)
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.checkers.pdf_evidence import PdfSourceError, budget_exhausted, conversion_blocks_pass, evidence_from_conversion
from audit.engine.checkers.rule_check import RuleCheck
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.engine.rule_engine import (
    GuardStop,
    RuleContext,
    get_doc_resource_by_doc_type,
)
from audit.engine.rule_engine.contracts import DomainTokensModel, RuleResource
from audit.kinds import PpapDocumentType

logger = logging.getLogger(__name__)
DEFAULT_NG_COLLECTION_TOPN = 5
NG_COLLECTION_TOPN_ENV = "PPAP_NG_TOPN"
GENERIC_RULE_CATEGORIES = {"local_excel_check"}
RuleEvidencePhase = Literal["decision", "guard", "error"]


class _DeclaredClause(NamedTuple):
    clause_id: str
    description: str
    index: int


def rule(description: str, clause_id: str | None = None) -> Callable[[Any], Any]:
    """
    规则装饰器，用于自动注册规则描述和检查器。

    clause_id 是条款身份。方法名和声明顺序不代替它。
    """

    def decorator(func: Any) -> Any:
        func.__rule_description__ = description
        if clause_id is not None:
            text = str(clause_id).strip()
            if not text:
                raise ValueError("clause_id must not be empty")
            func.__rule_clause_id__ = text
        return func

    return decorator


def merge_rule_resources(registered: RuleResource | None, explicit: RuleResource | None) -> RuleResource | None:
    """Put an explicit task resource and the registry resource in one resolver map."""
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


class ExcelAgent(BaseAgent):
    """
    Excel文件专用审核代理基类

    提供Excel文件的通用处理功能，包括：
    - Excel文件格式检查和加载
    - 通用的工作表查找和列查找方法
    - 统一的规则检查流程（check -> _execute_rule_check）
    - 规则检查器基础设施
    """

    DOC_TYPE: PpapDocumentType | None = None
    NG_COLLECTION_TOPN: int | None = None
    EXPECTED_RULE_CATEGORIES: Tuple[str, ...] = ()
    CLAUSE_BOUND_CHECKS = False
    # Builtin checkers keep declaration order when the rule ids are not their clause ids.
    POSITIONAL_CLAUSE_CHECKS = False

    @classmethod
    def declared_rules(cls) -> tuple[tuple[str, str], ...]:
        """Return deterministic method names and descriptions without loading a file."""
        return discover_declared_rules(cls)

    def __init__(
        self,
        audit_file: AuditInput,
        rule: RuleInput,
        group_context: GroupContextInput | None = None,
        *,
        resource: RuleResource | None = None,
    ) -> None:
        """
        初始化Excel代理

        Args:
            audit_file: 要审核的文件
            rule: 审核规则
        """
        super().__init__(audit_file, rule, group_context=group_context)
        # 子类需要定义这些属性
        self.agent_rules: List[str] = []
        self.rule_checkers: List[Callable[..., Tuple[str, str]]] = []

        # 自动注册带有 @rule 装饰器的方法
        declared_rules = self.declared_rules()
        if declared_rules:
            for method_name, description in declared_rules:
                method = getattr(self, method_name)
                self.agent_rules.append(description)

                def make_checker(m: Callable[[RuleContext], Tuple[str, str]]) -> Callable[..., Tuple[str, str]]:
                    def checker(workbook: Workbook, *, ctx: RuleContext | None = None) -> Tuple[str, str]:
                        rule_ctx = ctx if ctx is not None else self.rule_ctx(workbook)
                        return m(rule_ctx)

                    return checker

                self.rule_checkers.append(make_checker(cast(Callable[[RuleContext], Tuple[str, str]], method)))

        self._validate_hardcoded_rule_contract()

        # Cached workbook – loaded once, reused across all rule checks.
        self._workbook_cache: Optional[Workbook] = None
        self._conversion_artifact = None
        self._doc_type = self._resolve_resource_doc_type()
        registered = get_doc_resource_by_doc_type(self._doc_type) if self._doc_type is not None else None
        self._rule_resource = merge_rule_resources(registered, resource)
        self._source_path = self._resolve_local_file_path(str(getattr(self.audit_file, "file_url", "") or ""))
        self.checks: tuple[RuleCheck, ...] | None = None
        self._declared_checks: tuple[_DeclaredClause, ...] = ()
        self._declaration_checks: tuple[RuleCheck, ...] = ()
        self._checks_by_clause: dict[str, RuleCheck] = {}
        self._shared_rule_context: RuleContext | None = None
        if type(self).CLAUSE_BOUND_CHECKS:
            self._bind_clause_checks()

    def _resolve_resource_doc_type(self) -> PpapDocumentType | None:
        doc_type = getattr(type(self), "DOC_TYPE", None)
        if isinstance(doc_type, PpapDocumentType) and doc_type != PpapDocumentType.UNKNOWN:
            return doc_type
        inferred = PpapDocumentType.from_string(str(getattr(self.rule, "type", "") or ""))
        if inferred == PpapDocumentType.UNKNOWN:
            return None
        return inferred

    def _resolve_local_file_path(self, file_url: str) -> str | None:
        parsed = urlparse(str(file_url or ""))
        if parsed.scheme in ("", "file"):
            raw_path = parsed.path if parsed.scheme == "file" else str(file_url or "")
            local_path = Path(unquote(raw_path))
            if local_path.exists():
                return str(local_path)
        return None

    @staticmethod
    def _rule_category(raw_rule: Any) -> str | None:
        if isinstance(raw_rule, dict):
            category = raw_rule.get("category")
        else:
            category = getattr(raw_rule, "category", None)
        if category is None:
            return None
        text = str(category).strip()
        return text or None

    @staticmethod
    def _rule_id(raw_rule: Any) -> str | None:
        if isinstance(raw_rule, dict):
            rule_id = raw_rule.get("id")
        else:
            rule_id = getattr(raw_rule, "id", None)
        if rule_id is None:
            return None
        text = str(rule_id).strip()
        return text or None

    def _validate_hardcoded_rule_contract(self) -> None:
        expected = tuple(str(item).strip() for item in (getattr(type(self), "EXPECTED_RULE_CATEGORIES", ()) or ()))
        if not expected:
            return

        actual = tuple(str(item).strip() for item in self.agent_rules)
        if actual != expected:
            raise RuntimeError(f"{type(self).__name__} hardcoded rule contract mismatch: checker_categories={actual!r}, expected={expected!r}")

        raw_rules = getattr(self.rule, "rules", []) or []
        if not isinstance(raw_rules, list) or not raw_rules:
            return

        rule_categories = tuple(self._rule_category(raw_rule) for raw_rule in raw_rules)
        normalized_rule_categories = tuple(category for category in rule_categories if category is not None)
        if normalized_rule_categories and set(normalized_rule_categories).issubset(GENERIC_RULE_CATEGORIES):
            return

        if type(self).CLAUSE_BOUND_CHECKS:
            if len(raw_rules) == len(actual) and normalized_rule_categories and normalized_rule_categories != actual:
                logger.warning(
                    "%s rule JSON category text differs from hardcoded checker names; "
                    "continuing because rule JSON category is LLM-extracted display text: "
                    "checker_categories=%r, rule_json_categories=%r",
                    type(self).__name__,
                    actual,
                    normalized_rule_categories,
                )
            return

        if len(raw_rules) != len(actual):
            logger.error(
                "%s rule contract count mismatch: checker_count=%d, rule_json_count=%d, rule_json=%s",
                type(self).__name__,
                len(actual),
                len(raw_rules),
                json.dumps(raw_rules, ensure_ascii=False, default=str),
            )
            raise RuntimeError(f"{type(self).__name__} rule contract count mismatch: checker_count={len(actual)}, rule_json_count={len(raw_rules)}")

        rule_ids = tuple(self._rule_id(raw_rule) for raw_rule in raw_rules)
        if any(rule_id is not None for rule_id in rule_ids):
            expected_ids = tuple(str(index) for index in range(1, len(actual) + 1))
            if rule_ids != expected_ids:
                raise RuntimeError(f"{type(self).__name__} rule contract id/order mismatch: expected_rule_ids={expected_ids!r}, rule_json_ids={rule_ids!r}")
        else:
            logger.warning("%s rule JSON has no rule ids; validated count only", type(self).__name__)

        if normalized_rule_categories and normalized_rule_categories != actual:
            logger.warning(
                "%s rule JSON category text differs from hardcoded checker names; continuing because rule JSON category is LLM-extracted display text: checker_categories=%r, rule_json_categories=%r",
                type(self).__name__,
                actual,
                normalized_rule_categories,
            )

    def _close_cached_workbook(self) -> None:
        workbook = self._workbook_cache
        self._workbook_cache = None
        if workbook is None:
            return
        close_method = getattr(workbook, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception as exc:
                logger.debug("关闭工作簿缓存失败: %s", exc)

    def close(self) -> None:
        self._close_cached_workbook()

    def __del__(self) -> None:
        try:
            self._close_cached_workbook()
        except Exception:
            pass

    def _rule_payload_is_generic(self) -> bool:
        raw_rules = getattr(self.rule, "rules", []) or []
        if not isinstance(raw_rules, list) or not raw_rules:
            return True
        categories = tuple(self._rule_category(raw_rule) for raw_rule in raw_rules)
        normalized = tuple(category for category in categories if category is not None)
        if not normalized:
            return False
        return set(normalized).issubset(GENERIC_RULE_CATEGORIES)

    def _declared_clauses(self) -> tuple[_DeclaredClause, ...]:
        declared: list[_DeclaredClause] = []
        for index, (method_name, description) in enumerate(self.declared_rules()):
            method = getattr(self, method_name)
            func = getattr(method, "__func__", method)
            clause_id = getattr(func, "__rule_clause_id__", None)
            if clause_id is None:
                clause_id = ""
            declared.append(_DeclaredClause(str(clause_id), description, index))
        return tuple(declared)

    def _input_rule_items(self) -> list[RuleItem]:
        raw_rules = getattr(self.rule, "rules", []) or []
        if not isinstance(raw_rules, list):
            return []
        items: list[RuleItem] = []
        for raw in raw_rules:
            if isinstance(raw, RuleItem):
                items.append(raw)
                continue
            if not isinstance(raw, dict):
                try:
                    raw = RuleItem.model_validate(raw).model_dump()
                except Exception:
                    items.append(RuleItem(id="", category="", rule_to_check="", type_of_rule=""))
                    continue
            priority = raw.get("priority")
            priority_text = str(priority).strip() if priority is not None else ""
            items.append(
                RuleItem(
                    id=str(raw.get("id") or "").strip(),
                    category=str(raw.get("category") or "").strip(),
                    priority=priority_text or None,
                    rule_to_check=str(raw.get("rule_to_check") or "").strip(),
                    type_of_rule=str(raw.get("type_of_rule") or "").strip(),
                )
            )
        return items

    def _synthetic_item(self, clause: _DeclaredClause) -> RuleItem:
        return RuleItem(
            id=clause.clause_id,
            category=clause.description,
            rule_to_check=clause.description,
            type_of_rule="",
        )

    def _problem_check(self, item: RuleItem, message: str, *, blocking: bool = True) -> RuleCheck:
        def run() -> AuditResult:
            label = item.category or item.rule_to_check
            if not label:
                label = f"条款 {item.id}" if item.id else "未命名条款"
            return AuditResult(
                classification=self.document_type_name,
                item=label,
                priority=item.priority or "P0",
                status="ERR",
                suggestions=message,
                clause_id=item.id or None,
            )

        return RuleCheck(item, run, binding_error=message if blocking else None)

    def _executable_check(self, item: RuleItem, rule_index: int) -> RuleCheck:
        def run() -> AuditResult | None:
            return self._execute_rule_check(rule_index + 1)

        return RuleCheck(item, run)

    def _bind_clause_checks(self) -> None:
        declared = self._declared_clauses()
        self._declared_checks = declared
        items = self._input_rule_items()
        if self._rule_payload_is_generic() or type(self).POSITIONAL_CLAUSE_CHECKS:
            self._bind_declared_checks(declared, items)
            return
        self._bind_effective_rule_checks(declared, items)

    def _bind_declared_checks(self, declared: tuple[_DeclaredClause, ...], items: list[RuleItem]) -> None:
        items_by_id: dict[str, RuleItem] = {}
        for item in items:
            if item.id and item.id not in items_by_id:
                items_by_id[item.id] = item
        duplicates = {clause_id for clause_id, count in Counter(clause.clause_id for clause in declared).items() if count > 1}
        checks: list[RuleCheck] = []
        by_clause: dict[str, RuleCheck] = {}
        for clause in declared:
            if not clause.clause_id:
                checks.append(self._problem_check(self._synthetic_item(clause), "检查入口缺少条款 ID"))
                continue
            item = items_by_id.get(clause.clause_id) or self._synthetic_item(clause)
            if clause.clause_id in duplicates:
                problem = self._problem_check(item, f"条款 {clause.clause_id} 绑定了多个检查入口")
                checks.append(problem)
                by_clause.setdefault(clause.clause_id, problem)
                continue
            check = self._executable_check(item, clause.index)
            checks.append(check)
            by_clause[clause.clause_id] = check
        self.checks = tuple(checks)
        self._declaration_checks = tuple(checks)
        self._checks_by_clause = by_clause

    def _bind_effective_rule_checks(self, declared: tuple[_DeclaredClause, ...], items: list[RuleItem]) -> None:
        method_counts = Counter(clause.clause_id for clause in declared)
        input_counts = Counter(item.id for item in items if item.id)
        declared_by_id: dict[str, _DeclaredClause] = {}
        for clause in declared:
            declared_by_id.setdefault(clause.clause_id, clause)

        checks: list[RuleCheck] = []
        by_clause: dict[str, RuleCheck] = {}
        declaration: list[RuleCheck | None] = [None] * len(declared)

        def bind_declaration(clause_id: str, check: RuleCheck) -> None:
            for candidate in declared:
                if candidate.clause_id == clause_id and declaration[candidate.index] is None:
                    declaration[candidate.index] = check

        for item in items:
            if not item.id:
                checks.append(self._problem_check(item, "生效规则缺少条款 ID"))
                continue
            if input_counts[item.id] > 1:
                problem = self._problem_check(item, f"条款 {item.id} 在生效规则中重复", blocking=False)
                checks.append(problem)
                by_clause.setdefault(item.id, problem)
                bind_declaration(item.id, problem)
                continue
            if method_counts[item.id] == 0:
                checks.append(self._problem_check(item, f"条款 {item.id} 没有对应的检查入口"))
                continue
            if method_counts[item.id] > 1:
                problem = self._problem_check(item, f"条款 {item.id} 绑定了多个检查入口")
                checks.append(problem)
                by_clause.setdefault(item.id, problem)
                bind_declaration(item.id, problem)
                continue
            clause = declared_by_id[item.id]
            check = self._executable_check(item, clause.index)
            checks.append(check)
            by_clause[item.id] = check
            declaration[clause.index] = check

        input_ids = {item.id for item in items if item.id}
        for clause in declared:
            if declaration[clause.index] is not None:
                continue
            if not clause.clause_id:
                problem = self._problem_check(self._synthetic_item(clause), "检查入口缺少条款 ID")
            elif clause.clause_id in input_ids:
                existing = by_clause.get(clause.clause_id)
                if existing is not None:
                    declaration[clause.index] = existing
                continue
            elif method_counts[clause.clause_id] > 1:
                problem = self._problem_check(self._synthetic_item(clause), f"条款 {clause.clause_id} 绑定了多个检查入口")
            else:
                problem = self._problem_check(self._synthetic_item(clause), f"条款 {clause.clause_id} 不在本次生效规则中")
            checks.append(problem)
            if clause.clause_id:
                by_clause.setdefault(clause.clause_id, problem)
            declaration[clause.index] = problem

        aligned: list[RuleCheck] = []
        for clause, bound in zip(declared, declaration, strict=True):
            if bound is None:
                bound = self._problem_check(
                    self._synthetic_item(clause),
                    f"条款 {clause.clause_id} 没有对应的检查入口" if clause.clause_id else "检查入口缺少条款 ID",
                )
                checks.append(bound)
            aligned.append(bound)
        self.checks = tuple(checks)
        self._declaration_checks = tuple(aligned)
        self._checks_by_clause = by_clause

    def _check_bound_clause(self, ith: int) -> Optional[AuditResult]:
        self.log_audit_start(ith)
        try:
            checks = self._declaration_checks
            if ith < 0 or ith >= len(checks):
                return self.create_error_result(
                    f"无效的规则编号: {ith + 1}，必须是 1 ~ {len(checks)} 之间的整数",
                    ith,
                )
            result = checks[ith].check()
            self.log_audit_result(result, ith)
            return result
        except Exception as exc:
            logger.exception("%s审核失败: %s", self.document_type_name, exc)
            return super().check(ith)

    def check(self, ith: int) -> Optional[AuditResult]:
        """
        执行第ith条规则的审核，提供Excel文件的标准检查流程

        当代理拥有自身的 rule_checkers 时，直接按 ith 索引调用对应的
        检查器，不依赖规则文件中的条目（规则文件条目数/顺序可能与
        agent_rules 不一致）。

        已按条款绑定的代理把 ith 转给对应条款的 RuleCheck。

        Args:
            ith: 规则索引（从0开始）

        Returns:
            AuditResult: 审核结果
        """
        if type(self).CLAUSE_BOUND_CHECKS:
            return self._check_bound_clause(ith)
        self.log_audit_start(ith)

        try:
            # 当代理有自己的 rule_checkers 时，直接执行，无需从规则文件取数据
            if self.rule_checkers:
                result = self._execute_rule_check(ith + 1)
                if result:
                    self.log_audit_result(result, ith)
                    return result
                logger.debug(f"Excel检查器没有结果，回退到AI审核: {ith}")
                return super().check(ith)

            # 没有 rule_checkers 时，走原有的规则文件驱动路径
            rule_data = self.get_rule_by_index(ith)
            if not rule_data:
                return self.create_error_result("无法获取规则数据", ith)

            # 直接调用统一的检查方法
            result = self._execute_rule_check(ith + 1)  # 使用1开始的索引
            if result:
                self.log_audit_result(result, ith)
                return result

            # 如果没有结果，回退到父类的AI逻辑
            logger.debug(f"Excel检查器没有结果，回退到AI审核: {ith}")
            return super().check(ith)

        except Exception as exc:
            logger.exception("%s审核失败: %s", self.document_type_name, exc)
            # 回退到父类的AI逻辑
            return super().check(ith)

    def _load_workbook(self) -> Workbook:
        """
        加载Excel工作簿（带实例级缓存，同一文件只解析一次）。

        Returns:
            workbook对象

        Raises:
            Exception: 当无法加载工作簿时
        """
        if self._workbook_cache is not None:
            return self._workbook_cache
        file_url = str(getattr(self.audit_file, "file_url"))
        if file_url.lower().split("?", 1)[0].endswith(".pdf"):
            return self._load_pdf_workbook(file_url)
        from audit.engine.runtime import get_runtime

        runtime = get_runtime()
        if runtime is not None:
            wb = runtime.load_workbook_auto(FileRef(location=file_url))
        else:
            wb = load_excel_workbook(file_url)
        if not isinstance(wb, Workbook):
            raise TypeError(f"Expected openpyxl Workbook, got {type(wb).__name__}")
        self._workbook_cache = wb
        return wb

    def _file_version_label(self) -> str:
        metadata = getattr(self.audit_file, "file_metadata", {}) or {}
        return str(metadata.get("file_version_id") or metadata.get("sha256") or self.audit_file.audit_id)

    def _load_pdf_workbook(self, file_url: str) -> Workbook:
        from documents.conversion import convert_pdf_to_excel_auto
        from documents.pdf import PdfSpec

        from audit.engine.runtime import get_runtime

        spec = getattr(type(self), "PDF_SPEC", None)
        if spec is not None and not isinstance(spec, PdfSpec):
            raise TypeError("PDF_SPEC must be a PdfSpec")
        runtime = get_runtime()
        file_ref = FileRef(location=file_url)
        if runtime is not None:
            artifact = runtime.convert_pdf_to_excel(file_ref, spec=spec, doc_type=self._doc_type)
        else:
            artifact = convert_pdf_to_excel_auto(file_ref, spec=spec, doc_type=self._doc_type)
        self._conversion_artifact = artifact
        if not artifact.success or not artifact.xlsx_path:
            raise PdfSourceError(artifact)
        workbook = load_excel_workbook(artifact.xlsx_path)
        if not isinstance(workbook, Workbook):
            raise TypeError(f"Expected openpyxl Workbook, got {type(workbook).__name__}")
        self._workbook_cache = workbook
        return workbook

    def _check_sources(self, evidence: AuditEvidence | None) -> tuple[EvidenceSource, ...]:
        """Keep fluent cell reads. Conversion pages do not replace them."""
        if evidence is None:
            return ()
        kept: list[EvidenceSource] = []
        for source in evidence.sources:
            locator = source.locator
            if isinstance(locator, str) and locator.startswith("{"):
                kept.append(source)
        return tuple(kept)

    def _with_source_evidence(self, result: AuditResult) -> AuditResult:
        artifact = self._conversion_artifact
        if artifact is None:
            return result
        evidence = evidence_from_conversion(artifact, file_version_id=self._file_version_label())
        reads = self._check_sources(result.evidence)
        diagnostics = evidence.diagnostics
        if result.evidence is not None and any(item.code == "read_locations_truncated" for item in result.evidence.diagnostics):
            diagnostics = diagnostics + tuple(item for item in result.evidence.diagnostics if item.code == "read_locations_truncated")
        if reads or diagnostics != evidence.diagnostics:
            evidence = evidence.model_copy(update={"sources": evidence.sources + reads, "diagnostics": diagnostics})
        status = result.status
        suggestions = result.suggestions
        location_gap = conversion_blocks_pass(artifact) or any(item.severity == "error" for item in diagnostics)
        if status == "PASS" and location_gap:
            status = "UNABLE"
            note = "预算用尽，不能判为通过。" if budget_exhausted(artifact) else "原件定位或转换未完成，不能判为通过"
            suggestions = f"{suggestions}；{note}" if suggestions else note
        return result.model_copy(update={"status": status, "suggestions": suggestions, "evidence": evidence})

    def rule_ctx(self, workbook: Any) -> RuleContext:
        resource = self._rule_resource
        if resource is None:
            if self._doc_type is not None:
                doc_type_name = self._doc_type.name
            else:
                doc_type_name = str(getattr(self.rule, "type", "") or "").strip() or "UNKNOWN"
            raise RuntimeError(f"文档类型{doc_type_name}缺少代码资源定义")
        version = self._file_version_label()
        cached = self._shared_rule_context
        if cached is not None and cached.workbook is workbook and cached.resource is resource:
            return cached
        ctx = RuleContext(
            ops=None,
            workbook=workbook,
            resource=resource,
            source_path=self._source_path,
            file_version_id=version,
        )
        ctx.bind_workbook(workbook, version)
        self._shared_rule_context = ctx
        return ctx

    def _resolve_ng_collection_topn(self) -> int:
        class_topn = getattr(type(self), "NG_COLLECTION_TOPN", None)
        if isinstance(class_topn, int) and class_topn > 0:
            return class_topn

        env_value = str(os.getenv(NG_COLLECTION_TOPN_ENV, "") or "").strip()
        if env_value:
            try:
                parsed = int(env_value)
            except ValueError:
                return DEFAULT_NG_COLLECTION_TOPN
            if parsed > 0:
                return parsed

        return DEFAULT_NG_COLLECTION_TOPN

    def _finalize_rule_result(
        self,
        *,
        status: str,
        message: str,
        ctx: RuleContext,
    ) -> tuple[str, str]:
        normalized_status = (status or "").upper()
        normalized_message = str(message or "")
        if normalized_status == "ERR":
            if type(self).CLAUSE_BOUND_CHECKS and ctx.has_collected_ng():
                summary = ctx.build_ng_summary()
                if summary and summary not in normalized_message:
                    normalized_message = f"{normalized_message}；{summary}" if normalized_message.strip() else summary
            if type(self).CLAUSE_BOUND_CHECKS:
                return self._apply_scope("ERR", normalized_message, ctx)
            return "ERR", normalized_message

        if not ctx.has_collected_ng():
            if (
                normalized_status == "PASS"
                and type(self).CLAUSE_BOUND_CHECKS
                and not ctx.questions()
                and ctx.finished_scans_yielded_nothing()
            ):
                empty_scope = ctx.empty_scope()
                if empty_scope is not None:
                    return self._apply_scope(empty_scope[0], empty_scope[1], ctx)
                if ctx.quantifier != "one":
                    return self._apply_scope("ERR", "空范围不能判为通过。", ctx)
            if normalized_status == "PASS" and type(self).CLAUSE_BOUND_CHECKS and ctx.execution_count == 0 and not ctx.questions():
                if ctx.has_scanned_row():
                    basis = "已经扫到数据行，不能按空范围通过。" if ctx.empty_scope() is not None else "零断言不能判为通过。"
                    return self._apply_scope("ERR", basis, ctx)
                empty_scope = ctx.empty_scope()
                if empty_scope is not None:
                    return self._apply_scope(empty_scope[0], empty_scope[1], ctx)
                if normalized_message.strip():
                    basis = "零断言不能判为通过。"
                else:
                    basis = "空结果不能判为通过。"
                return self._apply_scope("ERR", basis, ctx)
            return self._apply_scope(normalized_status, normalized_message, ctx)

        if normalized_status == "PASS":
            return self._apply_scope("NG", ctx.build_ng_summary(), ctx)
        if normalized_status == "NG":
            summary = ctx.build_ng_summary()
            text = normalized_message.strip()
            if text and text not in summary and summary not in text:
                summary = ctx.build_ng_summary(existing=text)
            return self._apply_scope("NG", summary or text, ctx)
        if normalized_status in {"UNABLE", "NA"} and type(self).CLAUSE_BOUND_CHECKS:
            text = normalized_message.strip()
            return self._apply_scope("NG", ctx.build_ng_summary(existing=text or None), ctx)
        return self._apply_scope(normalized_status, normalized_message, ctx)

    def _recorded_scan_scope(self, ctx: RuleContext | None) -> dict[str, int] | None:
        if ctx is None or not type(self).CLAUSE_BOUND_CHECKS:
            return None
        return ctx.finished_scan_scope()

    def _with_questions(self, result: AuditResult, ctx: RuleContext | None, rule_no: int) -> AuditResult:
        if ctx is None or not type(self).CLAUSE_BOUND_CHECKS or not ctx.questions():
            return result
        clause_id = ""
        declared = getattr(self, "_declared_checks", ())
        if 0 < rule_no <= len(declared):
            clause_id = declared[rule_no - 1].clause_id
        sources = ctx.read_sources()
        return result.model_copy(update={"clarifications": [{**item, "clause_id": clause_id, "sources": sources} for item in ctx.questions()]})

    def _apply_scope(self, status: str, message: str, ctx: RuleContext) -> tuple[str, str]:
        if not type(self).CLAUSE_BOUND_CHECKS:
            return status, message
        if status == "NA" and ctx.questions():
            return "UNABLE", self._scope_text("含义未确认，不能判为不适用。", ctx)
        if status == "PASS" and ctx.quantifier == "all":
            if not ctx.scope_is_complete():
                empty_scope = ctx.empty_scope()
                if (
                    empty_scope is not None
                    and empty_scope[0] == "PASS"
                    and ctx.empty_range_confirmed()
                    and not ctx.questions()
                    and not ctx.has_unresolved()
                ):
                    return "PASS", empty_scope[1]
                return "UNABLE", self._scope_text("范围不完整，不能判为通过。", ctx)
            if ctx.has_unresolved():
                return "UNABLE", self._scope_text("仍有未解问题，不能判为通过。", ctx)
        elif status == "PASS" and ctx.quantifier == "one":
            if ctx.questions():
                return "UNABLE", self._scope_text("含义未确认，不能判为通过。", ctx)
            if not ctx.has_witness():
                empty_scope = ctx.empty_scope()
                if (
                    empty_scope is not None
                    and empty_scope[0] == "PASS"
                    and ctx.empty_range_confirmed()
                    and not ctx.has_unresolved()
                ):
                    return "PASS", empty_scope[1]
                return "UNABLE", self._scope_text("没有有效见证，不能判为通过。", ctx)
            if not ctx.scope_is_complete() and "未完成全量扫描" not in message:
                message = f"{message}；未完成全量扫描。" if message else "未完成全量扫描。"
        elif status == "PASS" and ctx.quantifier is None and ctx.has_partial_scan():
            return "UNABLE", self._scope_text("范围不完整，不能判为通过。", ctx)
        elif status == "PASS" and ctx.quantifier is None and ctx.has_unresolved():
            return "UNABLE", self._scope_text("仍有未解问题，不能判为通过。", ctx)
        if status in {"NG", "UNABLE", "NA", "ERR"} and ctx.has_unresolved():
            return status, self._append_unresolved(message, ctx)
        if status == "PASS" and ctx.quantifier == "one":
            return status, self._append_unresolved(message, ctx)
        return status, message

    @staticmethod
    def _scope_text(basis: str, ctx: RuleContext) -> str:
        extra = ctx.unresolved_summary()
        if not extra or extra in basis:
            return basis
        return f"{basis}{extra}"

    @staticmethod
    def _append_unresolved(message: str, ctx: RuleContext) -> str:
        extra = ctx.unresolved_summary()
        if not extra or extra in message:
            return message
        return f"{message}；{extra}" if message else extra

    def _evidence_from_reads(self, ctx: RuleContext) -> AuditEvidence | None:
        if not type(self).CLAUSE_BOUND_CHECKS:
            return None
        reads = ctx.cell_reads()
        if not reads:
            return None
        sources = []
        for version, sheet, row, column in reads:
            payload = {"kind": "xlsx_cell", "sheet": sheet, "cell": f"{get_column_letter(column)}{row}"}
            if version:
                payload["file_version_id"] = version
            sources.append(
                EvidenceSource(
                    kind="structure",
                    locator=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    excerpt=ctx.cell_excerpt(version, sheet, row, column),
                )
            )
        diagnostics = ()
        if ctx.cell_reads_truncated():
            diagnostics = (
                EvidenceDiagnostic(
                    code="read_locations_truncated",
                    stage="structure",
                    severity="warning",
                    message="本次读取位置超过展示上限。未列出的读取已经执行，展示上限不是完整定位。",
                ),
            )
        suffix = Path(str(self._source_path or getattr(self.audit_file, "file_url", "") or "")).suffix.lower().lstrip(".")
        source_format = suffix if suffix in {"xlsx", "xls", "xlsm", "pdf", "docx", "doc"} else "xlsx"
        prepared_format = "xlsx" if source_format in {"pdf", "doc"} else source_format if source_format in {"xlsx", "xls", "xlsm", "docx"} else "xlsx"
        return AuditEvidence(
            required="structure",
            used="structure",
            source_format=source_format,
            prepared_format=prepared_format,
            sources=tuple(sources),
            diagnostics=diagnostics,
        )

    def _finalize_audit_result(
        self,
        result: AuditResult,
        *,
        rule_no: int,
        phase: RuleEvidencePhase,
        ctx: RuleContext | None = None,
    ) -> AuditResult:
        """Allow a document adapter to decorate the final result."""
        del rule_no, phase
        if ctx is not None and result.evidence is None:
            evidence = self._evidence_from_reads(ctx)
            if evidence is not None:
                result = result.model_copy(update={"evidence": evidence})
        return self._with_source_evidence(result)

    def _execute_rule_check(self, rule_no: int) -> Optional[AuditResult]:
        """
        执行规则检查的核心方法，整合了文件检查和结果转换

        Args:
            rule_no: 规则编号（1开始）

        Returns:
            AuditResult或None
        """

        # 验证规则编号
        if rule_no < 1 or rule_no > len(self.rule_checkers):
            return self.create_error_result(
                f"无效的规则编号: {rule_no}，必须是 1 ~ {len(self.rule_checkers)} 之间的整数",
                rule_no - 1,
            )

        # 加载工作簿并执行规则检查
        ctx: RuleContext | None = None
        try:
            workbook = self._load_workbook()
            ctx = self.rule_ctx(workbook)
            ctx.enable_ng_collection(topn=self._resolve_ng_collection_topn())

            checker = self.rule_checkers[rule_no - 1]
            outcome = checker(workbook, ctx=ctx)
            if not isinstance(outcome, tuple) or len(outcome) != 2 or any(not isinstance(part, str) for part in outcome):
                return self._finalize_audit_result(
                    self._failure_result(ctx, rule_no, "检查没有返回状态和说明。方法必须 return 状态, 说明。"),
                    rule_no=rule_no,
                    phase="error",
                    ctx=ctx,
                )
            result, message = outcome

            result, message = self._finalize_rule_result(
                status=result,
                message=message,
                ctx=ctx,
            )
            empty_scope = ctx.empty_scope() if type(self).CLAUSE_BOUND_CHECKS else None
            empty_basis = None
            if (
                empty_scope is not None
                and result == empty_scope[0]
                and not ctx.has_scanned_row()
                and (ctx.execution_count == 0 or ctx.finished_scans_yielded_nothing())
            ):
                empty_basis = empty_scope[1]

            item = self.agent_rules[rule_no - 1] if rule_no <= len(self.agent_rules) else f"[{self.document_type_name}]规则检查-{rule_no}"
            return self._finalize_audit_result(
                self._with_questions(
                    AuditResult(
                        classification=self.document_type_name,
                        item=item,
                        priority=self._get_rule_priority(rule_no),
                        status=result.upper(),
                        suggestions=message,
                        checks_performed=ctx.execution_count if type(self).CLAUSE_BOUND_CHECKS else None,
                        empty_scope_basis=empty_basis,
                        scope=self._recorded_scan_scope(ctx),
                    ),
                    ctx,
                    rule_no,
                ),
                rule_no=rule_no,
                phase="decision",
                ctx=ctx,
            )

        except PdfSourceError as exc:
            self._conversion_artifact = exc.artifact
            item = self.agent_rules[rule_no - 1] if 0 < rule_no <= len(self.agent_rules) else f"[{self.document_type_name}]规则检查-{rule_no}"
            return self._finalize_audit_result(
                AuditResult(
                    classification=self.document_type_name,
                    item=item,
                    priority=self._get_rule_priority(rule_no),
                    status="ERR",
                    suggestions=f"PDF 未能转换成可检查的表格: {exc}",
                ),
                rule_no=rule_no,
                phase="error",
                ctx=ctx,
            )
        except GuardStop as stop:
            if ctx is None:
                status, suggestions, performed = stop.status, stop.message, None
            else:
                status, suggestions = self._finalize_rule_result(status=stop.status, message=stop.message, ctx=ctx)
                performed = ctx.execution_count if type(self).CLAUSE_BOUND_CHECKS else None
            item = self.agent_rules[rule_no - 1] if rule_no <= len(self.agent_rules) else f"[{self.document_type_name}]规则检查-{rule_no}"
            return self._finalize_audit_result(
                self._with_questions(
                    AuditResult(
                        classification=self.document_type_name,
                        item=item,
                        priority=self._get_rule_priority(rule_no),
                        status=status,
                        suggestions=suggestions,
                        checks_performed=performed,
                        scope=self._recorded_scan_scope(ctx),
                    ),
                    ctx,
                    rule_no,
                ),
                rule_no=rule_no,
                phase="guard",
                ctx=ctx,
            )
        except Exception as exc:
            logger.exception("检查规则 %s 时出错: %s", rule_no, exc)
            return self._finalize_audit_result(
                self._failure_result(ctx, rule_no, f"检查规则{rule_no}时出错: {exc}"),
                rule_no=rule_no,
                phase="error",
                ctx=ctx,
            )

    def _failure_result(self, ctx: RuleContext | None, rule_no: int, message: str) -> AuditResult:
        """Keep a technical failure from hiding a counterexample or an open question."""
        if ctx is None or not type(self).CLAUSE_BOUND_CHECKS:
            return self.create_error_result(message, rule_no - 1)
        status, suggestions = self._finalize_rule_result(status="ERR", message=message, ctx=ctx)
        item = self.agent_rules[rule_no - 1] if rule_no <= len(self.agent_rules) else f"[{self.document_type_name}]规则检查-{rule_no}"
        return self._with_questions(
            AuditResult(
                classification=self.document_type_name,
                item=item,
                priority=self._get_rule_priority(rule_no),
                status=status,
                suggestions=suggestions,
                checks_performed=ctx.execution_count,
                scope=ctx.finished_scan_scope(),
            ),
            ctx,
            rule_no,
        )

    def _get_rule_priority(self, rule_no: int) -> str:
        """
        获取规则优先级，子类可以重写此方法

        Args:
            rule_no: 规则编号（1开始）

        Returns:
            str: 优先级（"P0", "P1", "P2"等）
        """
        return "P1"  # 默认优先级
