"""Shared DOCX PSW adapter over structured and source-located Word evidence."""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Optional, Protocol, Sequence, cast
from xml.etree import ElementTree
from zipfile import ZipFile

from documents.model_access import ModelUnavailable
from documents.word import WordBlock, WordDocument, WordParagraphBlock, WordTableBlock
from openpyxl import Workbook
from PIL import Image

from audit.contracts import AuditInput, EvidenceDiagnostic, EvidenceSource
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Table

from .docx_workbook_agent import (
    PSW_DOCX_PRIMARY_TABLE,
    DocxPrimaryTableContract,
    WordEvidenceExcelOps,
)
from .psw_evidence_trace import (
    DIAGNOSTIC_EVALUATION_RUNTIME_ERROR,
    DIAGNOSTIC_STRUCTURE_INSUFFICIENT,
    DIAGNOSTIC_VISUAL_MISSING,
    DIAGNOSTIC_VISUAL_RUNTIME_ERROR,
    EvidenceUse,
    with_psw_evidence,
)
from .psw_visual_evidence import PSW_ALWAYS_VISUAL_RULES, PSW_RULE_FIELDS, evaluate_psw_observation


def _no_opaque_docx_graphics(path: str) -> bool:
    """Only admit absence when no unparsed graphic carrier remains."""
    try:
        with ZipFile(path) as archive:
            parts = archive.infolist()
            if any(item.filename.startswith(("word/media/", "word/embeddings/", "word/fonts/", "word/footnotes", "word/endnotes", "word/comments")) for item in parts):
                return False
            xml_parts = [item for item in parts if item.filename.startswith("word/") and item.filename.endswith((".xml", ".rels"))]
            if sum(item.file_size for item in xml_parts) > 10_000_000:
                return False
            opaque = {"drawing", "pict", "object", "shape", "graphic", "chart", "sym", "AlternateContent", "oMath", "oMathPara"}
            for item in xml_parts:
                root = ElementTree.fromstring(archive.read(item.filename))
                if any(
                    node.tag.rsplit("}", 1)[-1] in opaque or (node.tag.endswith("Relationship") and str(node.get("Type", "")).rsplit("/", 1)[-1] in {"image", "oleObject", "package", "chart", "font"})
                    for node in root.iter()
                ):
                    return False
        return True
    except Exception:
        return False


class _PswDocxHost(Protocol):
    audit_file: AuditInput
    word_document: WordDocument

    def _load_workbook(self) -> Workbook: ...

    def rule_ctx(self, workbook: Workbook) -> RuleContext: ...

    def log_audit_start(self, ith: int) -> None: ...

    def log_audit_result(self, result: AuditResult, ith: int) -> None: ...

    def create_error_result(self, error_msg: str, ith: int = -1) -> AuditResult: ...


class PswDocxEvidenceMixin:
    """Route PSW DOCX rules without rendering or office conversion."""

    _docx_visual_facts: tuple[dict[str, object], list] | Exception
    DOCX_PRIMARY_TABLE_CONTRACT: DocxPrimaryTableContract | None = PSW_DOCX_PRIMARY_TABLE
    DOCX_NAMED_TABLE_ANCHORS: tuple[tuple[str, tuple[str, ...], int], ...] = (("PSW交付物清单", ("生产件批准交付物文件清单", "项目", "附件"), 2),)

    def _host(self) -> _PswDocxHost:
        return cast(_PswDocxHost, self)

    def _trace_result(
        self,
        result: AuditResult,
        *,
        rule_no: int,
        used: EvidenceUse,
        diagnostic: EvidenceDiagnostic | None = None,
        sources: tuple[EvidenceSource, ...] | None = None,
    ) -> AuditResult:
        ops = getattr(self, "_word_ops_cache", None)
        if sources is not None and ops is not None:
            located = []
            for source in sources:
                try:
                    raw = json.loads(source.locator)
                except (ValueError, TypeError):
                    located.append(source)
                    continue
                locator = ops.source_locator(str(raw.get("part", "")), str(raw.get("path", "")))
                if locator is not None:
                    locator["file_version_id"] = self._host().audit_file.audit_id
                    source = source.model_copy(update={"locator": json.dumps(locator, ensure_ascii=False)})
                located.append(source)
            sources = tuple(located)
        return with_psw_evidence(
            result,
            audit=self._host().audit_file,
            rule_no=rule_no,
            used=used,
            diagnostics=(diagnostic,) if diagnostic is not None else (),
            sources=sources,
        )

    def _word_text_evidence(self) -> list[WordParagraphBlock]:
        sources: list[WordParagraphBlock] = []

        def visit(blocks: Sequence[WordBlock]) -> None:
            for block in blocks:
                if isinstance(block, WordParagraphBlock):
                    if block.text.strip():
                        sources.append(block)
                elif isinstance(block, WordTableBlock):
                    for cell in block.cells:
                        if cell.origin:
                            visit(cell.blocks)

        document = self._host().word_document
        visit((*document.blocks, *document.headers, *document.footers))
        return sources

    def _missing_visual_result(self, rule_no: int) -> AuditResult:
        document = getattr(self, "word_document", None)
        sources = []
        if document is not None:
            sources = [f"{item.source.part}:{item.source.path}" for item in document.visual_evidence if item.kind in {"embedded_image", "drawing"}]
        source_note = f"；已发现但无法验证的来源: {', '.join(sources)}" if sources else ""
        if document is not None and not any(item.kind in {"embedded_image", "drawing"} for item in document.visual_evidence) and _no_opaque_docx_graphics(self._host().audit_file.file_url):
            result = evaluate_psw_observation(
                rule_no, {"psw_stamp_ends_with_company": False, "authorization_table_present": False, "authorization_company_stamp_present": False, "psw_stamp_present": False}
            )
            return self._trace_result(
                result, rule_no=rule_no, used="structure", sources=(EvidenceSource(kind="structure", locator="docx://word/package", excerpt="完整原件未包含图形、绘图或内嵌图片。"),)
            )
        return self._trace_result(
            AuditResult(
                classification="DOCX视觉证据",
                item=f"PSW - 规则{rule_no}",
                priority="P0",
                status="UNABLE",
                suggestions=f"未找到可验证的印章或签字事实，请核对原件{source_note}",
            ),
            rule_no=rule_no,
            used="none",
            diagnostic=EvidenceDiagnostic(
                code=DIAGNOSTIC_VISUAL_MISSING,
                stage="visual",
                severity="warning",
                message="DOCX 未找到可验证的内嵌视觉证据；缺少证据不证明缺少印章或签字。",
            ),
        )

    def check(self, ith: int) -> Optional[AuditResult]:
        return self._check_docx_rule(ith, source_bound=False)

    def check_source_rule(self, ith: int) -> Optional[AuditResult]:
        """Preserve Word visuals without legacy generic-model fallback."""
        return self._check_docx_rule(ith, source_bound=True)

    def _check_docx_rule(self, ith: int, *, source_bound: bool) -> Optional[AuditResult]:
        rule_no = ith + 1
        if rule_no not in PSW_ALWAYS_VISUAL_RULES:
            if source_bound:
                direct_check = getattr(self, "_execute_rule_check")
                return cast(Optional[AuditResult], direct_check(rule_no))
            parent_check = getattr(super(), "check")
            return cast(Optional[AuditResult], parent_check(ith))

        host = self._host()
        host.log_audit_start(ith)
        try:
            workbook = host._load_workbook()
            context = host.rule_ctx(workbook)
            ops = cast(WordEvidenceExcelOps, context.ops)
            evidence = ops.all_image_evidence()
            if not evidence:
                result = self._missing_visual_result(rule_no)
            else:
                from .ocr_visual_facts import OcrImage, observe_ocr_images

                audit = host.audit_file
                digest = hashlib.sha256(Path(audit.file_url).read_bytes()).hexdigest()
                previous = getattr(self, "_docx_visual_identity", digest)
                if previous != digest:
                    raise ValueError("Frozen Word source changed")
                self._docx_visual_identity = digest
                if not hasattr(self, "_docx_visual_facts"):
                    observed_sources = []
                    try:
                        images = []
                        for item in evidence:
                            payload = base64.b64decode(item.data_url.split(",", 1)[-1], validate=True)
                            with Image.open(BytesIO(payload)) as original:
                                width, height = original.size
                                if width * height > 12_000_000:
                                    raise GuardStop(Decision.UNABLE, "原件图像超出本次有界读取范围，尚不能确认视觉事实。")
                                original.load()
                                output = BytesIO()
                                original.convert("RGB").save(output, format="PNG")
                            images.append(
                                OcrImage(
                                    output.getvalue(),
                                    {
                                        "kind": "docx_image",
                                        "part": item.evidence.source.part,
                                        "path": item.evidence.source.path,
                                        "file_version_id": audit.audit_id,
                                        "source_sha256": digest,
                                        "coordinate_system": "image_pixels",
                                        "bbox": [0, 0, width, height],
                                        "scope": "docx:psw",
                                        "context": True,
                                    },
                                    width,
                                    height,
                                )
                            )
                        fields = {field: description for number in (3, 5) for field, description in PSW_RULE_FIELDS[number].items()}
                        text_evidence = self._word_text_evidence()
                        native_text = "\n".join(f"{block.source.part}:{block.source.path}: {block.text}" for block in text_evidence)
                        facts = observe_ocr_images(images, fields, lambda *args, **kwargs: observed_sources.append((args, kwargs)), context=native_text)
                        # A source-located native value in the signature field
                        # is sufficient; another printed name is not.
                        from .excel_field_evidence import read_native_signature

                        native_signature, _ = read_native_signature(context, Table(context).sheet("PSW主表").ws)
                        if native_signature is True:
                            facts["authorized_signature_present"] = True
                            for version, sheet, row, column in context.cell_reads():
                                locator = ops.cell_locator(context.workbook[sheet], row, column)
                                if locator is not None:
                                    locator["file_version_id"] = version or audit.audit_id
                                    observed_sources.append(((locator | {"fact": "authorized_signature_present"}, "经授权的供方代表签字字段存在原生内容。"), {"visual": False}))
                        if hashlib.sha256(Path(audit.file_url).read_bytes()).hexdigest() != digest:
                            raise ValueError("Frozen Word source changed")
                        self._docx_visual_facts = (facts, observed_sources)
                    except Exception as exc:
                        self._docx_visual_facts = exc
                cached = self._docx_visual_facts
                if isinstance(cached, Exception):
                    raise cached
                facts, observed_sources = cached
                sources = tuple(
                    EvidenceSource(
                        kind="visual" if kwargs.get("visual") else "structure",
                        locator=json.dumps(
                            {key: value for key, value in args[0].items() if key != "fact"} if args[0].get("kind") in {"docx_cell", "docx_paragraph"} else args[0],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        excerpt=args[1],
                    )
                    for args, kwargs in observed_sources
                    if args[0].get("fact") in PSW_RULE_FIELDS[rule_no]
                )
                result = evaluate_psw_observation(rule_no, facts)
                kinds = {source.kind for source in sources}
                result = self._trace_result(
                    result,
                    rule_no=rule_no,
                    used="both" if kinds == {"visual", "structure"} else "visual" if "visual" in kinds else "structure" if kinds else "none",
                    diagnostic=EvidenceDiagnostic(code="psw.visual.unconfirmed", stage="visual", severity="warning", message="原件视觉事实尚不能确认，请核对原件。")
                    if result.status == "UNABLE"
                    else None,
                    sources=sources,
                )
        except ModelUnavailable:
            raise  # Core exposes unavailable personal model access as UNABLE.
        except GuardStop as stop:
            is_runtime_error = stop.status == "ERR"
            result = self._trace_result(
                AuditResult(
                    classification="DOCX结构化证据",
                    item=f"PSW - 规则{rule_no}",
                    priority="P0",
                    status="ERR" if is_runtime_error else "UNABLE",
                    suggestions=stop.message,
                ),
                rule_no=rule_no,
                used="none" if is_runtime_error else "structure",
                diagnostic=EvidenceDiagnostic(
                    code=(DIAGNOSTIC_EVALUATION_RUNTIME_ERROR if is_runtime_error else DIAGNOSTIC_STRUCTURE_INSUFFICIENT),
                    stage="evaluation" if is_runtime_error else "structure",
                    severity="error" if is_runtime_error else "warning",
                    message=("DOCX 规则执行发生运行时错误。" if is_runtime_error else "DOCX 结构不足，无法定位 PSW 视觉证据。"),
                ),
            )
        except Exception as exc:
            result = self._trace_result(
                host.create_error_result(f"DOCX证据处理失败（{type(exc).__name__}）。", ith),
                rule_no=rule_no,
                used="none",
                diagnostic=EvidenceDiagnostic(
                    code=DIAGNOSTIC_VISUAL_RUNTIME_ERROR,
                    stage="visual",
                    severity="error",
                    message="DOCX 视觉证据处理发生运行时错误。",
                ),
            )
        host.log_audit_result(result, ith)
        return result


__all__ = ["PswDocxEvidenceMixin"]
