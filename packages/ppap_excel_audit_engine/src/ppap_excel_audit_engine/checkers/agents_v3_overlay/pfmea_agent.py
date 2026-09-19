"""潜在过程失效模式及后果分析（PFMEA）Excel 审核代理。"""

import logging
import re
from collections.abc import Iterator
from typing import Any, Callable, Dict, List, Optional, Tuple

from openpyxl.worksheet.worksheet import Worksheet
from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.checkers.date_utils import normalize_excel_date
from ppap_excel_audit_engine.rule_engine import RuleContext

from .cross_file_extractors import (
    _load_workbook_auto,
    extract_rows_from_workbook,
    format_row_location,
    is_pfd_header_row,
    normalize_key,
    normalize_text,
    prepend_row_location,
)
from .excel_agent import ExcelAgent, rule
from .pfmea_shared import (
    PFD_FIELD_ALIASES as _PFD_FIELD_ALIASES,
)
from .pfmea_shared import (
    PFMEA_SELF_FIELD_ALIASES as _PFMEA_SELF_FIELD_ALIASES,
)
from .pfmea_shared import (
    display_values,
    value_matches,
)

logger = logging.getLogger(__name__)

_EMPTY = {"", "nan", "none"}
_PROCESS_CODE_PATTERN = re.compile(r"^\s*([A-Za-z]?\d+(?:[.\-]\d+)*)")
_CHINESE_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_ENGLISH_TEXT_PATTERN = re.compile(r"[A-Za-z]+")
_PROCESS_CODE_SEP_PATTERN = re.compile(r"[.\-\s]+")
_RULE6_PART_NUMBER_ALIASES = ["零件编号", "零件号", "part number", "partnumber"]
_RULE7_PFD_SPECIAL_MARKS = frozenset({"cc", "sc"})
_RULE7_PFMEA_SPECIAL_MARKS = frozenset({"cc", "sc", "kpc", "kcc"})


class PfmeaAgent(ExcelAgent):
    """PFMEA专业审核代理"""

    DOC_TYPE = PpapDocumentType.PFMEA
    EXPECTED_RULE_CATEGORIES = (
        "表头字段完整性审核",
        "PFMEA与变更履历一致性审核",
        "过程项功能维度审核",
        "失效影响及严重度审核",
        "现行探测控制及探测度审核",
        "流程一致性审核",
        "特性一致性审核",
    )
    default_sheet_include = ["PFMEA"]
    _pfmea_sheet_keywords = ["pfmea", "过程fmea", "fmea"]
    _change_history_keywords = [
        "变更履历",
        "更新履历",
        "change log",
        "changelog",
        "revision",
        "变更记录",
    ]

    # ------------------------------------------------------------------
    # Sheet / cell lookup helpers
    # ------------------------------------------------------------------

    _PFMEA_SHEET_EXCLUDE_REF = frozenset(["sod", "准则", "criteria", "评估准则", "评分", "minutes", "remark"])

    def _find_pfmea_worksheets(self, workbook: Any) -> List[Any]:
        if not hasattr(workbook, "sheetnames"):
            return []
        for sn in workbook.sheetnames:
            if sn.strip().upper() == "PFMEA":
                return [workbook[sn]]
        for kw in self._pfmea_sheet_keywords:
            for sn in workbook.sheetnames:
                sn_lower = sn.lower()
                if kw.lower() in sn_lower and not any(ex in sn_lower for ex in self._PFMEA_SHEET_EXCLUDE_REF):
                    return [workbook[sn]]
        exclude = ["change", "log", "变更", "更新", "改进", "跟踪"]
        for sn in workbook.sheetnames:
            if not any(ex in sn.lower() for ex in exclude):
                ws = workbook[sn]
                if ws.max_row and ws.max_row > 5:
                    return [ws]
        return []

    def _find_change_history_worksheet(self, workbook: Any) -> Any:
        if not hasattr(workbook, "sheetnames"):
            return None
        for sn in workbook.sheetnames:
            sn_lower = sn.lower().replace(" ", "")
            if any(kw.lower().replace(" ", "") in sn_lower for kw in self._change_history_keywords):
                return workbook[sn]
        return None

    def _find_cell_by_keywords(
        self,
        ws: Worksheet,
        keywords: List[str],
        *,
        match_mode: str = "contains",
        max_scan_rows: int = 120,
        max_scan_cols: int = 120,
    ) -> Tuple[int, int]:
        lowered = [normalize_key(k) for k in keywords if normalize_key(k)]
        if not lowered:
            return 0, 0
        row_limit = min(max_scan_rows, ws.max_row)
        col_limit = min(max_scan_cols, ws.max_column)
        for r in range(1, row_limit + 1):
            for c in range(1, col_limit + 1):
                cell_key = normalize_key(ws.cell(row=r, column=c).value)
                if not cell_key:
                    continue
                if match_mode == "exact":
                    if any(cell_key == kw for kw in lowered):
                        return r, c
                    continue
                if any(kw in cell_key for kw in lowered):
                    return r, c
        return 0, 0

    def _find_worksheets_by_keywords(
        self,
        workbook: Any,
        *,
        include_keywords: Optional[List[str]] = None,
        default_include: Optional[List[str]] = None,
        match_mode: str = "contains",
        fallback_exclude_keywords: Optional[List[str]] = None,
        fallback_first_sheet: bool = False,
    ) -> List[Worksheet]:
        if not hasattr(workbook, "sheetnames"):
            return []
        include = include_keywords or default_include or []
        include_keys = [normalize_key(v) for v in include if normalize_key(v)]

        matched: List[Worksheet] = []
        for sheet_name in workbook.sheetnames:
            name_key = normalize_key(sheet_name)
            if not include_keys:
                matched.append(workbook[sheet_name])
                continue
            if match_mode == "exact":
                if any(name_key == kw for kw in include_keys):
                    matched.append(workbook[sheet_name])
            elif any(kw in name_key for kw in include_keys):
                matched.append(workbook[sheet_name])
        if matched:
            return matched

        if fallback_exclude_keywords:
            excludes = [normalize_key(v) for v in fallback_exclude_keywords if normalize_key(v)]
            for sheet_name in workbook.sheetnames:
                name_key = normalize_key(sheet_name)
                if any(ex in name_key for ex in excludes):
                    continue
                return [workbook[sheet_name]]

        if fallback_first_sheet and workbook.sheetnames:
            return [workbook[workbook.sheetnames[0]]]
        return []

    def _get_detailed_merge_info(self, ws: Worksheet, row: int, col: int) -> Any:
        for merged_range in ws.merged_cells.ranges:
            if merged_range.min_row <= row <= merged_range.max_row and merged_range.min_col <= col <= merged_range.max_col:
                return merged_range
        return None

    def _data_start_row_below_anchor(self, ws: Worksheet, row: int, col: int) -> int:
        start = row + 1
        while start <= ws.max_row:
            value = normalize_text(ws.cell(row=start, column=col).value)
            if value:
                return start
            start += 1
        return row + 1

    def _iter_rows_with_merge_step(
        self,
        ws: Worksheet,
        start_row: int,
        *,
        anchor_col: int,
        end_row: int,
        max_consecutive_empty: int = 40,
    ) -> Iterator[int]:
        empty_count = 0
        for row in range(start_row, end_row + 1):
            anchor = normalize_text(ws.cell(row=row, column=anchor_col).value)
            if anchor:
                empty_count = 0
                yield row
                continue

            has_any_value = False
            for col in range(1, ws.max_column + 1):
                if normalize_text(ws.cell(row=row, column=col).value):
                    has_any_value = True
                    break
            if has_any_value:
                empty_count = 0
                yield row
                continue

            empty_count += 1
            if empty_count >= max_consecutive_empty:
                break

    def _find_worksheet_by_include_keywords(self, workbook: Any, include_keywords: Optional[List[str]] = None) -> Any:
        return self._find_worksheets_by_keywords(
            workbook,
            include_keywords=include_keywords,
            default_include=self.default_sheet_include,
            match_mode="exact",
            fallback_exclude_keywords=["变更", "更新"],
            fallback_first_sheet=True,
        )

    def _read_label_value(self, ws: Worksheet, label_row: int, label_col: int) -> str:
        """Read value to the right of a label cell, handling merge and inline colon patterns."""
        label_raw = ws.cell(row=label_row, column=label_col).value
        if label_raw is not None:
            label_text = str(label_raw).replace("\n", "").replace("\r", "").strip()
            for sep in ("：", ":"):
                if sep in label_text:
                    after = label_text.split(sep, 1)[1].strip()
                    if after and after.lower() not in _EMPTY:
                        return after

        merged = self._get_detailed_merge_info(ws, label_row, label_col)
        start_col = (merged.max_col + 1) if merged else (label_col + 1)
        for c in range(start_col, min(start_col + 4, ws.max_column + 1)):
            raw = ws.cell(row=label_row, column=c).value
            if raw is not None:
                val = str(raw).replace("\n", "").replace("\r", "").strip()
                if val and val.lower() not in _EMPTY:
                    return val
        return ""

    def _get_data_start(self, ws: Worksheet, row: int, col: int) -> int:
        return self._data_start_row_below_anchor(ws, row, col)

    def _iter_data_rows(self, ws: Worksheet, start: int, anchor_col: int) -> Iterator[int]:
        yield from self._iter_rows_with_merge_step(
            ws,
            start,
            anchor_col=anchor_col,
            end_row=ws.max_row,
        )

    @staticmethod
    def _workbook_from_ctx(ctx: RuleContext) -> Any:
        workbook = getattr(ctx, "_workbook", None)
        if workbook is None:
            raise RuntimeError("RuleContext中缺少workbook对象")
        return workbook

    # ------------------------------------------------------------------
    # Cross-file helpers
    # ------------------------------------------------------------------

    def _require_group_context(self) -> Tuple[bool, str]:
        if not self.group_context:
            return False, "缺少同次提交组上下文，无法执行跨文件规则"
        return True, ""

    def _require_single_related_file(self, doc_type: PpapDocumentType) -> Any:
        files = self.get_related_files(doc_type)
        if not files:
            return None, f"缺少关联文档: {doc_type.get_type_name()}"
        if len(files) > 1:
            return None, f"{doc_type.get_type_name()}存在多份文档，无法判定主文档"
        return files[0], ""

    def _value_matches_any(self, value: str, candidates: set[str]) -> bool:
        return any(value_matches(value, c) for c in candidates)

    @staticmethod
    def _extract_rule_7_special_marks(value: object, valid_marks: frozenset[str]) -> set[str]:
        text = normalize_text(value).lower()
        if not text:
            return set()
        key = normalize_key(text)
        marks: set[str] = set()
        for mark in valid_marks:
            if key == mark:
                marks.add(mark)
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(mark)}(?![a-z0-9])", text):
                marks.add(mark)
        return marks

    def _extract_process_code(self, text: str) -> str:
        normalized = normalize_text(text)
        match = _PROCESS_CODE_PATTERN.match(normalized)
        return match.group(1).lower() if match else ""

    def _build_process_zh_key(self, raw_text: str) -> str:
        process_code = self._extract_process_code(raw_text)
        if not process_code:
            return ""
        zh_text = "".join(_CHINESE_TEXT_PATTERN.findall(normalize_text(raw_text)))
        if not zh_text:
            return ""
        return normalize_key(f"{process_code} {zh_text}")

    def _build_process_en_key(self, raw_text: str) -> str:
        process_code = self._extract_process_code(raw_text)
        if not process_code:
            return ""
        en_text = " ".join(_ENGLISH_TEXT_PATTERN.findall(normalize_text(raw_text)))
        if not en_text:
            return ""
        return normalize_key(f"{process_code} {en_text}")

    def _build_bilingual_reorder_signature(self, raw_text: str) -> Tuple[str, str] | None:
        zh_key = self._build_process_zh_key(raw_text)
        en_key = self._build_process_en_key(raw_text)
        if not zh_key or not en_key:
            return None
        return zh_key, en_key

    def _normalize_process_code_for_match(self, process_code: str) -> str:
        if not process_code:
            return ""
        return _PROCESS_CODE_SEP_PATTERN.sub("", process_code.lower())

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

    @staticmethod
    def _extract_rule_6_part_number_keys(raw_value: str) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()

        def _append_key(candidate: str) -> None:
            key = normalize_key(candidate)
            if not key or key in seen:
                return
            seen.add(key)
            keys.append(key)

        _append_key(raw_value)
        for token in re.split(r"[\s,;，；/、|]+", raw_value):
            _append_key(token)
        return keys

    def _extract_rule_6_part_number_from_sheet(self, ws: Worksheet) -> list[str]:
        part_row, part_col = self._find_cell_by_keywords(ws, _RULE6_PART_NUMBER_ALIASES)
        if part_row <= 0 or part_col <= 0:
            return []
        value = self._read_label_value(ws, part_row, part_col)
        if not value:
            value = normalize_text(ws.cell(row=part_row, column=part_col + 1).value)
        return self._extract_rule_6_part_number_keys(value)

    @staticmethod
    def _is_rule_6_part_number_compatible(left_keys: list[str], right_keys: list[str]) -> bool:
        if not left_keys or not right_keys:
            return False
        for left_key in left_keys:
            for right_key in right_keys:
                if left_key == right_key:
                    return True
                if left_key in right_key or right_key in left_key:
                    return True
        return False

    def _get_rule_6_pfd_sheet_and_rows(self) -> tuple[Any, list[dict[str, str]] | None, str]:
        related_file, _error = self._require_single_related_file(PpapDocumentType.PROCESS_FLOW_CHART)
        if related_file is None:
            return None, None, "缺少过程流程图，请补充"

        file_url = str(getattr(related_file, "file_url", "") or "")
        if not file_url:
            return None, None, "缺少过程流程图，请补充"

        try:
            pfd_workbook = _load_workbook_auto(file_url)
        except Exception as exc:
            logger.warning("无法加载过程流程图文件 %s: %s", file_url, exc)
            return None, None, "缺少过程流程图，请补充"

        pfd_ws = self._find_process_flow_chart_worksheet(pfd_workbook)
        if pfd_ws is None:
            return None, None, "缺少过程流程图，请补充"

        pfd_rows = extract_rows_from_workbook(
            workbook=pfd_workbook,
            field_aliases=_PFD_FIELD_ALIASES,
            required_fields=("operation_name",),
        )
        return pfd_ws, pfd_rows, ""

    def _build_rule_6_pfd_operations(self, pfd_rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
        pfd_ops: dict[str, dict[str, Any]] = {}
        for row in pfd_rows:
            if is_pfd_header_row(row):
                continue
            op_no = normalize_text(row.get("operation_no", "")).strip()
            op_name = normalize_text(row.get("operation_name", "")).strip()
            if not op_no and not op_name:
                continue

            raw_value = normalize_text(f"{op_no} {op_name}".strip())
            primary_key = normalize_key(raw_value)
            if not primary_key:
                continue

            pfd_ops.setdefault(
                primary_key,
                {
                    "row": row,
                    "raw": raw_value,
                    "operation_no": op_no,
                    "normalized_process_code": self._normalize_process_code_for_match(op_no),
                },
            )
        return pfd_ops

    def _build_rule_6_pfmea_steps(self, pfmea_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        pfmea_steps: dict[str, dict[str, str]] = {}
        for row in pfmea_rows:
            raw_value = normalize_text(row.get("process_step", ""))
            primary_key = normalize_key(raw_value)
            if not primary_key:
                continue
            pfmea_steps.setdefault(
                primary_key,
                {
                    "raw": raw_value,
                    "normalized_process_code": self._normalize_process_code_for_match(self._extract_process_code(raw_value)),
                },
            )
        return pfmea_steps

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @rule("表头字段完整性审核")
    def rule_1_tablehead_keywords(self, ctx: RuleContext) -> Any:
        """规则1：表头字段完整性检查"""
        workbook = self._workbook_from_ctx(ctx)
        try:
            ws_list = self._find_pfmea_worksheets(workbook)
            if not ws_list:
                return "NG", "未找到PFMEA工作表"

            field_specs = [
                ("公司名称", ["公司名称", "客户名称"]),
                ("车型项目", ["车型项目", "项目"]),
                ("零件名称", ["零件名称"]),
                ("零件号", ["零件号", "零件编号"]),
                ("制造地址", ["制造地址", "工程地点"]),
                ("PFMEA开始日期", ["pfmea开始日期"]),
                ("PFMEA修订日期", ["pfmea修订日期"]),
                ("跨职能团队", ["跨职能团队", "跨功能团队"]),
            ]
            for ws in ws_list:
                missing, empty = [], []
                for label, keywords in field_specs:
                    row, col = self._find_cell_by_keywords(ws, keywords)
                    if row == 0:
                        missing.append(label)
                    else:
                        val = self._read_label_value(ws, row, col)
                        if not val:
                            empty.append(label)
                if missing:
                    return "NG", f"缺少必要字段: {', '.join(missing)}"
                if empty:
                    return "NG", f"以下字段内容为空: {', '.join(empty)}"
            return "PASS", "规则1检查通过"
        except Exception as e:
            return "NG", f"Excel处理过程中出现错误: {e}"

    @rule("PFMEA与变更履历一致性审核")
    def rule_2_pfmea_start_revision_date(self, ctx: RuleContext) -> Any:
        """规则2：PFMEA日期与变更履历一致性检查"""
        workbook = self._workbook_from_ctx(ctx)
        try:
            pfmea_sheets = self._find_pfmea_worksheets(workbook)
            ch_ws = self._find_change_history_worksheet(workbook)
            if not pfmea_sheets:
                return "NG", "未找到PFMEA工作表"
            if not ch_ws:
                return "NG", "未找到变更履历工作表"

            ws = pfmea_sheets[0]
            start_r, start_c = self._find_cell_by_keywords(ws, ["pfmea开始日期"])
            rev_r, rev_c = self._find_cell_by_keywords(ws, ["pfmea修订日期"])
            if start_r == 0 or rev_r == 0:
                return "NG", "PFMEA工作表中缺少PFMEA开始日期或PFMEA修订日期字段"

            pfmea_start = self._read_label_value(ws, start_r, start_c)
            pfmea_rev = self._read_label_value(ws, rev_r, rev_c)
            if not pfmea_start or not pfmea_rev:
                return "NG", "PFMEA工作表中PFMEA开始日期或PFMEA修订日期内容为空"

            # Find revision date column in change history
            date_r, date_c = self._find_cell_by_keywords(ch_ws, ["修订日期", "日期", "date", "时间", "time"])
            if date_r == 0:
                return "NG", "变更履历工作表中缺少修订日期字段"

            data_start = self._get_data_start(ch_ws, date_r, date_c)

            def _cell_text(r: int) -> str:
                return str(ch_ws.cell(row=r, column=date_c).value or "").replace("\n", "").replace("\r", "").strip()

            first_date = next((t for r in range(data_start, ch_ws.max_row + 1) if (t := _cell_text(r)) and t.lower() not in _EMPTY), None)
            last_date = next((t for r in range(ch_ws.max_row, date_r, -1) if (t := _cell_text(r)) and t.lower() not in _EMPTY), None)
            if not first_date or not last_date:
                return "NG", "变更履历工作表中修订日期列无有效数据"

            dates = [normalize_excel_date(d) for d in (pfmea_start, pfmea_rev, first_date, last_date)]
            if not all(dates):
                return "NG", "PFMEA或变更履历日期格式不合法或为空"
            if dates[0] != dates[2]:
                return "NG", "PFMEA开始日期与变更履历中第一行修订日期不一致"
            if dates[1] != dates[3]:
                return "NG", "PFMEA修订日期与变更履历中最后一行修订日期不一致"
            return "PASS", "规则2检查通过"
        except Exception as e:
            return "NG", f"Excel处理过程中出现错误: {e}"

    @rule("过程项功能维度审核")
    def rule_3_process_item_function_dimensions(self, ctx: RuleContext) -> Any:
        """规则3：过程项功能维度检查"""
        workbook = self._workbook_from_ctx(ctx)
        try:
            ws_list = self._find_pfmea_worksheets(workbook)
            if not ws_list:
                return "NG", "未找到PFMEA工作表"

            required_classic = {
                "内部": ["内部", "工厂内", "厂内", "内"],
                "客户工厂": ["客户工厂", "主机厂", "OEM", "顾客工厂"],
                "最终用户": ["最终用户", "用户", "终端用户", "消费者", "客户", "OEM"],
            }
            required_soc = {"s", "o", "c"}

            for ws in ws_list:
                func_r, func_c = self._find_cell_by_keywords(
                    ws,
                    ["过程项功能", "过程名称的功能", "过程项目的功能", "过程步骤的功能"],
                )
                if func_r == 0:
                    return "NG", "PFMEA工作表中缺少过程项功能字段"

                data_start = self._get_data_start(ws, func_r, func_c)
                header_text = str(ws.cell(row=func_r, column=func_c).value or "").replace(" ", "").lower()
                use_soc = ("过程名称的功能" in header_text) or ("过程步骤的功能" in header_text)
                if not use_soc:
                    for probe in range(data_start, min(data_start + 20, ws.max_row + 1)):
                        if re.search(r"[SOCsoc][:：]", str(ws.cell(row=probe, column=func_c).value or "")):
                            use_soc = True
                            break

                def _row_has_data(row: int) -> bool:
                    return any(ws.cell(row=row, column=c).value is not None and str(ws.cell(row=row, column=c).value).strip() for c in range(1, ws.max_column + 1))

                for r in self._iter_data_rows(ws, data_start, func_c):
                    val = str(ws.cell(row=r, column=func_c).value or "").replace("\n", " ").replace("\r", " ").strip()
                    if val and val.lower() not in _EMPTY:
                        normalized = val.replace(" ", "")
                        if use_soc:
                            found = {m.group(0)[0].lower() for m in re.finditer(r"[SOCsoc][:：]", normalized)}
                            miss = required_soc - found
                            if miss:
                                return "NG", f"工作表{ws.title} 行号{r} 过程项功能内容缺少维度前缀 {','.join(sorted(miss)).upper()}"
                        else:
                            miss_dims = [d for d, aliases in required_classic.items() if not any(a in normalized for a in aliases)]
                            if miss_dims:
                                return "NG", f"工作表{ws.title} 行号{r} 过程项功能内容 '{val}' 缺少维度 '{'、'.join(miss_dims)}'"
                    else:
                        if not _row_has_data(r):
                            break
                        if not use_soc:
                            return "NG", f"工作表{ws.title} 行号{r} 过程项功能内容为空"
            return "PASS", "规则3检查通过"
        except Exception as e:
            return "NG", f"Excel处理过程中出现错误: {e}"

    def _check_score_rule(
        self,
        workbook: Any,
        *,
        content_keywords: List[str],
        score_keywords: List[str],
        content_label: str,
        score_label: str,
        aggregate: Callable[[list[int]], int],
        agg_description: str,
        preprocess: Optional[Callable[[str], str]] = None,
        min_dimensions: int = 1,
        score_extractor: Optional[Callable[[str], list[int]]] = None,
        missing_score_message_builder: Optional[Callable[[str, list[int]], str]] = None,
    ) -> Any:
        """Shared structure for rules 4 & 5: find content+score columns, iterate rows,
        parse numeric scores from content text, compare aggregate with score column.
        """
        try:
            ws_list = self._find_pfmea_worksheets(workbook)
            if not ws_list:
                return "NG", "未找到PFMEA工作表"
            for ws in ws_list:
                content_r, content_c = self._find_cell_by_keywords(ws, content_keywords)
                score_r, score_c = self._find_cell_by_keywords(ws, score_keywords)
                if content_r == 0:
                    return "NG", f"PFMEA工作表中缺少{content_label}字段"
                if score_r == 0:
                    return "NG", f"PFMEA工作表中缺少{score_label}字段"

                data_start = self._get_data_start(ws, content_r, content_c)
                missing_score_rows: list[int] = []

                for r in self._iter_data_rows(ws, data_start, content_c):
                    content_val = str(ws.cell(row=r, column=content_c).value or "").replace("\n", "").replace("\r", "").strip()
                    if preprocess:
                        content_val = preprocess(content_val)
                    score_val = str(ws.cell(row=r, column=score_c).value or "").replace("\n", "").replace("\r", "").strip()

                    # 模板结构行/分组行：内容与分值均为空，直接跳过。
                    if content_val.lower() in _EMPTY and score_val.lower() in _EMPTY:
                        continue

                    dimensions = re.split(r"[，,;:：/\n\r]+", content_val)
                    if len(dimensions) < min_dimensions:
                        return "NG", f"工作表{ws.title} 行号{r} {content_label}内容 '{content_val}' 不包含{min_dimensions}个维度"

                    if score_extractor:
                        scores = score_extractor(content_val)
                    else:
                        scores = list(map(int, re.findall(r"(\d+)", content_val)))
                    if len(scores) < min_dimensions:
                        if missing_score_message_builder:
                            missing_score_rows.append(r)
                            continue
                        return "NG", f"工作表{ws.title} 行号{r} {content_label}内容 '{content_val}' 缺少对应的{score_label}分值"

                    expected = aggregate(scores)

                    if score_val.lower() in _EMPTY:
                        return "NG", f"工作表{ws.title} 行号{r} {score_label}内容为空"
                    try:
                        actual = int(score_val)
                        if actual != expected:
                            return "NG", f"工作表{ws.title} 行号{r} {score_label}分值 '{score_val}' 不是{content_label}中分值的{agg_description} '{expected}'"
                    except ValueError:
                        return "NG", f"工作表{ws.title} 行号{r} {score_label}分值 '{score_val}' 不是有效的整数"
                if missing_score_rows and missing_score_message_builder:
                    return "NG", missing_score_message_builder(ws.title, missing_score_rows)
            return "PASS", f"{score_label}检查通过"
        except Exception as e:
            return "NG", f"Excel处理过程中出现错误: {e}"

    @rule("失效影响及严重度审核")
    def rule_4_failure_effect_dimensions_severity(self, ctx: RuleContext) -> Any:
        """规则4：失效影响维度+严重度分值检查"""
        workbook = self._workbook_from_ctx(ctx)

        def _is_placeholder_dimension_text(text: str) -> bool:
            normalized = re.sub(r"\s+", "", (text or "")).lower()
            return normalized in {"n/a", "na", "n.a", "n.a.", "/", "-", "无", "暂无", "不适用"}

        def _format_issue(sheet_name: str, issue: Tuple[str, int, Any, Any, bool]) -> str:
            issue_type, row, actual, expected, _placeholder = issue
            if issue_type == "severity_not_max":
                return f"工作表{sheet_name} 行号{row}，失效影响中S值最大应为{expected}，而“严重度S”中显示为{actual}"
            if issue_type == "missing_dimension_score":
                return f"工作表{sheet_name} 行号{row} 失效影响未注明严重度，无法判断“严重度S”列是否取的是影响中的最大值"
            if issue_type == "missing_prefix":
                return f"工作表{sheet_name} 行号{row}，失效影响内容缺少维度前缀 {expected}"
            if issue_type == "severity_empty":
                return f"工作表{sheet_name} 行号{row} 严重度内容为空"
            if issue_type == "severity_non_int":
                return f"工作表{sheet_name} 行号{row} 严重度分值 '{actual}' 不是有效的整数"
            if issue_type == "empty_effect":
                return f"工作表{sheet_name} 行号{row} 失效影响内容为空"
            return f"工作表{sheet_name} 行号{row} 失效影响内容 '{actual}' 不包含3个维度"

        try:
            ws_list = self._find_pfmea_worksheets(workbook)
            if not ws_list:
                return "NG", "未找到PFMEA工作表"

            for ws in ws_list:
                effect_r, effect_c = self._find_cell_by_keywords(ws, ["失效影响", "失效影响（fe）"])
                severity_r, severity_c = self._find_cell_by_keywords(ws, ["严重度", "严重度（s）"])
                if effect_r == 0:
                    return "NG", "PFMEA工作表中缺少失效影响字段"
                if severity_r == 0:
                    return "NG", "PFMEA工作表中缺少严重度字段"

                data_start = self._get_data_start(ws, effect_r, effect_c)
                issues: List[Tuple[str, int, Any, Any, bool]] = []
                for r in self._iter_data_rows(ws, data_start, effect_c):
                    content_val = str(ws.cell(row=r, column=effect_c).value or "").replace("\n", "\n").replace("\r", "\n").strip()
                    severity_val = str(ws.cell(row=r, column=severity_c).value or "").replace("\n", "").replace("\r", "").strip()

                    # 模板结构行/分组行：失效影响与严重度均为空，直接跳过。
                    if content_val.lower() in _EMPTY and severity_val.lower() in _EMPTY:
                        continue

                    if content_val.lower() in _EMPTY:
                        issues.append(("empty_effect", r, None, None, False))
                        continue

                    expected: Optional[int] = None

                    if re.search(r"[SOCsoc]\s*[:：]", content_val):
                        prefixes: set[str] = set()
                        dimension_scores: List[int] = []
                        has_placeholder_dimension = False
                        for m in re.finditer(
                            r"([SOCsoc])\s*[:：]\s*(.*?)(?=(?:[\n\r]+\s*[SOCsoc]\s*[:：])|$)",
                            content_val,
                            flags=re.S,
                        ):
                            prefix = m.group(1).upper()
                            prefixes.add(prefix)
                            segment = m.group(2).strip()
                            if _is_placeholder_dimension_text(segment):
                                has_placeholder_dimension = True
                            score = self._extract_dimension_score(segment)
                            if score is not None:
                                dimension_scores.append(score)

                        missing_prefixes = {"S", "O", "C"} - prefixes
                        if missing_prefixes:
                            issues.append(("missing_prefix", r, None, ",".join(sorted(missing_prefixes)), False))
                            continue
                        if not dimension_scores:
                            issues.append(("missing_dimension_score", r, None, None, has_placeholder_dimension))
                            continue
                        expected = max(dimension_scores)
                    else:
                        dimensions = [x for x in re.split(r"[，,;:：/\n\r]+", content_val) if x.strip()]
                        if len(dimensions) < 3:
                            issues.append(("dimension_lt_3", r, content_val, None, False))
                            continue

                        scores = list(map(int, re.findall(r"(\d+)", content_val)))
                        if len(scores) < 3:
                            issues.append(("missing_dimension_score", r, None, None, False))
                            continue
                        expected = max(scores)

                    if severity_val.lower() in _EMPTY:
                        issues.append(("severity_empty", r, None, None, False))
                        continue
                    try:
                        actual = int(severity_val)
                    except ValueError:
                        issues.append(("severity_non_int", r, severity_val, None, False))
                        continue

                    if expected is not None and actual != expected:
                        issues.append(("severity_not_max", r, actual, expected, False))

                if issues:
                    has_mismatch = any(i[0] == "severity_not_max" for i in issues)
                    if not has_mismatch:
                        return "NG", _format_issue(ws.title, sorted(issues, key=lambda x: x[1])[0])

                    filtered = [i for i in issues if not (i[0] == "missing_dimension_score" and i[4])]
                    selected = sorted(filtered or issues, key=lambda x: x[1])[:3]
                    if len(selected) == 1:
                        return "NG", _format_issue(ws.title, selected[0])
                    details = [f"{idx}. {_format_issue(ws.title, issue)}；" for idx, issue in enumerate(selected, start=1)]
                    return "NG", " ".join(details)
            return "PASS", "严重度检查通过"
        except Exception as e:
            return "NG", f"Excel处理过程中出现错误: {e}"

    def _extract_dimension_score(self, text: str) -> Optional[int]:
        """Extract severity score from one dimension sentence, ignoring percentages."""
        normalized = (text or "").strip()
        if not normalized:
            return None

        bracket_scores = re.findall(r"[（(]\s*(\d{1,2})\s*[）)]", normalized)
        if bracket_scores:
            return int(bracket_scores[-1])

        tail_match = re.search(r"(\d{1,2})\s*[。；;，,、]?\s*$", normalized)
        if tail_match:
            return int(tail_match.group(1))
        return None

    @rule("现行探测控制及探测度审核")
    def rule_5_current_detection_controls_detection(self, ctx: RuleContext) -> Any:
        """规则5：现行探测控制+探测度分值检查"""
        workbook = self._workbook_from_ctx(ctx)

        def _strip_numbering(text: str) -> str:
            text = re.sub(r"\d+\.", "", text)
            text = re.sub(r"\d+、", "", text)
            return text

        def _extract_detection_scores(text: str) -> list[int]:
            normalized = (text or "").strip()
            if not normalized:
                return []
            segments = [seg.strip() for seg in re.split(r"[\n\r;；]+", normalized) if seg.strip()]
            if not segments:
                segments = [normalized]
            scores: list[int] = []
            for seg in segments:
                bracket_scores = [int(v) for v in re.findall(r"[（(]\s*(10|[1-9])\s*[）)]", seg)]
                if bracket_scores:
                    scores.extend(bracket_scores)
                    continue
                tail_match = re.search(r"(?<!\d)(10|[1-9])\s*[。；;，,、]?\s*$", seg)
                if tail_match:
                    scores.append(int(tail_match.group(1)))
            return scores

        def _build_missing_score_message(sheet_name: str, rows: list[int]) -> str:
            row_values = "、".join(str(v) for v in rows[:5])
            suffix = "等" if len(rows) > 5 else ""
            return f"工作表{sheet_name} 行号{row_values}{suffix} 现行探测措施下的内容未标注“探测度D”的值，无法判断“探测度D”是否取的是探测措施中的最小值"

        return self._check_score_rule(
            workbook,
            content_keywords=["现行探测控制", "探测措施"],
            score_keywords=["探测度", "探测度（d）"],
            content_label="现行探测控制",
            score_label="探测度",
            aggregate=min,
            agg_description="最小值",
            preprocess=_strip_numbering,
            min_dimensions=1,
            score_extractor=_extract_detection_scores,
            missing_score_message_builder=_build_missing_score_message,
        )

    @rule("流程一致性审核")
    def rule_6_process_steps_consistency(self, ctx: RuleContext) -> Any:
        """规则6（跨文件）：流程一致性审核"""
        workbook = self._workbook_from_ctx(ctx)
        ok, msg = self._require_group_context()
        if not ok:
            return "NG", msg

        pfd_ws, pfd_rows, pfd_error = self._get_rule_6_pfd_sheet_and_rows()
        if pfd_error:
            return "NG", pfd_error

        pfmea_sheets = self._find_pfmea_worksheets(workbook)
        if not pfmea_sheets:
            return "NG", "缺少PFMEA，请补充"

        pfmea_rows = extract_rows_from_workbook(
            workbook=workbook,
            field_aliases=_PFMEA_SELF_FIELD_ALIASES,
            required_fields=("process_step",),
        )
        if not pfmea_rows:
            return "NG", "PFMEA无法识别过程步骤列结构"

        pfd_ops = self._build_rule_6_pfd_operations(pfd_rows or [])
        if not pfd_ops:
            return "NG", "PFD未提取到有效工序数据"

        pfmea_steps = self._build_rule_6_pfmea_steps(pfmea_rows)
        if not pfmea_steps:
            return "NG", "PFMEA未提取到有效过程步骤数据"

        missing_issues: list[str] = []
        for op_key, op_info in pfd_ops.items():
            if any(op_key == step_key or op_key in step_key or step_key in op_key for step_key in pfmea_steps):
                continue

            normalized_process_code = op_info["normalized_process_code"]
            if normalized_process_code and any(step_info["normalized_process_code"].startswith(normalized_process_code) for step_info in pfmea_steps.values() if step_info["normalized_process_code"]):
                continue

            missing_issues.append(prepend_row_location(op_info["row"], f"PFD工序'{op_info['raw']}'在PFMEA过程步骤中未找到对应行"))
        if missing_issues:
            suffix = f"...等共{len(missing_issues)}处" if len(missing_issues) > 5 else ""
            return "NG", "; ".join(missing_issues[:5]) + suffix
        return "Pass", "PFD工序名称与PFMEA过程步骤一致"

    @rule("特性一致性审核")
    def rule_7_special_characteristics_consistency(self, ctx: RuleContext) -> Any:
        """规则7（跨文件）：特性一致性审核"""
        workbook = self._workbook_from_ctx(ctx)
        ok, msg = self._require_group_context()
        if not ok:
            return "NG", msg

        pfd_ws, pfd_rows, pfd_error = self._get_rule_6_pfd_sheet_and_rows()
        if pfd_error:
            return "NG", pfd_error

        pfmea_sheets = self._find_pfmea_worksheets(workbook)
        if not pfmea_sheets:
            return "NG", "缺少PFMEA，请补充"

        pfmea_rows = extract_rows_from_workbook(
            workbook=workbook,
            field_aliases=_PFMEA_SELF_FIELD_ALIASES,
            required_fields=("process_step",),
        )
        if not pfmea_rows:
            return "NG", "PFMEA无法识别过程步骤列结构"
        special_header_keys = {normalize_key(alias) for alias in _PFMEA_SELF_FIELD_ALIASES.get("special_char", []) if normalize_key(alias)}

        def _is_effective_special_value(row: Dict[str, str]) -> bool:
            value = normalize_text(row.get("special_char", ""))
            if not value:
                return False
            value_key = normalize_key(value)
            if value_key in special_header_keys:
                return False
            return bool(self._extract_rule_7_special_marks(value, _RULE7_PFMEA_SPECIAL_MARKS))

        if not any(_is_effective_special_value(row) for row in pfmea_rows):
            return "NG", "PFMEA中未找到“特殊特性”列，请检查文件是否与模板一致；"

        # Build PFMEA index by process step
        pfmea_by_step: Dict[str, List[Dict[str, str]]] = {}
        for row in pfmea_rows:
            key = normalize_key(row.get("process_step", ""))
            if key:
                pfmea_by_step.setdefault(key, []).append(row)

        issues: List[str] = []

        def _collect_trigger_types(pfd_row: Dict[str, str]) -> List[str]:
            trigger_types: List[str] = []
            for prefix, label in (("product", "产品"), ("process", "过程")):
                char_type = normalize_text(pfd_row.get(f"{prefix}_char_type", ""))
                marks = self._extract_rule_7_special_marks(char_type, _RULE7_PFD_SPECIAL_MARKS)
                if not marks:
                    continue
                trigger_types.append(f"{label}:{char_type}")
            return trigger_types

        pfd_entries: list[dict[str, Any]] = []
        for pfd_row in pfd_rows or ():
            op_name = normalize_text(pfd_row.get("operation_name", ""))
            op_key = normalize_key(op_name)
            if not op_key:
                continue
            trigger_types = _collect_trigger_types(pfd_row)
            pfd_entries.append(
                {
                    "row": pfd_row,
                    "op_name": op_name,
                    "op_key": op_key,
                    "trigger_types": trigger_types,
                }
            )
            if not trigger_types:
                continue

            matched = [r for k, rows in pfmea_by_step.items() for r in rows if op_key in k or k in op_key]
            if not matched:
                issues.append(
                    prepend_row_location(
                        pfd_row,
                        f"PFD工序'{op_name}'(特性类别={', '.join(trigger_types)})在PFMEA过程步骤中未找到对应行",
                    )
                )
                continue

            pfmea_specials = [normalize_text(r.get("special_char", "")) for r in matched if _is_effective_special_value(r)]
            pfmea_locations = [format_row_location(r) for r in matched if format_row_location(r)]
            if not pfmea_specials:
                issues.append(
                    prepend_row_location(
                        pfd_row,
                        f"PFD工序'{op_name}'特性类别为{', '.join(trigger_types)}，但PFMEA对应行特殊特性为空(PFMEA位置: {display_values(pfmea_locations)})",
                    )
                )
                continue

            if not any(self._extract_rule_7_special_marks(pfmea_special, _RULE7_PFMEA_SPECIAL_MARKS) for pfmea_special in pfmea_specials):
                issues.append(
                    prepend_row_location(
                        pfd_row,
                        f"PFD工序'{op_name}'特性类别为{', '.join(trigger_types)}，"
                        "但PFMEA对应行特殊特性不是KPC/KCC/CC/SC("
                        f"PFMEA值: {display_values(pfmea_specials)}; "
                        f"PFMEA位置: {display_values(pfmea_locations)})",
                    )
                )

        for pfmea_step_key, rows in pfmea_by_step.items():
            special_rows = [row for row in rows if _is_effective_special_value(row)]
            if not special_rows:
                continue

            matched_pfd_entries = [entry for entry in pfd_entries if entry["op_key"] in pfmea_step_key or pfmea_step_key in entry["op_key"]]
            if not matched_pfd_entries:
                row = special_rows[0]
                issues.append(
                    prepend_row_location(
                        row,
                        "PFMEA过程步骤存在特殊特性，但PFD中未找到对应工序",
                    )
                )
                continue
            if any(entry["trigger_types"] for entry in matched_pfd_entries):
                continue

            row = special_rows[0]
            pfmea_specials = [normalize_text(item.get("special_char", "")) for item in special_rows]
            pfd_locations = [format_row_location(entry["row"]) for entry in matched_pfd_entries if format_row_location(entry["row"])]
            issues.append(
                prepend_row_location(
                    row,
                    f"PFMEA过程步骤存在特殊特性(PFMEA值: {display_values(pfmea_specials)}), 但PFD对应工序未标注CC/SC(PFD位置: {display_values(pfd_locations)})",
                )
            )

        if issues:
            return "NG", "; ".join(issues[:5]) + (f"...等共{len(issues)}处" if len(issues) > 5 else "")
        return "Pass", "PFD特性要求与PFMEA特殊特性一致"
