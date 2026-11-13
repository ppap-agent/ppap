"""控制计划审核代理（含跨文件一致性规则）。"""

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from audit.checker_lib.nio.control_plan_shared import (
    CP_FIELD_ALIASES as _CP_FIELD_ALIASES,
)
from audit.checker_lib.nio.control_plan_shared import (
    HEADER_SCAN_COLS as _HEADER_SCAN_COLS,
)
from audit.checker_lib.nio.control_plan_shared import (
    HEADER_SCAN_ROWS as _HEADER_SCAN_ROWS,
)
from audit.checker_lib.nio.control_plan_shared import (
    PFD_FIELD_ALIASES as _PFD_FIELD_ALIASES,
)
from audit.checker_lib.nio.control_plan_shared import (
    PFMEA_FIELD_ALIASES as _PFMEA_FIELD_ALIASES,
)
from audit.checker_lib.nio.control_plan_shared import (
    SCL_FIELD_ALIASES_V2 as _SCL_FIELD_ALIASES_V2,
)
from audit.checker_lib.nio.control_plan_shared import (
    SPECIAL_STRICT_KWS as _SPECIAL_STRICT_KWS,
)
from audit.checker_lib.nio.control_plan_shared import (
    display_values,
    value_matches,
)
from audit.checker_lib.nio.cross_file_extractors import (
    REFERENCE_IDENTITY_UNRESOLVED,
    build_struct_key,
    extract_rows_from_workbook,
    filter_pfd_process_rows,
    format_row_location,
    is_pfd_header_row,
    normalize_key,
    normalize_text,
    operation_number_key,
    part_numbers_compatible,
    prepend_row_location,
    read_part_number_keys,
)
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource
from audit.engine.checkers.control_plan_section_headers import (
    detail_section_header_merge,
    detail_table_bounds,
    has_business_text,
)
from audit.engine.rule_engine import RuleContext
from audit.kinds import PpapDocumentType

logger = logging.getLogger(__name__)

_SCL_FILE_NAME_KEY = "__file_name"
MULTIPLE_RELATED_FILES = "存在多份文档，无法判定主文档"
CP_NO_DETAIL_ROWS = "控制计划数据区没有任何明细行"
CP_STRAY_MARK = "表头有勾选符号，但旁边没有可识别的控制计划类型文字，无法确认勾选了哪一类"
_SCL_SHEET_KEYWORD = "特殊特性清单"
_SCL_VALID_CHAR_CLASS_KEYS = {
    normalize_key("产品"),
    normalize_key("过程"),
}

_CP_SELECTED_MARKS = frozenset(
    {
        "■",
        "☑",
        "✓",
        "√",
        "✔",
        "☒",
        "▄",
    }
)
_CP_UNSELECTED_MARKS = frozenset({"□", "☐", "▢"})

_CP_STAGE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prototype", ("样件", "prototype", "ots", "pp")),
    ("pre_launch", ("试生产", "pre-launch")),
    ("production", ("批量生产", "mass production", "量产", "生产", "production", "sop")),
)
_CP_HEADER_OPERATION_NO_KEYS = {normalize_key(value) for value in ["工序号", "工序编号", "零件/工序号", "零件/过程编号", "零件/过程号码", "Part / Process Number"]}
_CP_HEADER_OPERATION_NAME_KEYS = {normalize_key(value) for value in ["工序名称", "工序内容", "过程名称", "工序名称/操作描述", "过程名称/操作描述", "Process Name / Operation Description"]}
_PFMEA_RULE9_FIELD_ALIASES: Dict[str, List[str]] = {
    **_PFMEA_FIELD_ALIASES,
    "current_prevention_control": [
        "现行预防控制",
        "Current Prevention Control",
        "Current Prevention",
        "现行预防",
    ],
    "current_detection_control": [
        "现行探测控制",
        "Current Detection Control",
        "Current Detection",
        "现行探测",
    ],
    "legacy_prevention_control": [
        "预防控制",
        "预防措施",
        "控制预防",
        "Prevention Control",
        "Preventive Action",
    ],
    "legacy_detection_control": [
        "探测措施",
        "探测控制",
        "控制探测",
        "Detection Control",
        "Detection Action",
        "Detective Action",
    ],
}


class ControlPlanAgent(ExcelAgent):
    """Control Plan 专用审核代理。"""

    DOC_TYPE = PpapDocumentType.CONTROL_PLAN
    _stage_graphics_cache: dict[object, Any]
    _stage_graphics_evidence: AuditEvidence | None = None
    EXPECTED_RULE_CATEGORIES = (
        "文件类型勾选审核",
        "过程流程图与控制计划列项及一致性审核",
        "PFMEA与控制计划特殊特性一致性审核",
        "特殊特性分类与产品/过程列存在性审核",
        "特殊特性分类与控制方法关联审核",
        "频率或频次列完整性审核",
        "控制计划表头字段完整性及必填项审核",
        "流程一致性审核",
        "特性一致性审核",
        "措施一致性审核（PFMEA与CP）",
        "措施一致性审核（特殊特性清单与CP）",
    )

    def _read_stage_boxes(self, content: bytes, sheet: Any, stage_cells) -> Tuple[str, str] | None:
        """An XLS option row pairs boxes with labels by order, which survives any font or renderer."""
        from documents.excel.legacy_shapes import option_boxes
        from documents.excel.loader import ExcelLoader

        if not content.startswith(ExcelLoader.OLE_MAGIC):
            return None
        for row, column, _ in stage_cells:
            cell = sheet.cell(row, column)
            labels = str(cell.value or "").split()
            boxes = option_boxes(content, sheet.title, row) if len(labels) > 1 else None
            if not boxes or len(boxes) != len(labels) or any(box.filled is None for box in boxes):
                continue
            chosen = [label for label, box in zip(labels, boxes) if box.filled]
            production = any(self._classify_stage_text(label.casefold()) == "production" for label in chosen)
            self._stage_graphics_evidence = AuditEvidence(
                required="structure",
                used="structure",
                source_format="xls",
                prepared_format="xls",
                sources=(
                    EvidenceSource(
                        kind="structure",
                        locator=json.dumps({"kind": "xls_drawing", "sheet": sheet.title, "cell": cell.coordinate, "options": labels, "filled": chosen}, ensure_ascii=False, sort_keys=True),
                        excerpt=str(cell.value)[:500],
                    ),
                ),
            )
            return ("PASS", "原件生产阶段勾选已确认。") if production else ("NG", "原件生产/SOP阶段未勾选。")
        return None

    def _read_stage_graphics(self, sheet: Any, stage_cells) -> Tuple[str, str] | None:
        """Read the stage's labelled field through the shared Excel fact reader."""
        from documents.excel.visual import has_region_graphics, renderable_xlsx
        from openpyxl.utils.cell import get_column_letter

        from audit.engine.checkers.excel_field_evidence import observe_excel_region
        from audit.engine.file_bytes import load_file_bytes

        self._stage_graphics_evidence = None
        content, _ = load_file_bytes(str(self.audit_file.file_url))
        structural = self._read_stage_boxes(content, sheet, stage_cells)
        if structural is not None:
            return structural
        try:
            normalized = renderable_xlsx(content)
        except ValueError as error:
            raise ValueError(f"阶段字段区域读取失败（{getattr(error, 'code', type(error).__name__)}）。") from None
        if not normalized.startswith(b"PK"):
            return None
        source_suffix = Path(str(self.audit_file.file_url)).suffix.lower()
        source_format: Literal["xls", "xlsx", "xlsm"] = "xls" if source_suffix == ".xls" else "xlsm" if source_suffix == ".xlsm" else "xlsx"
        ranges = []
        for row, column, _ in stage_cells:
            merged = self._get_detailed_merge_info(sheet, row, column)
            ranges.append((merged.min_col, merged.min_row, merged.max_col, merged.max_row) if merged else (column, row, column, row))
        left = max(1, min(item[0] for item in ranges) - 1)
        cell_range = f"{get_column_letter(left)}{min(item[1] for item in ranges)}:{get_column_letter(max(item[2] for item in ranges))}{max(item[3] for item in ranges)}"
        if not has_region_graphics(normalized, sheet_name=sheet.title, cell_range=cell_range):
            return None
        cache = getattr(self, "_stage_graphics_cache", None)
        if cache is None:
            cache = self._stage_graphics_cache = {}
        sources: list[EvidenceSource] = []

        def witness(locator, excerpt, *, visual=False):
            sources.append(EvidenceSource(kind="visual" if visual else "structure", locator=json.dumps(locator, ensure_ascii=False, sort_keys=True), excerpt=str(excerpt)[:500]))

        try:
            facts = observe_excel_region(
                self.audit_file,
                sheet,
                cell_range,
                {
                    "production_stage_selected": (
                        "生产、批量生产或SOP选项对应的复选框实际被勾选或填充；样件/试生产勾选不算，选项文字存在不代表已勾选。完整区域看清且生产框为空返回false，看不清返回null。"
                    )
                },
                ("样件", "样品", "试生产", "生产", "SOP", "Prototype", "Production", "Pre-launch"),
                cache=cache,
                witness=witness,
            )
        except Exception as error:
            self._stage_graphics_evidence = AuditEvidence(
                required="visual",
                used="none",
                source_format=source_format,
                prepared_format="png",
                diagnostics=(EvidenceDiagnostic(code="control_plan.stage_read_error", stage="visual", severity="error", message="原件阶段图形读取失败。"),),
            )
            raise ValueError(f"阶段字段区域读取失败（{getattr(error, 'code', type(error).__name__)}）。") from None
        selected = facts["production_stage_selected"]
        self._stage_graphics_evidence = AuditEvidence(
            required="visual",
            used="visual" if sources else "none",
            source_format=source_format,
            prepared_format="png",
            conversion_strategies=("xlsx_text_located_region", "ocr_llm"),
            sources=tuple(sources),
            diagnostics=() if selected is not None else (EvidenceDiagnostic(code="control_plan.stage_unconfirmed", stage="visual", severity="warning", message="原件阶段勾选尚不能确认。"),),
        )
        # A converted XLS can shift text away from its fixed boxes, so a render alone cannot prove "unchecked".
        if selected is None or (selected is False and source_format == "xls"):
            return "UNABLE", "原件阶段区域中的生产/SOP勾选无法确认。"
        return ("PASS", "原件生产阶段勾选已确认。") if selected is True else ("NG", "原件生产/SOP阶段未勾选。")

    def _finalize_audit_result(self, result: AuditResultItem, *, rule_no: int, phase: Any, ctx: RuleContext | None = None) -> AuditResultItem:
        if rule_no == 1 and getattr(self, "_stage_graphics_evidence", None) is not None:
            result = result.model_copy(update={"evidence": self._stage_graphics_evidence})
        return super()._finalize_audit_result(result, rule_no=rule_no, phase=phase, ctx=ctx)

    @staticmethod
    def _workbook_from_ctx(ctx: RuleContext) -> Any:
        workbook = getattr(ctx, "_workbook", None)
        if workbook is None:
            raise RuntimeError("RuleContext中缺少workbook对象")
        return workbook

    @staticmethod
    def _get_detailed_merge_info(ws: Any, row: int, col: int) -> Any:
        merged_ranges = getattr(getattr(ws, "merged_cells", None), "ranges", [])
        for merged_range in merged_ranges:
            if merged_range.min_row <= row <= merged_range.max_row and merged_range.min_col <= col <= merged_range.max_col:
                return merged_range
        return None

    @staticmethod
    def _is_wide_merged_detail_section_row(
        ws: Any,
        row: int,
        *,
        table_left: int,
        table_right: int,
        required_col: int | None = None,
    ) -> bool:
        table_width = max(1, table_right - table_left + 1)
        min_width = max(4, (table_width * 3 + 3) // 4)
        for merged_range in getattr(getattr(ws, "merged_cells", None), "ranges", []):
            if not (merged_range.min_row <= row <= merged_range.max_row):
                continue
            merged_width = merged_range.max_col - merged_range.min_col + 1
            if merged_width < min_width:
                continue
            if merged_range.min_col > table_left + 1:
                continue
            if required_col is not None and not (merged_range.min_col <= required_col <= merged_range.max_col):
                continue
            if required_col is None and merged_range.max_col < table_right - 1:
                continue
            if not has_business_text(ws.cell(merged_range.min_row, merged_range.min_col).value):
                continue

            has_trailing_detail_text = False
            for col in range(merged_range.max_col + 1, table_right + 1):
                if has_business_text(ws.cell(row, col).value):
                    has_trailing_detail_text = True
                    break
            if has_trailing_detail_text:
                continue
            return True
        return False

    def _require_group_context(self) -> Tuple[bool, str]:
        if not self.group_context:
            return False, "缺少同次提交组上下文，无法执行跨文件规则"
        return True, ""

    def _require_single_related_file(self, doc_type: PpapDocumentType) -> Any:
        files = self.get_related_files(doc_type)
        if not files:
            return None, f"缺少关联文档: {doc_type.get_type_name()}"
        if len(files) > 1:
            return None, f"{doc_type.get_type_name()}{MULTIPLE_RELATED_FILES}"
        return files[0], ""

    @staticmethod
    def _related_error_status(error: str) -> str:
        """Several candidate reference files leave the main one unknown; that is not a missing file."""
        return "UNABLE" if MULTIPLE_RELATED_FILES in error else "NG"

    def _identity_check(self, left_keys: list[str], right_keys: list[str]) -> Tuple[str, str] | None:
        """Different part numbers fail; a missing one leaves an otherwise passing comparison unresolved."""
        if not left_keys or not right_keys:
            if self._shared_rule_context is not None:
                self._shared_rule_context.note_unresolved(REFERENCE_IDENTITY_UNRESOLVED)
            return None
        if not part_numbers_compatible(left_keys, right_keys):
            return "NG", "请上传同零件号的待审文件进行审核"
        return None

    def _extract_cp_rows(self, wb: Any) -> Any:
        return extract_rows_from_workbook(
            workbook=wb,
            field_aliases=_CP_FIELD_ALIASES,
            required_fields=("operation_no", "operation_name"),
            note_cell=self._shared_rule_context.note_cell if self._shared_rule_context else None,
        )

    def _extract_related_rows(self, doc_type: PpapDocumentType, aliases: Dict[str, List[str]], required_fields: Tuple[str, ...]) -> Any:
        related_file, error = self._require_single_related_file(doc_type)
        if related_file is None:
            return None, error
        workbook = self.load_related_workbook(related_file)
        ctx = self._shared_rule_context
        version = str((getattr(related_file, "file_metadata", None) or {}).get("file_version_id") or related_file.audit_id)
        rows = extract_rows_from_workbook(
            workbook,
            aliases,
            required_fields,
            note_cell=(lambda sheet, row, column: ctx.note_cell(sheet, row, column, file_version_id=version)) if ctx else None,
        )
        if not rows:
            return None, f"{doc_type.get_type_name()}无法识别必要列结构"
        return rows, ""

    def _find_special_characteristic_list_worksheet(self, workbook: Any) -> Any:
        if not hasattr(workbook, "sheetnames"):
            return None
        for sheet_name in workbook.sheetnames:
            if _SCL_SHEET_KEYWORD in str(sheet_name):
                return workbook[sheet_name]
        return None

    def _extract_rows_from_single_sheet(self, ws: Any, field_aliases: Dict[str, List[str]], required_fields: Tuple[str, ...] = ()) -> List[Dict[str, str]]:
        ctx = self._shared_rule_context
        return extract_rows_from_workbook(
            ws.parent,
            field_aliases,
            required_fields,
            note_cell=(lambda sheet, row, column: ctx.note_cell(sheet, row, column, file_version_id=ctx.version_for_worksheet(ws))) if ctx else None,
            sheets=(ws,),
        )

    def _extract_cross_rule_8_scl_rows_and_part_numbers(self) -> tuple[List[Dict[str, str]], List[str], bool]:
        scl_files = self.get_related_files(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        if not scl_files:
            return [], [], False

        rows: List[Dict[str, str]] = []
        all_part_keys: List[str] = []
        has_scl_sheet = False

        for related_file in scl_files:
            file_url = str(getattr(related_file, "file_url", "") or "")
            file_name = Path(file_url).name if file_url else "特殊特性清单"
            if not file_url:
                continue
            workbook = self.load_related_workbook(related_file)

            ws = self._find_special_characteristic_list_worksheet(workbook)
            if ws is None:
                continue
            has_scl_sheet = True

            all_part_keys.extend(self._extract_cross_rule_part_number_from_sheet(ws))

            extracted_rows = self._extract_rows_from_single_sheet(
                ws,
                field_aliases=_SCL_FIELD_ALIASES_V2,
                required_fields=("char_description",),
            )
            for row in extracted_rows:
                if not self._is_valid_scl_cross_row(row):
                    continue
                rows.append({**row, _SCL_FILE_NAME_KEY: file_name})

        dedup_part_keys: List[str] = []
        seen: set[str] = set()
        for part_key in all_part_keys:
            if part_key and part_key not in seen:
                seen.add(part_key)
                dedup_part_keys.append(part_key)

        return rows, dedup_part_keys, has_scl_sheet

    def _find_process_flow_chart_worksheet(self, workbook: Any) -> Any:
        if not hasattr(workbook, "sheetnames"):
            return None
        for sheet_name in workbook.sheetnames:
            if "过程流程图" in str(sheet_name):
                return workbook[sheet_name]
        for sheet_name in workbook.sheetnames:
            normalized = str(sheet_name).lower().replace(" ", "")
            if "pfd" in normalized or "pfc" in normalized:
                return workbook[sheet_name]
        for sheet_name in workbook.sheetnames:
            if "process flow" in str(sheet_name).lower():
                return workbook[sheet_name]
        return None

    def _find_control_plan_worksheet(self, workbook: Any) -> Any:
        if not hasattr(workbook, "sheetnames"):
            return None
        for sheet_name in workbook.sheetnames:
            if "控制计划" in str(sheet_name):
                return workbook[sheet_name]
        for sheet_name in workbook.sheetnames:
            normalized = str(sheet_name).lower().replace(" ", "")
            if "controlplan" in normalized:
                return workbook[sheet_name]
        for sheet_name in workbook.sheetnames:
            ws = workbook[sheet_name]
            for row in range(1, min(ws.max_row, 10) + 1):
                for col in range(1, min(ws.max_column, 10) + 1):
                    value = normalize_text(ws.cell(row=row, column=col).value)
                    if "控制计划" in value or "control plan" in value.lower():
                        return ws
        return None

    def _find_pfmea_worksheet(self, workbook: Any) -> Any:
        if not hasattr(workbook, "sheetnames"):
            return None
        for sheet_name in workbook.sheetnames:
            if str(sheet_name).strip().upper() == "PFMEA":
                return workbook[sheet_name]
        for sheet_name in workbook.sheetnames:
            normalized = str(sheet_name).lower().replace(" ", "")
            if "pfmea" in normalized or "过程fmea" in normalized:
                return workbook[sheet_name]
        return None

    def _extract_cross_rule_part_number_from_sheet(self, ws: Any) -> list[str]:
        ctx = self._shared_rule_context
        return read_part_number_keys(ws, (lambda sheet, row, column: ctx.note_cell(sheet, row, column, file_version_id=ctx.version_for_worksheet(ws))) if ctx else None)

    def _get_cross_rule_9_pfmea_sheet_and_rows(self) -> tuple[Any, list[dict[str, str]] | None, str]:
        related_file, error = self._require_single_related_file(PpapDocumentType.PFMEA)
        if related_file is None:
            return None, None, error if MULTIPLE_RELATED_FILES in error else "缺少PFMEA，请补充"

        file_url = str(getattr(related_file, "file_url", "") or "")
        if not file_url:
            return None, None, "缺少PFMEA，请补充"

        pfmea_workbook = self.load_related_workbook(related_file)

        pfmea_ws = self._find_pfmea_worksheet(pfmea_workbook)
        if pfmea_ws is None:
            return None, None, "缺少PFMEA，请补充"

        ctx = self._shared_rule_context
        pfmea_rows = extract_rows_from_workbook(
            workbook=pfmea_workbook,
            field_aliases=_PFMEA_RULE9_FIELD_ALIASES,
            required_fields=("process_step",),
            note_cell=(lambda sheet, row, column: ctx.note_cell(sheet, row, column, file_version_id=ctx.version_for_worksheet(pfmea_ws))) if ctx else None,
        )
        if not pfmea_rows:
            return pfmea_ws, None, "PFMEA无法识别必要列结构"
        return pfmea_ws, pfmea_rows, ""

    def _get_cross_rule_7_pfd_sheet_and_rows(self) -> tuple[Any, list[dict[str, str]] | None, str]:
        related_file, error = self._require_single_related_file(PpapDocumentType.PROCESS_FLOW_CHART)
        if related_file is None:
            return None, None, error if MULTIPLE_RELATED_FILES in error else "缺少过程流程图，请补充"

        file_url = str(getattr(related_file, "file_url", "") or "")
        if not file_url:
            return None, None, "缺少过程流程图，请补充"

        pfd_workbook = self.load_related_workbook(related_file)

        pfd_ws = self._find_process_flow_chart_worksheet(pfd_workbook)
        if pfd_ws is None:
            return None, None, "缺少过程流程图，请补充"

        ctx = self._shared_rule_context
        pfd_rows = extract_rows_from_workbook(
            workbook=pfd_workbook,
            field_aliases=_PFD_FIELD_ALIASES,
            required_fields=("operation_no", "operation_name"),
            note_cell=(lambda sheet, row, column: ctx.note_cell(sheet, row, column, file_version_id=ctx.version_for_worksheet(pfd_ws))) if ctx else None,
        )
        return pfd_ws, pfd_rows, ""

    def _is_cp_operation_header_row(self, row: Dict[str, str]) -> bool:
        operation_no = normalize_key(row.get("operation_no", ""))
        operation_name = normalize_key(row.get("operation_name", ""))
        return operation_no in _CP_HEADER_OPERATION_NO_KEYS or operation_name in _CP_HEADER_OPERATION_NAME_KEYS

    def _build_cross_rule_7_pfd_operations(self, rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
        operations: Dict[str, Dict[str, str]] = {}
        for row in rows:
            if is_pfd_header_row(row):
                continue
            operation_no = normalize_text(row.get("operation_no", ""))
            operation_name = normalize_text(row.get("operation_name", ""))
            if not operation_no and not operation_name:
                continue
            display = normalize_text(f"{operation_no} {operation_name}".strip())
            key = normalize_key(display)
            if not key:
                continue
            operations.setdefault(
                key,
                {
                    "display": display,
                    "operation_no": operation_no,
                    "row_number": normalize_text(row.get("__row_number", "")),
                    "sheet_name": normalize_text(row.get("__sheet_name", "")),
                },
            )
        return operations

    def _build_cross_rule_7_cp_operations(self, rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
        operations: Dict[str, Dict[str, str]] = {}
        for row in rows:
            if self._is_cp_operation_header_row(row):
                continue
            operation_no = normalize_text(row.get("operation_no", ""))
            operation_name = normalize_text(row.get("operation_name", ""))
            if not operation_no and not operation_name:
                continue
            display = normalize_text(f"{operation_no} {operation_name}".strip())
            key = normalize_key(display)
            if not key:
                continue
            operations.setdefault(
                key,
                {
                    "display": display,
                    "operation_no": operation_no,
                },
            )
        return operations

    def _is_valid_scl_cross_row(self, row: Dict[str, str]) -> bool:
        char_class_key = normalize_key(row.get("char_class", ""))
        if char_class_key not in _SCL_VALID_CHAR_CLASS_KEYS:
            return False
        char_desc = normalize_text(row.get("char_description", ""))
        char_val = normalize_text(row.get("char_value", ""))
        return bool(char_desc or char_val)

    def _build_cross_rule_8_scl_entries(self, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for row in rows:
            char_desc = normalize_text(row.get("char_description", ""))
            char_val = normalize_text(row.get("char_value", ""))
            char_class = normalize_text(row.get("char_class", ""))
            special_type = normalize_text(row.get("special_type", ""))
            display = f"【{char_class}】【{special_type}】{char_desc}：{char_val}"
            key = normalize_key(display)
            file_name = normalize_text(row.get(_SCL_FILE_NAME_KEY, "")) or "特殊特性清单"
            dedupe_key = (file_name, key)
            if not key or dedupe_key in seen:
                continue
            entries.append(
                {
                    "key": key,
                    "display": display,
                    "file_name": file_name,
                    "row_number": normalize_text(row.get("__row_number", "")),
                    "char_class": char_class,
                    "char_description": char_desc,
                }
            )
            seen.add(dedupe_key)
        return entries

    def _format_cross_rule_8_missing_entries(self, entries: List[Dict[str, str]]) -> str:
        grouped: Dict[str, List[str]] = {}
        for entry in entries:
            file_name = entry.get("file_name", "") or "特殊特性清单"
            row_number = entry.get("row_number", "")
            if row_number:
                grouped.setdefault(file_name, [])
                if row_number not in grouped[file_name]:
                    grouped[file_name].append(row_number)

        messages: List[str] = []
        for file_name, row_numbers in grouped.items():
            sample = "、".join(f"行{row}" for row in row_numbers[:5])
            suffix = f"...等共{len(row_numbers)}行" if len(row_numbers) > 5 else ""
            messages.append(f"特殊特性清单“{file_name}”中存在特性未在控制计划中体现，例如：{sample}{suffix}")

        if messages:
            return "；".join(messages)
        return f"特殊特性清单项目未在控制计划覆盖: {display_values([entry.get('display', '') for entry in entries])}"

    def _format_cross_rule_10_unmatched_scl_entries(self, entries: List[Dict[str, str]]) -> str:
        grouped: Dict[str, List[str]] = {}
        for entry in entries:
            file_name = entry.get("file_name", "") or "特殊特性清单"
            row_number = entry.get("row_number", "")
            if row_number:
                grouped.setdefault(file_name, [])
                if row_number not in grouped[file_name]:
                    grouped[file_name].append(row_number)

        messages: List[str] = []
        for file_name, row_numbers in grouped.items():
            sample = "、".join(f"行{row}" for row in row_numbers[:5])
            suffix = f"...等共{len(row_numbers)}行" if len(row_numbers) > 5 else ""
            messages.append(f"特殊特性清单“{file_name}”中存在部分特性未在控制计划中体现，因而部分数据行无法判断，例如：{sample}{suffix}")

        if messages:
            return "；".join(messages)
        return "特殊特性清单中存在部分特性未在控制计划中体现，因而部分数据行无法判断"

    def _format_cross_rule_10_missing_matches(self, items: List[str]) -> str:
        return f"控制计划中的项目在特殊特性清单中未找到对应项: {display_values(items)}"

    def _check_pfd_cp_consistency(
        self,
        wb: Any,
        *,
        ng_msg: str,
        pass_msg: str,
    ) -> Tuple[str, str]:
        """Shared logic for cross-rules 2a and 7: PFD↔CP 工序一致性."""
        ok, message = self._require_group_context()
        if not ok:
            return "NG", message

        cp_rows = self._extract_cp_rows(wb)
        if not cp_rows:
            return "NG", "控制计划无法识别工序列结构"

        pfd_rows, error = self._extract_related_rows(
            doc_type=PpapDocumentType.PROCESS_FLOW_CHART,
            aliases=_PFD_FIELD_ALIASES,
            required_fields=("operation_no", "operation_name"),
        )
        if pfd_rows is None:
            return "NG", error
        pfd_rows = filter_pfd_process_rows(pfd_rows)
        if not pfd_rows:
            return "NG", "PFD 未提取到有效工序数据"

        cp_operations = {(operation_number_key(row.get("operation_no", "")), normalize_key(row.get("operation_name", ""))) for row in cp_rows if row.get("operation_no") or row.get("operation_name")}
        if not cp_operations:
            return "NG", "控制计划未提取到有效工序数据"
        cp_rows_by_operation_no: Dict[str, List[Dict[str, str]]] = {}
        for row in cp_rows:
            opno_key = operation_number_key(row.get("operation_no", ""))
            if not opno_key:
                continue
            cp_rows_by_operation_no.setdefault(opno_key, []).append(row)

        missing_issues: List[str] = []
        for row in pfd_rows:
            operation_no = normalize_text(row.get("operation_no", ""))
            operation_name = normalize_text(row.get("operation_name", ""))
            key = (operation_number_key(operation_no), normalize_key(operation_name))
            if not any(key):
                continue
            if key in cp_operations:
                continue
            operation_no_key = operation_number_key(operation_no)
            same_operation_no_rows = cp_rows_by_operation_no.get(operation_no_key, [])
            if same_operation_no_rows and any(self._operation_name_matches(operation_name, cp_row.get("operation_name", "")) for cp_row in same_operation_no_rows):
                continue
            display = f"{operation_no} {operation_name}".strip()
            missing_issues.append(prepend_row_location(row, f"{ng_msg}: '{display}'"))
        if missing_issues:
            return "NG", "; ".join(missing_issues[:5]) + (f"...等共{len(missing_issues)}处" if len(missing_issues) > 5 else "")
        return "Pass", pass_msg

    @staticmethod
    def _name_token_counter(value: str) -> Counter[str]:
        tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", normalize_text(value))
        return Counter(normalize_key(token) for token in tokens if normalize_key(token))

    def _operation_name_matches(self, left: str, right: str) -> bool:
        if value_matches(left, right):
            return True
        left_tokens = self._name_token_counter(left)
        right_tokens = self._name_token_counter(right)
        if not left_tokens or not right_tokens:
            return False
        return left_tokens == right_tokens

    def _check_cross_rule_2a(self, wb: Any) -> Tuple[str, str]:
        """规则 2a: PFD↔CP 流程一致性检查"""
        return self._check_pfd_cp_consistency(
            wb,
            ng_msg="PFD 工序在控制计划中未找到对应",
            pass_msg="PFD 工序号+工序名称与控制计划一一对应",
        )

    def _check_cross_rule_2b(self, wb: Any) -> Tuple[str, str]:
        """规则 2b: PFMEA↔CP 特殊特性一致性检查
        当 PFMEA '特殊特性'有内容时，在 CP 中按 过程步骤↔工序号+工序名称 定位同行，
        校验 CP 对应行的'特殊特性分类'内容与 PFMEA '特殊特性'是否一致。
        """
        ok, message = self._require_group_context()
        if not ok:
            return "NG", message

        cp_rows = self._extract_cp_rows(wb)
        if not cp_rows:
            return "NG", "控制计划无法识别必要列结构"

        # 使用窄别名提取 PFMEA，避免 '特性' 匹配到 '产品特性'/'过程特性' 描述列
        _r2b_pfmea_aliases = {
            **_PFMEA_FIELD_ALIASES,
            "special_char": [
                "特殊特性",
                "Special Characteristic",
            ],
        }
        pfmea_rows, error = self._extract_related_rows(
            doc_type=PpapDocumentType.PFMEA,
            aliases=_r2b_pfmea_aliases,
            required_fields=("process_step",),
        )
        if pfmea_rows is None:
            return self._related_error_status(error), error

        # 检查 PFMEA 是否识别到了 special_char 列
        if pfmea_rows and "special_char" not in pfmea_rows[0]:
            return "NG", "PFMEA文件中未找到\u201c特殊特性\u201d列，请检查文件是否与模板一致"

        # 过滤 PFMEA 模板表头行（不是真实工序数据）
        pfmea_rows = self._filter_pfmea_header_rows(pfmea_rows)

        # 构建 CP 按工序查找的索引: key=normalize(operation_no+operation_name)
        cp_by_operation: Dict[str, List[Dict[str, str]]] = {}
        # 同时维护按 operation_no 的索引，用于 PFMEA step 内嵌编号的回退匹配
        cp_by_opno: Dict[str, List[Dict[str, str]]] = {}
        for row in cp_rows:
            key = build_struct_key(row.get("operation_no", ""), row.get("operation_name", ""))
            if key:
                cp_by_operation.setdefault(key, []).append(row)
            opno = normalize_key(row.get("operation_no", ""))
            if opno:
                cp_by_opno.setdefault(opno, []).append(row)

        # 分类标记正则: 仅处理包含 SC/CC/KPC/KCC 或图形特殊特性标记的行。
        _SPECIAL_CHAR_RE = re.compile(r"\b(CC|SC|KPC|KCC)\b|[★☆◇◆●]", re.IGNORECASE)

        pfmea_has_special_rows = any(_SPECIAL_CHAR_RE.search(normalize_text(row.get("special_char", ""))) for row in pfmea_rows)
        cp_has_special_rows = any(_SPECIAL_CHAR_RE.search(normalize_text(row.get("special_characteristic", ""))) for row in cp_rows)
        if cp_has_special_rows and not pfmea_has_special_rows:
            return "NG", "PFMEA文件中未找到“特殊特性”列，请检查文件是否与模板一致"

        issues: List[str] = []
        for pfmea_row in pfmea_rows:
            pfmea_special = normalize_text(pfmea_row.get("special_char", ""))
            if not pfmea_special:
                continue
            # 仅处理含有分类标记 (SC/CC/KPC/KCC) 的行
            if not _SPECIAL_CHAR_RE.search(pfmea_special):
                continue
            # PFMEA 过程步骤 → 定位 CP 同名行
            pfmea_step = pfmea_row.get("process_step", "") or pfmea_row.get("operation_name", "")
            pfmea_step_key = normalize_key(pfmea_step)
            if not pfmea_step_key:
                continue
            # 在 CP 中查找匹配的工序行
            # 策略 1: 去掉 '|' 分隔符后做子串匹配（解决 build_struct_key 添加的 | ）
            pfmea_flat = pfmea_step_key.replace("|", "")
            matched_cp_rows = []
            for cp_key, cp_row_list in cp_by_operation.items():
                cp_flat = cp_key.replace("|", "")
                if pfmea_flat in cp_flat or cp_flat in pfmea_flat:
                    matched_cp_rows.extend(cp_row_list)
            # 策略 2: 从 PFMEA step 中提取工序号做精确匹配
            if not matched_cp_rows:
                m = re.match(r"([\d]+(?:\.[\d]+)?)", pfmea_step.strip())
                if m:
                    opno_key = normalize_key(m.group(1))
                    if opno_key in cp_by_opno:
                        matched_cp_rows.extend(cp_by_opno[opno_key])
            if not matched_cp_rows:
                issues.append(prepend_row_location(pfmea_row, f"PFMEA 工序'{pfmea_step}'在控制计划中未找到对应行"))
                continue
            # 校验特殊特性分类是否一致
            pfmea_special_key = normalize_key(pfmea_special)
            has_match = False
            for cp_row in matched_cp_rows:
                cp_special = normalize_text(cp_row.get("special_characteristic", ""))
                if cp_special and normalize_key(cp_special) == pfmea_special_key:
                    has_match = True
                    break
                # 模糊匹配：包含关系
                if cp_special and (pfmea_special_key in normalize_key(cp_special) or normalize_key(cp_special) in pfmea_special_key):
                    has_match = True
                    break
            if not has_match:
                cp_specials = [normalize_text(r.get("special_characteristic", "")) for r in matched_cp_rows if r.get("special_characteristic")]
                cp_locations = [format_row_location(r) for r in matched_cp_rows if format_row_location(r)]
                issues.append(
                    prepend_row_location(
                        pfmea_row,
                        f"PFMEA 工序'{pfmea_step}'特殊特性'{pfmea_special}'与 CP 不一致(CP值: {', '.join(cp_specials[:3]) if cp_specials else '空'}; CP位置: {display_values(cp_locations)})",
                    )
                )

        if issues:
            return "NG", "; ".join(issues[:5]) + (f"...等共{len(issues)}处" if len(issues) > 5 else "")
        return "Pass", "PFMEA 与控制计划特殊特性一致"

    # ---- PFMEA 模板表头行 过滤 ----
    _PFMEA_HEADER_PATTERNS: tuple[str, ...] = (
        "processitem",
        "processstep",
        "processname",
        "systemsubsystem",
        "partelementof",
        "nameofprocess",
        "functionoftheprocess",
        "functionofprocessstep",
        "系统子系统零件要素或过程名称",
        "过程步骤的功能",
        "过程项目的功能",
        "过程功能要求",
    )

    @classmethod
    def _filter_pfmea_header_rows(cls, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """移除从 PFMEA 表格中提取到的模板表头行。

        某些 NIO/英文格式的 PFMEA，列标题（如 '1. Process Item',
        'System Subsystem, Part Element of Name of Process'）
        会被 extractor 当作数据行返回。通过 normalize_key 检测并排除。
        """
        filtered: List[Dict[str, str]] = []
        for row in rows:
            step = normalize_key(row.get("process_step", ""))
            if not step:
                filtered.append(row)
                continue
            if any(pat in step for pat in cls._PFMEA_HEADER_PATTERNS):
                logger.debug("过滤 PFMEA 模板表头行: %s", row.get("process_step", ""))
                continue
            filtered.append(row)
        return filtered

    def _check_cross_rule_7(self, wb: Any) -> Tuple[str, str]:
        """规则 7: PFD↔CP 流程一致性深度逐项校验"""
        ok, message = self._require_group_context()
        if not ok:
            return "NG", message

        pfd_ws, pfd_rows, pfd_error = self._get_cross_rule_7_pfd_sheet_and_rows()
        if pfd_error:
            return self._related_error_status(pfd_error), pfd_error

        cp_ws = self._find_control_plan_worksheet(wb)
        if cp_ws is None:
            return "NG", "缺少控制计划，请补充"

        pfd_part_keys = self._extract_cross_rule_part_number_from_sheet(pfd_ws)
        cp_part_keys = self._extract_cross_rule_part_number_from_sheet(cp_ws)
        identity_failure = self._identity_check(pfd_part_keys, cp_part_keys)
        if identity_failure is not None:
            return identity_failure

        cp_rows = self._extract_cp_rows(wb)
        if not cp_rows:
            return "NG", "控制计划无法识别工序列结构"

        pfd_operations = self._build_cross_rule_7_pfd_operations(pfd_rows or [])
        if not pfd_operations:
            return "NG", "PFD 未提取到有效工序数据"

        cp_operations = self._build_cross_rule_7_cp_operations(cp_rows)
        if not cp_operations:
            return "NG", "控制计划未提取到有效工序数据"

        missing_issues: List[str] = []
        for pfd_key, pfd_info in pfd_operations.items():
            pfd_operation_no_key = operation_number_key(pfd_info.get("operation_no", ""))
            if any(
                (not pfd_operation_no_key or not cp_info.get("operation_no") or pfd_operation_no_key == operation_number_key(cp_info["operation_no"]))
                and (pfd_key == cp_key or pfd_key in cp_key or cp_key in pfd_key)
                for cp_key, cp_info in cp_operations.items()
            ):
                continue

            row_ref = {
                "__sheet_name": pfd_info.get("sheet_name", ""),
                "__row_number": pfd_info.get("row_number", ""),
            }
            missing_issues.append(prepend_row_location(row_ref, f"PFD 工序在控制计划中未体现: '{pfd_info.get('display', '')}'"))

        if missing_issues:
            suffix = f"...等共{len(missing_issues)}处" if len(missing_issues) > 5 else ""
            return "NG", "; ".join(missing_issues[:5]) + suffix
        return "Pass", "PFD 与控制计划工序逐项对应"

    def _check_cross_rule_8(self, wb: Any) -> Tuple[str, str]:
        """规则 8: CP↔特殊特性清单 特性一致性审核

        SCL 拼接格式: 【特性分类】【特殊特性种类】特性描述：特性值
        CP 拼接格式:
          - 产品不为空: 【产品】【特殊特性分类】产品：产品/工序规范/公差
          - 过程不为空: 【过程】【特殊特性分类】过程：产品/工序规范/公差
        """
        ok, message = self._require_group_context()
        if not ok:
            return "NG", message

        cp_ws = self._find_control_plan_worksheet(wb)
        if cp_ws is None:
            return "NG", "缺少控制计划，请补充"

        cp_rows = self._extract_cp_rows(wb)
        if not cp_rows:
            return "NG", "控制计划无法识别必要列结构"

        scl_files = self.get_related_files(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        if not scl_files:
            return "NG", f"缺少关联文档: {PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name()}"

        special_rows, scl_part_keys, has_scl_sheet = self._extract_cross_rule_8_scl_rows_and_part_numbers()
        if not has_scl_sheet:
            return "NG", "缺少特殊特性清单，请补充"
        if not special_rows:
            return "NG", "特殊特性清单未提取到有效特性项"

        cp_part_keys = self._extract_cross_rule_part_number_from_sheet(cp_ws)
        identity_failure = self._identity_check(cp_part_keys, scl_part_keys)
        if identity_failure is not None:
            return identity_failure

        scl_entries = self._build_cross_rule_8_scl_entries(special_rows)
        if not scl_entries:
            return "NG", "特殊特性清单未提取到有效特性项"

        # --- CP side: 按产品/过程维度拼接 ---
        cp_keys: set[str] = set()
        for row in cp_rows:
            special_char = normalize_text(row.get("special_characteristic", ""))
            if not special_char:
                continue
            product_val = normalize_text(row.get("product", ""))
            process_val = normalize_text(row.get("process", ""))
            spec_tol = normalize_text(row.get("spec_tolerance", ""))
            if product_val:
                key = normalize_key(f"【产品】【{special_char}】{product_val}：{spec_tol}")
                cp_keys.add(key)
            if process_val:
                key = normalize_key(f"【过程】【{special_char}】{process_val}：{spec_tol}")
                cp_keys.add(key)

        missing_entries = [entry for entry in scl_entries if entry["key"] not in cp_keys]
        if missing_entries:
            return ("NG", self._format_cross_rule_8_missing_entries(missing_entries))
        return "Pass", "特殊特性清单项目已在控制计划覆盖"

    def _check_cross_rule_9(self, wb: Any) -> Tuple[str, str]:
        """规则 9: PFMEA↔CP 措施一致性审核（按工序对应）

        PFMEA 过程步骤 → CP 工序名称/操作描述 定位同行，
        比对 PFMEA 的 现行预防控制/现行探测控制/预防控制/探测措施
        是否在 CP 对应行的 评价测量技术 中体现。
        """
        ok, message = self._require_group_context()
        if not ok:
            return "NG", message

        pfmea_ws, pfmea_rows, pfmea_error = self._get_cross_rule_9_pfmea_sheet_and_rows()
        if pfmea_error:
            return self._related_error_status(pfmea_error), pfmea_error

        cp_ws = self._find_control_plan_worksheet(wb)
        if cp_ws is None:
            return "NG", "缺少控制计划，请补充"

        pfmea_part_keys = self._extract_cross_rule_part_number_from_sheet(pfmea_ws)
        cp_part_keys = self._extract_cross_rule_part_number_from_sheet(cp_ws)
        identity_failure = self._identity_check(pfmea_part_keys, cp_part_keys)
        if identity_failure is not None:
            return identity_failure

        cp_rows = self._extract_cp_rows(wb)
        if not cp_rows:
            return "NG", "控制计划无法识别必要列结构"

        cp_by_operation: Dict[str, List[Dict[str, str]]] = {}
        cp_by_opno: Dict[str, List[Dict[str, str]]] = {}
        for row in cp_rows:
            op_name = normalize_key(row.get("operation_name", ""))
            if op_name:
                cp_by_operation.setdefault(op_name, []).append(row)
            op_no = normalize_key(row.get("operation_no", ""))
            if op_no:
                cp_by_opno.setdefault(op_no, []).append(row)

        issues: List[str] = []
        pfmea_measure_fields = (
            ("current_prevention_control", "现行预防控制"),
            ("current_detection_control", "现行探测控制"),
            ("legacy_prevention_control", "预防控制"),
            ("legacy_detection_control", "探测措施"),
        )
        for pfmea_row in pfmea_rows or []:
            step = normalize_text(pfmea_row.get("process_step", ""))
            if not step:
                continue
            step_key = normalize_key(step)
            # 在 CP 中按工序名称匹配
            matched_cp_rows: List[Dict[str, str]] = []
            for cp_key, cp_row_list in cp_by_operation.items():
                if step_key in cp_key or cp_key in step_key:
                    matched_cp_rows.extend(cp_row_list)
            if not matched_cp_rows:
                opno_match = re.match(r"([\d]+(?:[.\-][\d]+)?)", step.strip())
                if opno_match:
                    opno_key = normalize_key(opno_match.group(1))
                    for cp_opno, cp_row_list in cp_by_opno.items():
                        if opno_key == cp_opno or opno_key in cp_opno or cp_opno in opno_key:
                            matched_cp_rows.extend(cp_row_list)
            if not matched_cp_rows:
                continue  # 无对应工序行，跳过

            # 收集 CP 对应行的评价测量技术
            cp_techniques = []
            cp_locations = []
            for cp_row in matched_cp_rows:
                tech = normalize_text(cp_row.get("measurement_technique", ""))
                if tech:
                    cp_techniques.append(tech)
                location = format_row_location(cp_row)
                if location:
                    cp_locations.append(location)

            # 比对 PFMEA 措施是否在 CP 评价测量技术中体现
            for field, field_label in pfmea_measure_fields:
                measure = normalize_text(pfmea_row.get(field, ""))
                if not measure:
                    continue
                if not any(value_matches(measure, tech) for tech in cp_techniques):
                    issues.append(
                        prepend_row_location(
                            pfmea_row,
                            f"工序'{step}' PFMEA {field_label}'{measure}'未在CP评价测量技术体现(CP位置: {display_values(cp_locations)})",
                        )
                    )

        if issues:
            return "NG", "; ".join(issues[:5]) + (f"...等共{len(issues)}处" if len(issues) > 5 else "")
        return "Pass", "PFMEA 预防/探测措施已在控制计划对应工序体现"

    def _check_cross_rule_10(self, wb: Any) -> Tuple[str, str]:
        """规则 10: CP↔特殊特性清单 控制方法/样本数/频次一致性校验

        按 产品/过程 维度匹配：
        - CP 产品非空 → SCL 特性分类=产品 且 特性描述=CP 产品 的行
        - CP 过程非空 → SCL 特性分类=过程 且 特性描述=CP 过程 的行
        比对: 评价测量技术 vs 控制方法, 数量 vs 样本数, 频率 vs 频次
        """
        ok, message = self._require_group_context()
        if not ok:
            return "NG", message

        cp_ws = self._find_control_plan_worksheet(wb)
        if cp_ws is None:
            return "NG", "缺少控制计划，请补充"

        cp_rows = self._extract_cp_rows(wb)
        if not cp_rows:
            return "NG", "控制计划无法识别必要列结构"

        scl_files = self.get_related_files(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        if not scl_files:
            return "NG", f"缺少关联文档: {PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name()}"

        special_rows, scl_part_keys, has_scl_sheet = self._extract_cross_rule_8_scl_rows_and_part_numbers()
        if not has_scl_sheet:
            return "NG", "缺少特殊特性清单，请补充"
        if not special_rows:
            return "NG", "特殊特性清单未提取到可比对项目"

        cp_part_keys = self._extract_cross_rule_part_number_from_sheet(cp_ws)
        identity_failure = self._identity_check(cp_part_keys, scl_part_keys)
        if identity_failure is not None:
            return identity_failure

        # SCL 按 (特性分类, 特性描述) 建索引
        scl_index: Dict[str, Dict[str, str]] = {}
        for row in special_rows:
            char_class = normalize_key(row.get("char_class", ""))
            char_desc = normalize_key(row.get("char_description", ""))
            if char_class and char_desc:
                composite_key = f"{char_class}|{char_desc}"
                scl_index[composite_key] = row

        cp_match_keys: set[str] = set()
        for row in cp_rows:
            special_char = normalize_text(row.get("special_characteristic", ""))
            if not special_char:
                continue
            product_val = normalize_text(row.get("product", ""))
            process_val = normalize_text(row.get("process", ""))
            if product_val:
                cp_match_keys.add(f"{normalize_key('产品')}|{normalize_key(product_val)}")
            if process_val:
                cp_match_keys.add(f"{normalize_key('过程')}|{normalize_key(process_val)}")

        unmatched_scl_entries: list[dict[str, str]] = []
        for row in special_rows:
            if not self._is_valid_scl_cross_row(row):
                continue
            char_class = normalize_key(row.get("char_class", ""))
            char_desc = normalize_key(row.get("char_description", ""))
            if not char_class or not char_desc:
                continue
            if f"{char_class}|{char_desc}" in cp_match_keys:
                continue
            unmatched_scl_entries.append(
                {
                    "file_name": normalize_text(row.get(_SCL_FILE_NAME_KEY, "")) or "特殊特性清单",
                    "row_number": normalize_text(row.get("__row_number", "")),
                }
            )
        if unmatched_scl_entries:
            return "NG", self._format_cross_rule_10_unmatched_scl_entries(unmatched_scl_entries)

        # 比对字段映射: (CP字段, SCL字段, 显示名)
        compare_map = [
            ("measurement_technique", "control_method", "评价测量技术vs控制方法"),
            ("sample_size", "sample_size", "数量vs样本数"),
            ("frequency", "frequency", "频率vs频次"),
        ]

        missing_matches: List[str] = []
        mismatch_items: List[str] = []
        for cp_row in cp_rows:
            special_char = normalize_text(cp_row.get("special_characteristic", ""))
            if not special_char:
                continue
            product_val = normalize_text(cp_row.get("product", ""))
            process_val = normalize_text(cp_row.get("process", ""))
            cp_compare_values = [
                normalize_text(cp_row.get("measurement_technique", "")),
                normalize_text(cp_row.get("sample_size", "")),
                normalize_text(cp_row.get("frequency", "")),
                normalize_text(cp_row.get("spec_tolerance", "")),
            ]
            if not product_val and not process_val:
                continue
            if not any(cp_compare_values):
                continue

            # 产品维度
            if product_val:
                product_key = f"{normalize_key('产品')}|{normalize_key(product_val)}"
                scl_row = scl_index.get(product_key)
                if scl_row is None:
                    missing_matches.append(f"产品'{product_val}'")
                else:
                    for cp_field, scl_field, label in compare_map:
                        cp_val = normalize_text(cp_row.get(cp_field, ""))
                        scl_val = normalize_text(scl_row.get(scl_field, ""))
                        if scl_val and cp_val and not value_matches(cp_val, scl_val):
                            mismatch_items.append(f"产品'{product_val}' {label}")

            # 过程维度
            if process_val:
                process_key = f"{normalize_key('过程')}|{normalize_key(process_val)}"
                scl_row = scl_index.get(process_key)
                if scl_row is None:
                    missing_matches.append(f"过程'{process_val}'")
                else:
                    for cp_field, scl_field, label in compare_map:
                        cp_val = normalize_text(cp_row.get(cp_field, ""))
                        scl_val = normalize_text(scl_row.get(scl_field, ""))
                        if scl_val and cp_val and not value_matches(cp_val, scl_val):
                            mismatch_items.append(f"过程'{process_val}' {label}")

        if missing_matches:
            return "NG", self._format_cross_rule_10_missing_matches(missing_matches)

        if mismatch_items:
            return "NG", f"控制方法/样本数/频次不一致: {display_values(mismatch_items)}"
        return "Pass", "控制计划与特殊特性清单在控制方法/样本数/频次一致"

    def _find_special_char_column(self, ws: Any) -> Tuple[Optional[int], Optional[int]]:
        """更精确地查找特殊特性分类列，避免误匹配。

        支持单行 / 多行拆分表头（如 Cetus "特殊" + "特性" + "分类" 跨行）、
        中英文混排（如 Sirius "Special Char. Class\\n特殊特性分类"）等场景。
        """
        scan_rows = min(_HEADER_SCAN_ROWS, ws.max_row)
        scan_cols = min(_HEADER_SCAN_COLS, ws.max_column)

        # ---------- Strategy 1: 单 cell 中文匹配 ----------
        for row in range(1, scan_rows + 1):
            for col in range(1, scan_cols + 1):
                val = ws.cell(row=row, column=col).value
                if not isinstance(val, str):
                    continue
                val_clean = val.replace("\n", "").replace("\r", "").replace(" ", "").strip()
                for kw in _SPECIAL_STRICT_KWS:
                    if kw in val_clean:
                        return row, col

        # ---------- Strategy 2: 单 cell 英文组合匹配 ----------
        for row in range(1, scan_rows + 1):
            for col in range(1, scan_cols + 1):
                val = ws.cell(row=row, column=col).value
                if not isinstance(val, str):
                    continue
                val_lower = re.sub(r"\s+", " ", val.strip()).lower()
                if "special" in val_lower and ("char" in val_lower or "character" in val_lower or "characteristic" in val_lower) and "class" in val_lower:
                    return row, col

        # ---------- Strategy 3: 纵向拼接同列相邻 cell 匹配 ----------
        for col in range(1, scan_cols + 1):
            concat = ""
            last_row = 0
            for row in range(1, scan_rows + 1):
                val = ws.cell(row=row, column=col).value
                if isinstance(val, str) and val.strip():
                    concat += val.replace("\n", "").replace("\r", "").replace(" ", "")
                    last_row = row
            for kw in _SPECIAL_STRICT_KWS:
                if kw in concat:
                    return last_row, col

        return None, None

    # -------- 通用列查找工具 --------
    def _find_column_by_keywords_multi(
        self,
        ws: Any,
        cn_keywords: list[str],
        en_keywords: list[str] | None = None,
        *,
        exclude_cols: set[int] | None = None,
        use_vertical_concat: bool = True,
    ) -> Tuple[Optional[int], Optional[int]]:
        """在表头区域查找包含任一关键词的列。

        Parameters
        ----------
        cn_keywords : list[str]
            中文关键词，按优先级排列。
        en_keywords : list[str] | None
            英文关键词（可选）。
        exclude_cols : set[int] | None
            排除的列号集合（避免和已匹配列重复）。
        use_vertical_concat : bool
            是否启用纵向拼接策略。

        Returns
        -------
        (row, col) 或 (None, None)
        """
        scan_rows = min(_HEADER_SCAN_ROWS, ws.max_row)
        scan_cols = min(_HEADER_SCAN_COLS, ws.max_column)
        exclude = exclude_cols or set()

        # Pass 0: 精确匹配（cell 文本去空白后 == 关键词）
        all_kws_p0 = cn_keywords + (en_keywords or [])
        for row in range(1, scan_rows + 1):
            for col in range(1, scan_cols + 1):
                if col in exclude:
                    continue
                val = ws.cell(row=row, column=col).value
                if not isinstance(val, str):
                    continue
                val_clean = val.replace("\n", "").replace("\r", "").replace(" ", "").strip()
                for kw in all_kws_p0:
                    if val_clean == kw or val_clean.lower() == kw.lower():
                        return row, col

        # Pass 1: 逐 cell 中文子串匹配
        for row in range(1, scan_rows + 1):
            for col in range(1, scan_cols + 1):
                if col in exclude:
                    continue
                val = ws.cell(row=row, column=col).value
                if not isinstance(val, str):
                    continue
                val_clean = val.replace("\n", "").replace("\r", "").replace(" ", "").strip()
                for kw in cn_keywords:
                    if kw in val_clean:
                        return row, col

        # Pass 2: 逐 cell 英文子串匹配
        if en_keywords:
            for row in range(1, scan_rows + 1):
                for col in range(1, scan_cols + 1):
                    if col in exclude:
                        continue
                    val = ws.cell(row=row, column=col).value
                    if not isinstance(val, str):
                        continue
                    val_lower = val.replace("\n", "").replace("\r", "").replace(" ", "").strip().lower()
                    for kw in en_keywords:
                        if kw.lower() in val_lower:
                            return row, col

        # Pass 3: 纵向拼接
        if use_vertical_concat:
            for col in range(1, scan_cols + 1):
                if col in exclude:
                    continue
                concat = ""
                last_row = 0
                for row in range(1, scan_rows + 1):
                    val = ws.cell(row=row, column=col).value
                    if isinstance(val, str) and val.strip():
                        concat += val.replace("\n", "").replace("\r", "").replace(" ", "")
                        last_row = row
                all_kws = cn_keywords + (en_keywords or [])
                for kw in all_kws:
                    if kw.lower() in concat.lower():
                        return last_row, col

        return None, None

    def _find_column_near_anchor_by_keywords(
        self,
        ws: Any,
        *,
        anchor_row: int,
        anchor_col: int,
        cn_keywords: list[str],
        en_keywords: list[str] | None = None,
        exclude_cols: set[int] | None = None,
        row_before: int = 1,
        row_after: int = 4,
        col_before: int = 1,
        col_after: int = 8,
    ) -> Tuple[Optional[int], Optional[int]]:
        """在锚点附近搜索列，避免误命中前置元数据区表头。"""
        exclude = exclude_cols or set()
        row_start = max(1, anchor_row - row_before)
        row_end = min(ws.max_row, anchor_row + row_after)
        col_start = max(1, anchor_col - col_before)
        col_end = min(ws.max_column, anchor_col + col_after)

        def _clean(v: Any) -> str:
            if not isinstance(v, str):
                return ""
            return v.replace("\n", "").replace("\r", "").replace(" ", "").strip()

        all_kws = cn_keywords + (en_keywords or [])

        # Pass 0: 精确匹配
        for row in range(row_start, row_end + 1):
            for col in range(col_start, col_end + 1):
                if col in exclude:
                    continue
                val_clean = _clean(ws.cell(row=row, column=col).value)
                if not val_clean:
                    continue
                for kw in all_kws:
                    if val_clean == kw or val_clean.lower() == kw.lower():
                        return row, col

        # Pass 1: 子串匹配
        for row in range(row_start, row_end + 1):
            for col in range(col_start, col_end + 1):
                if col in exclude:
                    continue
                val_clean = _clean(ws.cell(row=row, column=col).value)
                if not val_clean:
                    continue
                for kw in cn_keywords:
                    if kw in val_clean:
                        return row, col
                if en_keywords:
                    val_lower = val_clean.lower()
                    for kw in en_keywords:
                        if kw.lower() in val_lower:
                            return row, col
        return None, None

    @staticmethod
    def _normalize_header_text(value: Any) -> str:
        return str(value).replace("\n", "").replace("\r", "").replace(" ", "").lower().strip()

    @staticmethod
    def _is_empty_header_value(value: str) -> bool:
        return value in {"none", ""}

    def _read_right_header_value(self, ws: Any, row: int, col: int) -> str:
        merged = self._get_detailed_merge_info(ws, row, col)
        value_row = merged.min_row if merged else row
        value_col = (merged.max_col + 1) if merged else (col + 1)
        value = ws.cell(value_row, value_col).value
        return self._normalize_header_text(value)

    def _extract_inline_header_value(
        self,
        ws: Any,
        row: int,
        col: int,
        prefixes: List[str],
        *,
        require_all: bool = False,
    ) -> Optional[str]:
        raw_value = self._normalize_header_text(ws.cell(row=row, column=col).value)
        if require_all:
            if not all(prefix in raw_value for prefix in prefixes):
                return None
        elif not any(prefix in raw_value for prefix in prefixes):
            return None

        cleaned = raw_value
        for prefix in prefixes:
            cleaned = cleaned.replace(prefix, "")
        return cleaned.replace("：", "").replace(":", "").strip()

    @staticmethod
    def _stage_alias_patterns() -> List[Tuple[str, str]]:
        patterns: List[Tuple[str, str]] = []
        for stage, aliases in _CP_STAGE_ALIASES:
            for alias in aliases:
                patterns.append((stage, alias.replace(" ", "").lower()))
        patterns.sort(key=lambda item: len(item[1]), reverse=True)
        return patterns

    @classmethod
    def _extract_stage_occurrences(cls, text: str) -> List[Tuple[int, int, str]]:
        normalized = cls._normalize_header_text(text)
        if not normalized:
            return []

        spans: List[Tuple[int, int, str]] = []
        for stage, alias in cls._stage_alias_patterns():
            start = normalized.find(alias)
            while start != -1:
                end = start + len(alias)
                overlaps = any(not (end <= span_start or start >= span_end) for span_start, span_end, _ in spans)
                if not overlaps:
                    spans.append((start, end, stage))
                start = normalized.find(alias, start + 1)

        spans.sort(key=lambda item: (item[0], item[1]))
        return spans

    @classmethod
    def _classify_stage_text(cls, text: str) -> Optional[str]:
        if not text:
            return None

        stages: List[str] = []
        for _, _, stage in cls._extract_stage_occurrences(text):
            if stage not in stages:
                stages.append(stage)

        if not stages:
            return None
        if "production" in stages:
            return "production"
        if "pre_launch" in stages:
            return "pre_launch"
        return stages[0]

    @classmethod
    def _extract_selected_stages_from_cell(cls, text: str) -> List[str]:
        normalized = cls._normalize_header_text(text)
        if not normalized:
            return []

        mark_positions = [idx for idx, char in enumerate(normalized) if char in _CP_SELECTED_MARKS]
        if not mark_positions:
            return []

        stages = cls._extract_stage_occurrences(normalized)
        if not stages:
            return []

        # A complete checkbox list fixes the label/mark order. Looking only
        # at selected marks binds a prefix checkbox to the preceding label.
        boxes = [(index, char in _CP_SELECTED_MARKS) for index, char in enumerate(normalized) if char in _CP_SELECTED_MARKS or char in _CP_UNSELECTED_MARKS]
        if len(boxes) == len(stages):
            prefix = all((stages[index - 1][1] if index else 0) <= position < start for index, ((position, _), (start, _, _)) in enumerate(zip(boxes, stages)))
            suffix = all(end <= position < (stages[index + 1][0] if index + 1 < len(stages) else len(normalized)) for index, ((position, _), (_, end, _)) in enumerate(zip(boxes, stages)))
            if prefix or suffix:
                return list(dict.fromkeys(stage for (_, checked), (_, _, stage) in zip(boxes, stages) if checked))

        selected: List[str] = []
        for mark_pos in mark_positions:
            selected_stage: Optional[str] = None
            for index, (start, _end, stage) in enumerate(stages):
                next_start = stages[index + 1][0] if index + 1 < len(stages) else len(normalized) + 1
                if start <= mark_pos < next_start:
                    selected_stage = stage
                    break

            if selected_stage is None:
                left_matches = [stage for start, end, stage in stages if end <= mark_pos]
                if left_matches:
                    selected_stage = left_matches[-1]
                else:
                    right_matches = [stage for start, _end, stage in stages if start > mark_pos]
                    if right_matches:
                        selected_stage = right_matches[0]

            if selected_stage and selected_stage not in selected:
                selected.append(selected_stage)
        return selected

    @rule("文件类型勾选审核")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 1: 文件适用阶段检查

        支持多种模板格式：
        - 标记符在同一 cell 中（■/☑/✓/√ 紧跟阶段文字）
        - 标记符在相邻 cell 中（如 Sirius: √ 在独立 cell）
        - 无标记时返回 NG（而非 ERR）
        """
        wb = self._workbook_from_ctx(ctx)
        stray_marks = False
        try:
            for sheet in wb.worksheets:
                max_r = min(_HEADER_SCAN_ROWS, sheet.max_row)
                max_c = min(_HEADER_SCAN_COLS, sheet.max_column)

                # 1) 收集 header 区域中出现标记符的 cell 坐标
                mark_cells = []  # [(row, col, cell_cleaned)]
                stage_cells = []  # [(row, col, cell_cleaned)]  — 含阶段关键词

                for row in range(1, max_r + 1):
                    for col in range(1, max_c + 1):
                        raw = sheet.cell(row=row, column=col).value
                        if not isinstance(raw, str):
                            continue
                        cleaned = raw.replace("\n", "").replace("\r", "").replace(" ", "").strip()
                        cleaned_lower = cleaned.lower()

                        has_mark = any(m in cleaned for m in _CP_SELECTED_MARKS)
                        has_stage = self._classify_stage_text(cleaned_lower) is not None

                        if has_mark:
                            mark_cells.append((row, col, cleaned_lower))
                        if has_stage:
                            stage_cells.append((row, col, cleaned_lower))

                if not mark_cells and not stage_cells:
                    continue  # 这个 sheet 没有相关信息，看下一个
                if not stage_cells:
                    # A tick with no stage text anywhere on the sheet does not say which type was chosen.
                    stray_marks = True
                    continue

                # 2) 汇总所有已识别的勾选阶段，统一判定，避免首个非生产阶段提前返回
                selected_stage_types: List[str] = []
                same_cell_selected: Dict[Tuple[int, int], List[str]] = {}
                for mr, mc, cl in mark_cells:
                    picked = self._extract_selected_stages_from_cell(cl)
                    same_cell_selected[(mr, mc)] = picked
                    for stage_type in picked:
                        if stage_type not in selected_stage_types:
                            selected_stage_types.append(stage_type)

                # 3) mark 在相邻 cell（同一行）时，关联最近的阶段标签；
                # 若左右同距离存在歧义，则同时纳入候选阶段。
                for mr, mc, _ in mark_cells:
                    if same_cell_selected.get((mr, mc)):
                        continue
                    nearby_stages = [(abs(sc - mc), scl) for sr, sc, scl in stage_cells if sr == mr and sc != mc and abs(sc - mc) <= 4]
                    if not nearby_stages:
                        continue
                    min_distance = min(distance for distance, _ in nearby_stages)
                    nearest_stage_labels = [scl for distance, scl in nearby_stages if distance == min_distance]
                    for stage_label in nearest_stage_labels:
                        classified_stage_type = self._classify_stage_text(stage_label)
                        if classified_stage_type and classified_stage_type not in selected_stage_types:
                            selected_stage_types.append(classified_stage_type)

                for row, col, _ in (*mark_cells, *stage_cells):
                    ctx.note_cell(sheet.title, row, col)
                ctx.record_execution()  # Evaluate the identified stage selection.

                # 4) 统一判定：命中生产即通过，否则有勾选但无生产则不通过
                if "production" in selected_stage_types:
                    return "Pass", "适用阶段勾选正确"
                if selected_stage_types:
                    return "NG", "勾选了非生产/SOP阶段"

                # 5) 有 stage 关键词但无 mark → 未勾选任何阶段
                if stage_cells:
                    graphic_result = self._read_stage_graphics(sheet, stage_cells)
                    if graphic_result is not None:
                        return graphic_result
                metadata = getattr(self.audit_file, "file_metadata", None)
                source_url = str(metadata.get("source_pdf_url", "") or "") if isinstance(metadata, dict) else ""
                file_url = str(getattr(self.audit_file, "file_url", "") or "")
                source_candidates = (file_url.lower(), source_url.lower())
                if any(candidate.endswith(".pdf") for candidate in source_candidates) and any(self._classify_stage_text(scl) == "production" for _sr, _sc, scl in stage_cells):
                    return "UNABLE", "尚未从PDF原件确认实际阶段勾选；生产阶段文字不能代替选择。"
                if mark_cells:
                    return "UNABLE", CP_STRAY_MARK
                return "NG", "未勾选任何控制计划类型"

            if stray_marks:
                return "UNABLE", CP_STRAY_MARK
            return "NG", "未检测到任何适用阶段信息"

        except Exception as exc:
            logger.exception("rule1 error")
            return "ERR", f"规则1检查异常: {exc}"

    # ------------------------------------------------------------------
    #  PDF 转换后重复页眉行检测
    # ------------------------------------------------------------------
    _CP_PAGE_HEADER_KWS: tuple[str, ...] = (
        "控制计划",
        "控制计划种类",
        "控制计划编号",
        "零件编号",
        "零件名称",
        "合作伙伴编码",
        "主要小组",
        "主要联系人",
        "客户工程批准",
        "客户质量批准",
        "其他合作伙伴批准",
        "其他批准",
    )

    def _is_cp_repeated_header_row(self, ws: Any, row: int) -> bool:
        """判断 row 是否为 PDF→Excel 转换后的重复页眉行。

        PDF 控制计划多页时，每页的标题块（控制计划、编号、零件信息、
        列标题等）被原样保留为 Excel 行。这些行不是数据行。

        检测策略：
        1. C1 单元格包含任一页眉关键词 → 是页眉行
        2. C1 包含 '零件／过程编号' 或 '零件/过程编号'（列标题行）
        3. 检查同行是否包含 '特殊特性分类'（列标题行）
        """
        c1_val = str(ws.cell(row=row, column=1).value or "").replace("\n", "").replace("\r", "").replace(" ", "").strip()
        # 策略 1: 页眉关键词
        for kw in self._CP_PAGE_HEADER_KWS:
            if kw in c1_val:
                return True
        # 策略 2: 列标题行
        if "零件" in c1_val and "过程" in c1_val and "编号" in c1_val:
            return True
        # 策略 3: 行内出现 '特殊特性分类' 或 '频率' 等列标题
        for c in range(2, min(ws.max_column + 1, 20)):
            cv = str(ws.cell(row=row, column=c).value or "").replace("\n", "").replace("\r", "").replace(" ", "")
            if "特殊特性分类" in cv or cv in ("容量", "频率", "编号"):
                return True
        return False

    @rule("过程流程图与控制计划列项及一致性审核")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        wb = self._workbook_from_ctx(ctx)
        return self._check_cross_rule_2a(wb)

    @rule("PFMEA与控制计划特殊特性一致性审核")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        wb = self._workbook_from_ctx(ctx)
        return self._check_cross_rule_2b(wb)

    @rule("特殊特性分类与产品/过程列存在性审核")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 3: 特殊特性分类、特性列列检查

        使用通用列查找，支持中文纯文本表头和中英文双语表头。
        遍历所有工作表直到找到匹配列。
        """
        wb = self._workbook_from_ctx(ctx)
        found = False
        detail_rows = 0
        for ws in wb.worksheets:
            feature_type_row, feature_type_col = self._find_special_char_column(ws)
            if feature_type_row is None or feature_type_col is None:
                continue  # 尝试下一个 sheet

            logger.debug(f"工作表{ws.title}特殊特性分类 {feature_type_row}, col {feature_type_col}")

            # 查找 "特性"/"Characteristic" 列（排除已匹配的特殊特性分类列）
            char_row, char_col = self._find_column_by_keywords_multi(
                ws,
                ["特性"],
                ["characteristic"],
                exclude_cols={feature_type_col},
            )
            if char_col is None:
                continue

            logger.debug(f"工作表{ws.title}特性 {char_row}, col {char_col}")

            # 查找 "产品" / "过程" 列（优先在 特性/特殊特性分类 附近查找，避免命中元数据区）
            anchor_row = max(feature_type_row or 1, char_row or 1)
            anchor_col = min(feature_type_col or 1, char_col or 1)
            product_row, product_col = self._find_column_near_anchor_by_keywords(
                ws,
                anchor_row=anchor_row,
                anchor_col=anchor_col,
                cn_keywords=["产品"],
                en_keywords=["product"],
                exclude_cols={feature_type_col},
            )
            process_row, process_col = self._find_column_near_anchor_by_keywords(
                ws,
                anchor_row=anchor_row,
                anchor_col=anchor_col,
                cn_keywords=["过程"],
                en_keywords=["process"],
                exclude_cols={feature_type_col, product_col} if product_col else {feature_type_col},
            )
            if product_col is None or process_col is None:
                logger.debug(f"工作表{ws.title}未找到产品或过程列")
                continue

            logger.debug(f"工作表{ws.title}产品 {product_row}, col {product_col}")
            logger.debug(f"工作表{ws.title}过程 {process_row}, col {process_col}")

            found = True
            # 确定数据起始行
            is_merged = self._get_detailed_merge_info(ws, feature_type_row, feature_type_col)
            data_start = max(is_merged.max_row if is_merged else feature_type_row, char_row or 0, product_row or 0, process_row or 0) + 1

            # 确定数据结束行
            data_end = ws.max_row
            for r in range(data_start, ws.max_row + 1):
                for c in range(1, ws.max_column + 1):
                    val = ws.cell(r, c).value
                    if isinstance(val, str) and "备注" in val.replace(" ", ""):
                        data_end = r - 1
                        break
                if data_end != ws.max_row:
                    break

            logger.debug(f"工作表{ws.title}数据范围: {data_start} - {data_end}")
            table_left, table_right = detail_table_bounds(
                ws,
                header_row=min(row for row in (feature_type_row, char_row, product_row, process_row) if row is not None),
                columns=(feature_type_col, char_col, product_col, process_col),
            )

            r = data_start
            while r <= data_end:
                # 跳过 PDF 转换后的重复页眉行
                if self._is_cp_repeated_header_row(ws, r):
                    r += 1
                    continue
                section_header = detail_section_header_merge(
                    ws,
                    r,
                    table_left=table_left,
                    table_right=table_right,
                )
                if section_header:
                    r = section_header.max_row + 1
                    continue

                is_merged_cell = self._get_detailed_merge_info(ws, r, feature_type_col)
                special_cell = ws.cell(is_merged_cell.min_row if is_merged_cell else r, feature_type_col)
                if special_cell.data_type == "e":
                    ctx.note_cell(ws.title, special_cell.row, special_cell.column)
                    return "UNABLE", f"行号{r} 特殊特性分类无法确认"
                val_special = special_cell.value
                # A real detail row evaluates the conditional requirement even
                # when its special-characteristic antecedent is false.
                if any(ws.cell(r, c).value is not None for c in {table_left, feature_type_col, product_col, process_col}):
                    detail_rows += 1
                    ctx.note_cell(ws.title, special_cell.row, special_cell.column)
                    ctx.record_execution()

                if val_special is not None and str(val_special).strip():
                    is_merged_product = self._get_detailed_merge_info(ws, r, product_col)
                    val_product = ws.cell(
                        is_merged_product.min_row if is_merged_product else r,
                        product_col,
                    ).value
                    is_merged_process = self._get_detailed_merge_info(ws, r, process_col)
                    val_process = ws.cell(
                        is_merged_process.min_row if is_merged_process else r,
                        process_col,
                    ).value

                    product_cell = ws.cell(is_merged_product.min_row if is_merged_product else r, product_col)
                    process_cell = ws.cell(is_merged_process.min_row if is_merged_process else r, process_col)
                    known = next((cell for cell in (product_cell, process_cell) if cell.data_type != "e" and cell.value is not None and str(cell.value).strip()), None)
                    if known is not None:
                        ctx.note_cell(ws.title, known.row, known.column)
                    elif any(cell.data_type == "e" for cell in (product_cell, process_cell)):
                        for cell in (product_cell, process_cell):
                            if cell.data_type == "e":
                                ctx.note_cell(ws.title, cell.row, cell.column)
                        return "UNABLE", f"行号{r} 产品、过程证据无法确认"
                    elif (val_product is None or str(val_product).strip() == "") and (val_process is None or str(val_process).strip() == ""):
                        return "NG", f"行号{r} 特殊特性分类对应行的产品、过程列均为空"

                # Every row of a merged 特殊特性分类 block is checked against its own 产品/过程.
                r += 1

        if not found:
            return "NG", "未找到'特殊特性分类'或'特性'列"
        if not detail_rows:
            return "NG", CP_NO_DETAIL_ROWS
        return "Pass", "特殊特性分类、特性列符合要求"

    @rule("特殊特性分类与控制方法关联审核")
    def rule_5(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 4: 特殊特性分类、控制方法列检查

        使用 _find_special_char_column + _find_column_by_keywords,
        支持灵活的中/英文表头匹配，遍历所有工作表。
        """
        wb = self._workbook_from_ctx(ctx)
        found = False
        detail_rows = 0
        for ws in wb.worksheets:
            feature_type_row, feature_type_col = self._find_special_char_column(ws)
            if feature_type_row is None or feature_type_col is None:
                continue

            ctrl_row, ctrl_col = self._find_column_by_keywords_multi(
                ws,
                ["控制方法"],
                ["controlmethod", "control method"],
                exclude_cols={feature_type_col},
            )
            if ctrl_col is None:
                logger.debug(f"工作表{ws.title}未找到控制方法列")
                continue

            logger.debug(f"工作表{ws.title}特殊特性分类 {feature_type_row}, col {feature_type_col}")
            logger.debug(f"工作表{ws.title}控制方法 {ctrl_row}, col {ctrl_col}")

            found = True
            is_merged = self._get_detailed_merge_info(ws, feature_type_row, feature_type_col)
            data_start = max(is_merged.max_row if is_merged else feature_type_row, ctrl_row or 0) + 1

            data_end = ws.max_row
            for r in range(data_start, ws.max_row + 1):
                for c in range(1, ws.max_column + 1):
                    val = ws.cell(r, c).value
                    if isinstance(val, str) and "备注" in val.replace(" ", ""):
                        data_end = r - 1
                        break
                if data_end != ws.max_row:
                    break
            logger.debug(f"工作表{ws.title}数据范围: {data_start} - {data_end}")
            table_left, table_right = detail_table_bounds(
                ws,
                header_row=min(row for row in (feature_type_row, ctrl_row) if row is not None),
                columns=(feature_type_col, ctrl_col),
            )
            r = data_start
            valid_content = [
                "关键特殊特性",
                "重要特殊特性",
                "[G]",
                "[Z]",
                "[SC]",
                "[CC]",
                "SC",
                "CC",
            ]
            while r <= data_end:
                # 跳过 PDF 转换后的重复页眉行
                if self._is_cp_repeated_header_row(ws, r):
                    r += 1
                    continue
                section_header = detail_section_header_merge(
                    ws,
                    r,
                    table_left=table_left,
                    table_right=table_right,
                )
                if section_header:
                    r = section_header.max_row + 1
                    continue

                # 读取特殊特性分类列的值
                is_merged_cell = self._get_detailed_merge_info(ws, r, feature_type_col)
                special_cell = ws.cell(is_merged_cell.min_row if is_merged_cell else r, feature_type_col)
                if special_cell.data_type == "e":
                    ctx.note_cell(ws.title, special_cell.row, special_cell.column)
                    return "UNABLE", f"行号{r} 特殊特性分类无法确认"
                val_special = special_cell.value
                # A real detail row evaluates the conditional requirement even
                # when its special-characteristic antecedent is false.
                if any(ws.cell(r, c).value is not None for c in {table_left, feature_type_col, ctrl_col}):
                    detail_rows += 1
                    ctx.note_cell(ws.title, special_cell.row, special_cell.column)
                    ctx.record_execution()

                if val_special is not None and str(val_special).strip():
                    v = str(val_special).strip()
                    if any(vc == v for vc in valid_content):
                        is_merged_ctrl = self._get_detailed_merge_info(ws, r, ctrl_col)
                        val_ctrl = ws.cell(
                            is_merged_ctrl.min_row if is_merged_ctrl else r,
                            ctrl_col,
                        ).value
                        control_cell = ws.cell(is_merged_ctrl.min_row if is_merged_ctrl else r, ctrl_col)
                        ctx.note_cell(ws.title, control_cell.row, control_cell.column)
                        if control_cell.data_type == "e":
                            ctx.note_cell(ws.title, control_cell.row, control_cell.column)
                            return "UNABLE", f"行号{r} 控制方法无法确认"
                        if val_ctrl is None or str(val_ctrl).strip() == "":
                            return "NG", f"行号{r} 特殊特性分类对应行的控制方法列为空"

                # Every row of a merged 特殊特性分类 block is checked against its own 控制方法.
                r += 1

        if not found:
            return "NG", "未找到'特殊特性分类'或'控制方法'列"
        if not detail_rows:
            return "NG", CP_NO_DETAIL_ROWS
        return "Pass", "特殊特性分类、控制方法列符合要求"

    @rule("频率或频次列完整性审核")
    def rule_6(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 5: 频率/频次列检查

        使用灵活关键词搜索，支持纯中文("频率"/"频次")和中英文双语表头。
        遍历所有工作表。
        """
        wb = self._workbook_from_ctx(ctx)
        for ws in wb.worksheets:
            freq_row, freq_col = self._find_column_by_keywords_multi(
                ws,
                ["频率", "频次", "频度"],
                ["freq"],
            )
            if freq_row is None or freq_col is None:
                logger.debug(f"工作表{ws.title}未找到频率或频次列")
                continue

            logger.debug(f"工作表{ws.title}频率/频次 {freq_row}, col {freq_col}")

            is_merged = self._get_detailed_merge_info(ws, freq_row, freq_col)
            data_start = (is_merged.max_row + 1) if is_merged else (freq_row + 1)

            data_end = ws.max_row
            for r in range(data_start, ws.max_row + 1):
                for c in range(1, ws.max_column + 1):
                    val = ws.cell(r, c).value
                    if isinstance(val, str) and "备注" in val.replace(" ", ""):
                        data_end = r - 1
                        break
                if data_end != ws.max_row:
                    break

            logger.debug(f"工作表{ws.title}数据范围: {data_start} - {data_end}")
            table_left, table_right = detail_table_bounds(
                ws,
                header_row=freq_row or 1,
                columns=(freq_col,),
            )

            r = data_start
            detail_rows = 0

            while r <= data_end:
                # 跳过 PDF 转换后的重复页眉行
                if self._is_cp_repeated_header_row(ws, r):
                    r += 1
                    continue
                section_header = detail_section_header_merge(
                    ws,
                    r,
                    table_left=table_left,
                    table_right=table_right,
                )
                if section_header:
                    r = section_header.max_row + 1
                    continue
                if self._is_wide_merged_detail_section_row(
                    ws,
                    r,
                    table_left=table_left,
                    table_right=table_right,
                    required_col=freq_col,
                ):
                    r += 1
                    continue

                is_merged_cell = self._get_detailed_merge_info(ws, r, freq_col)
                if is_merged_cell:
                    val_freq = ws.cell(is_merged_cell.min_row, freq_col).value
                else:
                    val_freq = ws.cell(r, freq_col).value

                if not any(has_business_text(ws.cell(r, c).value) for c in range(table_left, table_right + 1)):
                    r += 1
                    continue

                detail_rows += 1
                if val_freq is None or str(val_freq).strip() == "":
                    return "NG", f"行号{r} 频率/频次列为空"

                r = (is_merged_cell.max_row + 1) if is_merged_cell else (r + 1)

            if not detail_rows:
                return "NG", CP_NO_DETAIL_ROWS
            return "Pass", "频率/频次列符合要求"

        return "NG", "未找到'频率'或'频次'列"

    @rule("控制计划表头字段完整性及必填项审核")
    def rule_7(self, ctx: RuleContext) -> Tuple[str, str]:
        """规则 6: 表头关键信息检查

        对必填字段使用灵活关键词匹配，支持纯中文表头与中英双语表头。
        关键词比对时同样去除空格，以匹配 space-stripped cell 文本。
        """
        # 每个字段: (keywords, optional)
        # optional=True 的字段缺失时不判 NG（不是所有模板都有页数/页码）
        required_fields = {
            "控制计划类型": (
                [
                    "控制计划类型",
                    "样件",
                    "试生产",
                    "生产",
                    "prototype",
                    "pre-launch",
                    "production",
                ],
                False,
            ),
            "控制计划编号": (
                [
                    "控制计划编号",
                    "控制计划号",
                    "controlplannumber",
                    "controlplanno",
                    "cpno",
                ],
                False,
            ),
            "联系人/电话": (["联系人", "contact", "电话", "phone"], False),
            "日期(编制)": (["日期", "date"], False),
            "版本": (["版本", "version", "rev"], False),
            "页数": (["页数", "pp."], False),
            "页码": (["页码", "page"], False),
            "零件号": (["零件号", "零件编号", "partnumber", "partno"], False),
            "零件名称": (["零件名称", "partname", "零件描述"], False),
            "多功能小组": (
                [
                    "多功能小组",
                    "核心小组",
                    "coreteam",
                    "multifunction",
                    "multifunctional",
                ],
                False,
            ),
            "公司": (
                [
                    "公司",
                    "组织",
                    "工厂",
                    "组织/工厂",
                    "company",
                    "supplier",
                    "供应商",
                    "供货商",
                    "plant",
                    "organization",
                ],
                False,
            ),
            "公司代码": (
                ["公司代码", "供应商代码", "供货商代码", "companycode", "suppliercode"],
                False,
            ),
            "公司批准/日期": (
                [
                    "公司批准",
                    "供应商批准",
                    "供货商批准",
                    "工厂批准",
                    "plantapproval",
                    "supplierappr",
                ],
                False,
            ),
            "其它批准/日期": (["其它批准", "其他批准", "otherappr"], False),
            "顾客工程批准/日期": (
                ["顾客工程批准", "工程批准", "customereng", "engineeringappr"],
                False,
            ),
            "顾客质量批准/日期": (
                ["顾客质量批准", "质量批准", "customerquality", "qualityappr"],
                False,
            ),
        }

        wb = self._workbook_from_ctx(ctx)
        best_missing: list[str] | None = None  # track best (fewest missing) across sheets

        for ws in wb.worksheets:
            all_cells: Dict[Tuple[int, int], str] = {}
            _control_plan_type_row, control_plan_type_col = None, None
            control_plan_no_row, control_plan_no_col = None, None
            contact_phone_row, contact_phone_col = None, None
            date_row, date_col = None, None
            revision_date_row, revision_date_col = None, None
            version_row, version_col = None, None
            page_count_row, page_count_col = None, None
            page_number_row, page_number_col = None, None
            part_no_row, part_no_col = None, None
            part_name_row, part_name_col = None, None
            multifunctional_team_row, multifunctional_team_col = None, None
            company_row, company_col = None, None
            company_code_row, company_code_col = None, None
            company_approval_row, company_approval_col = None, None
            other_approval_fields: List[Tuple[int, int, str]] = []
            customer_engineering_approval_row, customer_engineering_approval_col = (
                None,
                None,
            )
            customer_quality_approval_row, customer_quality_approval_col = None, None
            # 在ws中查找各个关键信息列
            for row in range(1, ws.max_row + 1):
                for col in range(1, ws.max_column + 1):
                    val = ws.cell(row=row, column=col).value
                    if val is None:
                        continue
                    val_clean = str(val).replace("\n", "").replace("\r", "").replace(" ", "").strip().lower()
                    if val_clean:
                        all_cells[(row, col)] = val_clean
                    if ("控制计划类型" in val_clean) or any(
                        k in val_clean
                        for k in [
                            "样件sample",
                            "试生产pre-launch",
                            "生产production",
                            "prototype",
                            "pre-launch",
                            "production",
                        ]
                    ):
                        if control_plan_type_col is None:
                            _control_plan_type_row, control_plan_type_col = row, col
                        continue
                    if "控制计划编号" in val_clean and control_plan_no_col is None:
                        control_plan_no_row, control_plan_no_col = row, col
                        continue
                    if "联系人contact/电话phone" in val_clean and contact_phone_col is None:
                        contact_phone_row, contact_phone_col = row, col
                        continue
                    if "日期date（编制compi.）" in val_clean and date_col is None:
                        date_row, date_col = row, col
                        continue
                    if ("日期date（修订update）" in val_clean or "日期date(修订" in val_clean or "修订日期" in val_clean) and revision_date_col is None:
                        revision_date_row, revision_date_col = row, col
                        continue
                    if "版本rev." in val_clean and version_col is None:
                        version_row, version_col = row, col
                        continue
                    if "页数（pp.）" in val_clean and page_count_col is None:
                        page_count_row, page_count_col = row, col
                        continue
                    if "页码（p.）" in val_clean and page_number_col is None:
                        page_number_row, page_number_col = row, col
                        continue
                    if "零件号partnumber" in val_clean and part_no_col is None:
                        part_no_row, part_no_col = row, col
                        continue
                    if "零件名称partname" in val_clean and part_name_col is None:
                        part_name_row, part_name_col = row, col
                        continue
                    if "多功能小组mulfunctionteam" in val_clean and multifunctional_team_col is None:
                        multifunctional_team_row, multifunctional_team_col = row, col
                        continue
                    if "公司company" in val_clean and company_col is None:
                        company_row, company_col = row, col
                        continue
                    if "公司代码companycode" in val_clean and company_code_col is None:
                        company_code_row, company_code_col = row, col
                        continue
                    if "公司批准/日期" in val_clean and company_approval_col is None:
                        company_approval_row, company_approval_col = row, col
                        continue
                    if "其它批准/日期（如需要）" in val_clean or "其它批准/日期(如需要)" in val_clean or "其他批准/日期（如需要）" in val_clean or "其他批准/日期(如需要)" in val_clean:
                        if "其它批准/日期（如需要）" in val_clean:
                            display_name = "其它批准/日期（如需要）"
                        elif "其它批准/日期(如需要)" in val_clean:
                            display_name = "其它批准/日期(如需要)"
                        elif "其他批准/日期（如需要）" in val_clean:
                            display_name = "其他批准/日期（如需要）"
                        else:
                            display_name = "其他批准/日期(如需要)"
                        other_approval_fields.append((row, col, display_name))
                        continue
                    if "顾客工程批准/日期(如需要）" in val_clean and customer_engineering_approval_col is None:
                        (
                            customer_engineering_approval_row,
                            customer_engineering_approval_col,
                        ) = row, col
                        continue
                    if "顾客质量批准/日期(如需要)" in val_clean and customer_quality_approval_col is None:
                        customer_quality_approval_row, customer_quality_approval_col = (
                            row,
                            col,
                        )
                        continue
            missing_fields = []
            if control_plan_type_col is None:
                missing_fields.append("控制计划类型")
            if control_plan_no_col is None:
                missing_fields.append("控制计划编号")
            if contact_phone_col is None:
                missing_fields.append("联系人contact/电话phone")
            if date_col is None:
                missing_fields.append("日期date（编制compi.）")
            if revision_date_col is None:
                missing_fields.append("日期date（修订update）")
            if version_col is None:
                missing_fields.append("版本Rev.")
            if page_count_col is None:
                missing_fields.append("页数")
            if page_number_col is None:
                missing_fields.append("页码")
            if part_no_col is None:
                missing_fields.append("零件号")
            if part_name_col is None:
                missing_fields.append("零件名称")
            if multifunctional_team_col is None:
                missing_fields.append("多功能小组")
            if company_col is None:
                missing_fields.append("公司")
            if company_code_col is None:
                missing_fields.append("公司代码")
            if company_approval_col is None:
                missing_fields.append("公司批准/日期")
            if not other_approval_fields:
                missing_fields.append("其它批准/日期（如需要）")
            if customer_engineering_approval_col is None:
                missing_fields.append("顾客工程批准/日期(如需要）")
            if customer_quality_approval_col is None:
                missing_fields.append("顾客质量批准/日期(如需要)")
            if missing_fields:
                if best_missing is None or len(missing_fields) < len(best_missing):
                    best_missing = missing_fields
                continue
            # 检查各字段内容是否为空
            # 根据模板，部分字段值在同一行右侧一列，另一部分字段值与字段名在同一单元格
            # 首先检查字段值在右侧一列的字段: “控制计划编号”、“联系人contact/电话phone”、“日期date（编制compi.）”、“页数”、“页码”
            empty_fields: List[str] = []
            right_value_checks: List[Tuple[str, Optional[int], Optional[int]]] = [
                ("控制计划编号", control_plan_no_row, control_plan_no_col),
                ("联系人contact/电话phone", contact_phone_row, contact_phone_col),
                ("日期date（编制compi.）", date_row, date_col),
            ]
            if page_count_row is not None and page_count_col is not None:
                right_value_checks.append(("页数", page_count_row, page_count_col))
            if page_number_row is not None and page_number_col is not None:
                right_value_checks.append(("页码", page_number_row, page_number_col))
            for field_name, field_row, field_col in right_value_checks:
                if field_row is None or field_col is None:
                    empty_fields.append(field_name)
                    continue
                field_value = self._read_right_header_value(ws, field_row, field_col)
                if self._is_empty_header_value(field_value):
                    empty_fields.append(field_name)

            if revision_date_row is not None and revision_date_col is not None:
                revision_date_value = self._read_right_header_value(ws, revision_date_row, revision_date_col)
                if self._is_empty_header_value(revision_date_value):
                    empty_fields.append("日期date（修订update）")

            inline_field_checks: List[Tuple[str, Optional[int], Optional[int], List[str], bool]] = [
                ("版本号Rev.", version_row, version_col, ["版本rev."], False),
                ("零件号", part_no_row, part_no_col, ["零件号partnumber"], False),
                ("零件名称", part_name_row, part_name_col, ["零件名称partname"], False),
                ("多功能小组", multifunctional_team_row, multifunctional_team_col, ["多功能小组mulfunctionteam"], False),
                ("公司", company_row, company_col, ["公司company"], False),
                (
                    "顾客工程批准/日期(如需要）",
                    customer_engineering_approval_row,
                    customer_engineering_approval_col,
                    ["顾客工程批准/日期(如需要）", "customerengneeringappro./date(ifneed)"],
                    False,
                ),
                (
                    "顾客质量批准/日期(如需要)",
                    customer_quality_approval_row,
                    customer_quality_approval_col,
                    ["顾客质量批准/日期(如需要)", "customerqualityappro./date(ifneed)"],
                    False,
                ),
            ]
            if company_code_row is not None and company_code_col is not None:
                inline_field_checks.append(("公司代码", company_code_row, company_code_col, ["公司代码companycode"], False))
            if company_approval_row is not None and company_approval_col is not None:
                inline_field_checks.append(
                    (
                        "公司批准/日期",
                        company_approval_row,
                        company_approval_col,
                        ["公司批准/日期", "companyappr./date", "comparyappr./date"],
                        False,
                    )
                )

            for display_name, field_row, field_col, prefixes, require_all in inline_field_checks:
                if field_row is None or field_col is None:
                    empty_fields.append(display_name)
                    continue
                inline_value = self._extract_inline_header_value(
                    ws,
                    field_row,
                    field_col,
                    prefixes,
                    require_all=require_all,
                )
                if inline_value is None or self._is_empty_header_value(inline_value):
                    empty_fields.append(display_name)
                logger.debug("%s字段值: %s", display_name, inline_value)

            other_approval_prefixes = [
                "其它批准/日期（如需要）",
                "其它批准/日期(如需要)",
                "其他批准/日期（如需要）",
                "其他批准/日期(如需要)",
                "otherappro./date(ifneed)",
            ]
            for other_row, other_col, display_name in other_approval_fields:
                other_value = self._extract_inline_header_value(
                    ws,
                    other_row,
                    other_col,
                    other_approval_prefixes,
                    require_all=False,
                )
                if other_value is None or self._is_empty_header_value(other_value):
                    empty_fields.append(display_name)
                logger.debug("%s字段值: %s", display_name, other_value)
            if empty_fields:
                field_order = {
                    "控制计划编号": 10,
                    "联系人contact/电话phone": 20,
                    "日期date（编制compi.）": 30,
                    "日期date（修订update）": 40,
                    "版本号Rev.": 50,
                    "页数": 60,
                    "页码": 70,
                    "零件号": 80,
                    "零件名称": 90,
                    "多功能小组": 100,
                    "公司": 110,
                    "公司代码": 120,
                    "公司批准/日期": 130,
                    "其它批准/日期（如需要）": 140,
                    "其它批准/日期(如需要)": 150,
                    "其他批准/日期（如需要）": 160,
                    "其他批准/日期(如需要)": 170,
                    "顾客工程批准/日期(如需要）": 180,
                    "顾客质量批准/日期(如需要)": 190,
                }
                empty_fields = sorted(set(empty_fields), key=lambda name: field_order.get(name, 999))
                return "NG", f"字段内容为空：{'、'.join(empty_fields)}"

            if not all_cells:
                continue

            # 检查各字段是否存在
            missing = []
            for field_name, (keywords, optional) in required_fields.items():
                if optional:
                    continue  # skip optional fields
                found = False
                for _pos, cleaned in all_cells.items():
                    for kw in keywords:
                        kw_clean = kw.lower().replace(" ", "")
                        if kw_clean in cleaned:
                            found = True
                            break
                    if found:
                        break
                if not found:
                    missing.append(field_name)

            if missing:
                if best_missing is None or len(missing) < len(best_missing):
                    best_missing = missing
                continue  # 当前 sheet 缺少字段，尝试下一个 sheet

            # ---- 检查"控制计划类型"是否有且仅有一个选项被勾选 ----
            # Only the option rows of the control plan type belong to this
            # choice; a mark in the data table is not a control plan type.
            marks = {"■", "☑", "✓", "√"}
            selected_stage_types: List[str] = []
            type_rows = range(_control_plan_type_row - 1, _control_plan_type_row + 3) if _control_plan_type_row is not None else range(0)
            for (_r, _c), cleaned in all_cells.items():
                if _r not in type_rows:
                    continue
                if any(m in cleaned for m in marks):
                    for stage_type in self._extract_selected_stages_from_cell(cleaned):
                        if stage_type not in selected_stage_types:
                            selected_stage_types.append(stage_type)
                    # 检查相邻 cell
                    for dc in range(-4, 5):
                        if dc == 0:
                            continue
                        adj = all_cells.get((_r, _c + dc))
                        classified_stage_type = self._classify_stage_text(adj or "")
                        if classified_stage_type and classified_stage_type not in selected_stage_types:
                            selected_stage_types.append(classified_stage_type)

            if len(selected_stage_types) != 1:
                return "NG", "控制计划类型需有且仅有一个选项被勾选"

            return "Pass", "表头关键信息检查通过"

        if best_missing:
            return "NG", f"缺少表头关键信息字段: {', '.join(best_missing)}"
        return "NG", "缺少表头关键信息字段: 未找到任何有效工作表"

    @rule("流程一致性审核")
    def rule_8(self, ctx: RuleContext) -> Tuple[str, str]:
        wb = self._workbook_from_ctx(ctx)
        return self._check_cross_rule_7(wb)

    @rule("特性一致性审核")
    def rule_9(self, ctx: RuleContext) -> Tuple[str, str]:
        wb = self._workbook_from_ctx(ctx)
        return self._check_cross_rule_8(wb)

    @rule("措施一致性审核（PFMEA与CP）")
    def rule_10(self, ctx: RuleContext) -> Tuple[str, str]:
        wb = self._workbook_from_ctx(ctx)
        return self._check_cross_rule_9(wb)

    @rule("措施一致性审核（特殊特性清单与CP）")
    def rule_11(self, ctx: RuleContext) -> Tuple[str, str]:
        wb = self._workbook_from_ctx(ctx)
        return self._check_cross_rule_10(wb)
