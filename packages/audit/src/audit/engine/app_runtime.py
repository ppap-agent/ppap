"""In-repo audit runtime: DTO mapping plus engine call. Worker uses this, not ppap-agent."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator, Optional
from urllib.parse import unquote, urlparse

from documents.contracts import FileRef
from documents.conversion import convert_pdf_to_excel_auto, load_workbook_auto
from documents.files import download_file_sync
from documents.images.image import extract_images_from_bytes
from documents.word import (
    LegacyDocConversionError,
    LegacyDocConversionLimits,
    convert_legacy_doc_to_docx,
)
from pydantic import BaseModel

from audit.contracts import (
    AuditEvidence,
    AuditInput,
    AuditResultItem,
    EvidenceDiagnostic,
    GroupContextInput,
    RuleInput,
)
from audit.engine.checkers.client import AIClient
from audit.engine.checkers.psw_evidence_trace import (
    DIAGNOSTIC_DOC_CONVERSION_FAILED,
    DIAGNOSTIC_STRUCTURE_INSUFFICIENT,
    with_psw_evidence,
)
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.engine.contract_mapper import to_audit_input, to_rule_input
from audit.engine.docx_quality import (
    DocxAuditQualityError,
    validate_converted_docx_audit_quality,
)
from audit.engine.engine import run_audit
from audit.engine.routing import resolve_engine_agent_class
from audit.engine.runtime import AuditRuntime
from audit.kinds import PpapDocumentType


class AuditResult(BaseModel):
    classification: str
    item: str
    priority: str
    status: str
    suggestions: str
    evidence: Optional[AuditEvidence] = None


def _load_file_bytes(file_url: str) -> tuple[bytes, str]:
    parsed = urlparse(file_url)
    file_type = Path(unquote(parsed.path or file_url)).suffix.lower() or ".pdf"

    if parsed.scheme in ("http", "https"):
        file_content, _headers = download_file_sync(file_url, max_file_size=100 * 1024 * 1024)
        return file_content, file_type

    path_str = parsed.path if parsed.scheme == "file" else file_url
    local_path = Path(unquote(path_str))
    return local_path.read_bytes(), local_path.suffix.lower() or file_type


class _AppAuditRuntime(AuditRuntime):
    """Runtime hooks the in-repo engine needs for PDF/workbook/vision."""

    def load_workbook_auto(self, file_ref: FileRef) -> Any:
        return load_workbook_auto(file_ref, enable_cache=True)

    def convert_pdf_to_excel(
        self,
        file_ref: FileRef,
        *,
        enable_cache: bool = True,
    ) -> Any:
        return convert_pdf_to_excel_auto(
            file_ref,
            enable_cache=enable_cache,
        )

    def render_pdf_images(self, file_url: str, *, render_all_pages: bool = True) -> list[str]:
        file_content, file_type = _load_file_bytes(file_url)
        return extract_images_from_bytes(file_content, file_type, render_all_pages=render_all_pages)

    def run_image_prompt(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_base64_list: list[str],
        max_tokens: int = 12000,
        temperature: float = 0.1,
        json_format: bool = True,
    ) -> str:
        return AIClient().image_analysis_completion(
            system_prompt=system_prompt,
            user_text=user_text,
            image_base64_list=image_base64_list,
            max_tokens=max_tokens,
            temperature=temperature,
            json_format=json_format,
        )


def _build_group_context_input(group_context: Any) -> Optional[GroupContextInput]:
    if group_context is None:
        return None
    if isinstance(group_context, GroupContextInput):
        return group_context

    task_group_id = getattr(group_context, "task_group_id", None)

    by_audit_id = {str(audit_id): to_audit_input(audit_file) for audit_id, audit_file in (getattr(group_context, "by_audit_id", {}) or {}).items()}

    by_doc_type: dict[str, list[AuditInput]] = {}
    source_by_doc_type = getattr(group_context, "by_doc_type", {}) or {}
    for doc_type, audit_files in source_by_doc_type.items():
        if isinstance(doc_type, PpapDocumentType):
            doc_type_key = doc_type.get_type_name()
        else:
            doc_type_key = str(doc_type)
        by_doc_type[doc_type_key] = [to_audit_input(audit_file) for audit_file in (audit_files or [])]

    rules_by_id = {str(rule_id): to_rule_input(rule) for rule_id, rule in (getattr(group_context, "rules_by_id", {}) or {}).items()}

    return GroupContextInput(
        task_group_id=str(task_group_id) if task_group_id is not None else None,
        by_doc_type=by_doc_type,
        by_audit_id=by_audit_id,
        rules_by_id=rules_by_id,
        unavailable_files=getattr(group_context, "unavailable_files", {}) or {},
    )


def _to_audit_result(item: AuditResultItem) -> AuditResult:
    return AuditResult(
        classification=item.classification,
        item=item.item,
        priority=item.priority,
        status=item.status,
        suggestions=item.suggestions,
        evidence=item.evidence,
    )


def _legacy_doc_quality_results(
    audit_input: AuditInput,
    rule_input: RuleInput,
    error: DocxAuditQualityError,
) -> list[AuditResult]:
    """Return non-retryable NG results when readable conversion evidence is insufficient."""
    doc_type = PpapDocumentType.from_string(rule_input.type or "")
    type_name = doc_type.get_type_name()
    agent_cls = resolve_engine_agent_class(
        doc_type,
        "converted.docx",
        rule_input.rule_profile,
    )
    declared_rules = tuple(description for _method, description in discover_declared_rules(agent_cls)) if agent_cls is not None else ()
    if not declared_rules:
        declared_rules = tuple(str(rule.get("category") or rule.get("description") or f"规则{index}") for index, rule in enumerate(rule_input.rules or (), start=1)) or (type_name,)
    results = [
        AuditResultItem(
            classification="DOCX转换质量",
            item=f"{type_name} - {description}",
            priority="P0",
            status="NG",
            suggestions=str(error),
        )
        for description in declared_rules
    ]
    if doc_type == PpapDocumentType.PSW:
        results = [
            with_psw_evidence(
                result,
                audit=audit_input,
                rule_no=index,
                used="none",
                diagnostics=(
                    EvidenceDiagnostic(
                        code=DIAGNOSTIC_STRUCTURE_INSUFFICIENT,
                        stage="structure",
                        severity="error",
                        message="DOC 转换成功，但 DOCX 结构质量不足。",
                    ),
                ),
            )
            for index, result in enumerate(results, start=1)
        ]
    return [_to_audit_result(result) for result in results]


def _legacy_doc_conversion_error_results(
    audit_input: AuditInput,
    rule_input: RuleInput,
    error: LegacyDocConversionError,
) -> list[AuditResult]:
    """Return searchable ERR items when the local DOC converter cannot run."""
    doc_type = PpapDocumentType.from_string(rule_input.type or "")
    if doc_type != PpapDocumentType.PSW:
        raise error
    agent_cls = resolve_engine_agent_class(doc_type, "converted.docx", rule_input.rule_profile)
    declared_rules = tuple(description for _method, description in discover_declared_rules(agent_cls)) if agent_cls is not None else ()
    results: list[AuditResult] = []
    for index, description in enumerate(declared_rules, start=1):
        item = with_psw_evidence(
            AuditResultItem(
                classification="DOC转换运行时",
                item=f"{doc_type.get_type_name()} - {description}",
                priority="P0",
                status="ERR",
                suggestions=f"旧 DOC 本地转换失败（{error.code}）：{error}",
            ),
            audit=audit_input,
            rule_no=index,
            used="none",
            diagnostics=(
                EvidenceDiagnostic(
                    code=DIAGNOSTIC_DOC_CONVERSION_FAILED,
                    stage="conversion",
                    severity="error",
                    message=f"旧 DOC 本地转换失败（{error.code}）。",
                ),
            ),
        )
        results.append(_to_audit_result(item))
    return results


@contextmanager
def _normalize_legacy_doc_audit_input(
    audit_input: AuditInput,
    rule_input: RuleInput,
) -> Iterator[AuditInput]:
    """Adapt legacy DOC at the worker boundary; leave the engine DOCX-only."""
    parsed = urlparse(audit_input.file_url)
    suffix = Path(unquote(parsed.path or audit_input.file_url)).suffix.lower()
    if suffix != ".doc":
        yield audit_input
        return

    doc_type = PpapDocumentType.from_string(rule_input.type or "")
    if doc_type not in {PpapDocumentType.EQUIPMENT_LIST, PpapDocumentType.PSW}:
        display_type = doc_type.get_type_name() if doc_type is not PpapDocumentType.UNKNOWN else rule_input.type or "未知类型"
        raise ValueError(f"旧 DOC 当前仅支持设备清单和零件提交保证书，不支持 {display_type}。")

    limits = LegacyDocConversionLimits()
    if parsed.scheme in ("http", "https"):
        file_content, _headers = download_file_sync(
            audit_input.file_url,
            max_file_size=limits.max_input_bytes,
        )
    else:
        path_str = parsed.path if parsed.scheme == "file" else audit_input.file_url
        local_path = Path(unquote(path_str))
        if local_path.stat().st_size > limits.max_input_bytes:
            raise ValueError(f"旧 DOC 超过 {limits.max_input_bytes} 字节大小上限。")
        file_content = local_path.read_bytes()
    source_name = Path(unquote(parsed.path or audit_input.file_url)).name or "input.doc"
    converted = convert_legacy_doc_to_docx(file_content, source_name, limits=limits)
    validate_converted_docx_audit_quality(converted, doc_type)

    with TemporaryDirectory(prefix="ppap-audit-docx-") as temp_dir:
        converted_path = Path(temp_dir) / f"{Path(source_name).stem}.docx"
        converted_path.write_bytes(converted)
        metadata = dict(audit_input.file_metadata)
        metadata["legacy_doc_conversion"] = {
            "source_format": ".doc",
            "converter": "local_libreoffice",
        }
        yield audit_input.model_copy(
            update={
                "file_url": str(converted_path),
                "file_metadata": metadata,
            }
        )


def run_audit_engine(
    audit_file: Any,
    rule: Any,
    group_context: Any = None,
) -> list[AuditResult]:
    audit_input = to_audit_input(audit_file)
    rule_input = to_rule_input(rule)
    group_context_input = _build_group_context_input(group_context)
    runtime = _AppAuditRuntime()
    try:
        with _normalize_legacy_doc_audit_input(audit_input, rule_input) as normalized_audit_input:
            items = run_audit(
                normalized_audit_input,
                rule_input,
                group_context=group_context_input,
                runtime=runtime,
            )
    except LegacyDocConversionError as error:
        return _legacy_doc_conversion_error_results(audit_input, rule_input, error)
    except DocxAuditQualityError as error:
        if error.code != "insufficient_key_evidence":
            raise
        return _legacy_doc_quality_results(audit_input, rule_input, error)
    return [_to_audit_result(item) for item in items]
