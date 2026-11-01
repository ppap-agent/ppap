"""
Excel文件专用审核代理基类

在BaseAgent和具体Excel文档审核代理之间提供专门的Excel文件处理功能。
所有处理Excel文件的PPAP审核代理都应该继承此类。
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional, Tuple, cast
from urllib.parse import unquote, urlparse

from documents.contracts import FileRef
from documents.excel import load_excel_workbook
from openpyxl import Workbook

from audit.checkers.v2.base_agent import BaseAgent
from audit.contracts import (
    AuditInput,
    GroupContextInput,
    RuleInput,
)
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.engine.rule_engine import (
    GuardStop,
    RuleContext,
    get_doc_resource_by_doc_type,
)
from audit.kinds import PpapDocumentType

logger = logging.getLogger(__name__)
DEFAULT_NG_COLLECTION_TOPN = 5
NG_COLLECTION_TOPN_ENV = "PPAP_NG_TOPN"
GENERIC_RULE_CATEGORIES = {"local_excel_check"}
RuleEvidencePhase = Literal["decision", "guard", "error"]


def rule(description: str) -> Callable[[Any], Any]:
    """
    规则装饰器，用于自动注册规则描述和检查器。
    """

    def decorator(func: Any) -> Any:
        func.__rule_description__ = description
        return func

    return decorator


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

    @classmethod
    def declared_rules(cls) -> tuple[tuple[str, str], ...]:
        """Return deterministic method names and descriptions without loading a file."""
        return discover_declared_rules(cls)

    def __init__(
        self,
        audit_file: AuditInput,
        rule: RuleInput,
        group_context: GroupContextInput | None = None,
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
        self._doc_type = self._resolve_resource_doc_type()
        self._rule_resource = get_doc_resource_by_doc_type(self._doc_type) if self._doc_type is not None else None
        self._source_path = self._resolve_local_file_path(str(getattr(self.audit_file, "file_url", "") or ""))

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

    def check(self, ith: int) -> Optional[AuditResult]:
        """
        执行第ith条规则的审核，提供Excel文件的标准检查流程

        当代理拥有自身的 rule_checkers 时，直接按 ith 索引调用对应的
        检查器，不依赖规则文件中的条目（规则文件条目数/顺序可能与
        agent_rules 不一致）。

        Args:
            ith: 规则索引（从0开始）

        Returns:
            AuditResult: 审核结果
        """
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
        file_url = getattr(self.audit_file, "file_url")
        from audit.engine.runtime import get_runtime
        runtime = get_runtime()
        if runtime is not None:
            wb = runtime.load_workbook_auto(FileRef(location=str(file_url)))
        else:
            wb = load_excel_workbook(file_url)
        if not isinstance(wb, Workbook):
            raise TypeError(f"Expected openpyxl Workbook, got {type(wb).__name__}")
        self._workbook_cache = wb
        return wb

    def rule_ctx(self, workbook: Any) -> RuleContext:
        resource = self._rule_resource
        if resource is None:
            if self._doc_type is not None:
                doc_type_name = self._doc_type.name
            else:
                doc_type_name = str(getattr(self.rule, "type", "") or "").strip() or "UNKNOWN"
            raise RuntimeError(f"文档类型{doc_type_name}缺少代码资源定义")
        return RuleContext(
            ops=None,
            workbook=workbook,
            resource=resource,
            source_path=self._source_path,
        )

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
            return normalized_status, normalized_message

        if not ctx.has_collected_ng():
            return normalized_status, normalized_message

        if normalized_status == "PASS":
            return "NG", ctx.build_ng_summary()
        if normalized_status == "NG":
            return "NG", ctx.build_ng_summary(existing=normalized_message)
        return normalized_status, normalized_message

    def _finalize_audit_result(
        self,
        result: AuditResult,
        *,
        rule_no: int,
        phase: RuleEvidencePhase,
    ) -> AuditResult:
        """Allow a document adapter to decorate the final result."""
        del rule_no, phase
        return result

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
            result, message = checker(workbook, ctx=ctx)

            result, message = self._finalize_rule_result(
                status=result,
                message=message,
                ctx=ctx,
            )

            item = self.agent_rules[rule_no - 1] if rule_no <= len(self.agent_rules) else f"[{self.document_type_name}]规则检查-{rule_no}"
            return self._finalize_audit_result(
                AuditResult(
                    classification=self.document_type_name,
                    item=item,
                    priority=self._get_rule_priority(rule_no),
                    status=result.upper(),
                    suggestions=message,
                ),
                rule_no=rule_no,
                phase="decision",
            )

        except GuardStop as stop:
            suggestions = stop.message
            if stop.status == "NG" and ctx is not None and ctx.has_collected_ng():
                suggestions = ctx.build_ng_summary(existing=stop.message)
            item = self.agent_rules[rule_no - 1] if rule_no <= len(self.agent_rules) else f"[{self.document_type_name}]规则检查-{rule_no}"
            return self._finalize_audit_result(
                AuditResult(
                    classification=self.document_type_name,
                    item=item,
                    priority=self._get_rule_priority(rule_no),
                    status=stop.status,
                    suggestions=suggestions,
                ),
                rule_no=rule_no,
                phase="guard",
            )
        except Exception as exc:
            logger.exception("检查规则 %s 时出错: %s", rule_no, exc)
            error_result = self.create_error_result(f"检查规则{rule_no}时出错: {exc}", rule_no - 1)
            return self._finalize_audit_result(
                error_result,
                rule_no=rule_no,
                phase="error",
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
