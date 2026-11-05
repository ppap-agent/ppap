"""
过程流程图Excel文件审核代理

### 过程流程图评审规则逻辑
1.识别“过程流程图”中是否包含“零件号”、“零件名称”、“项目车型”、“供应商名称”、“代码”、“编制日期”、“版本”、“团队”字段，如果包含则继续识别；当“零件号”、“零件名称”、“项目车型”、“供应商名称”、“代码”、“编制日期”、“版本”、“团队”字段对应的内容均不为空则通过，否则不通过
2.识别“过程流程图”中是否包含两个“特性类别”列，如果包含则继续识别；当第一个“特性类别”列下内容为“【G】”或“【Z】”，且对应行的“产品特殊特性”列内容不为空则通过，否则不通过
3.识别“过程流程图”中是否包含两个“特性类别”列，如果包含则继续识别；当第二个“特性类别”列下内容为“【G】”或“【Z】”，且对应行的“过程特殊特性”列内容不为空则通过，否则不通过
4.识别“工序名称”、“操作描述”两列是否存在，有一列不存在则不通过，否则继续识别；当“工序名称”不为空且对应行的“操作描述”列内容均不为空则通过，否则不通过
5.识别“工序名称”、“制造”、“运输”、“存储”、“检验”、“返工”、“报废”七列是否存在，有一列不存在则不通过，否则继续识别；当“工序名称”不为空且对应行的“制造”、“运输”、“存储”、“检验”、“返工”、“报废”有一列内容不为空则通过，否则不通过
6.识别“工序名称”、“制造”、“运输”、“存储”、“检验”、“返工”、“报废”七列是否存在，有一列不存在则不通过，否则继续识别；当“工序名称”不为空且对应行的“制造”、“运输”、“存储”、“检验”、“返工”、“报废”有一列内容不为空则通过，否则不通过（检查连线）
"""

import logging
import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU, points_to_pixels
from openpyxl.worksheet.worksheet import Worksheet

from audit.engine.rule_engine import GuardStop, RuleContext, Table
from audit.engine.rule_engine.excel_ops import DefaultExcelOps
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)


def _split_non_empty_lines(value: object) -> list[str]:
    if value is None:
        return []
    return [line.strip() for line in re.split(r"[\r\n]+", str(value)) if line and line.strip()]


def _process_label(row: Any) -> str:
    process_names = _split_non_empty_lines(row.value("工序名称"))
    if process_names:
        return process_names[0]
    return "未知工序"


class ProcessFlowChartExcelAgent(ExcelAgent):
    """过程流程图Excel文件专用审核代理"""

    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART

    def _original_flow_rows(self):
        """Locate original cells when conversion joins distinct process records."""
        source = (self.audit_file.file_metadata or {}).get("source_pdf_url")
        if not source or not Path(source).is_file():
            return None
        cached = getattr(self, "_native_flow_rows", None)
        if cached is not None:
            return cached
        import fitz

        fields = ("工序名称", "操作描述", "过程特殊特性")
        layout = {}
        records = []
        with fitz.open(source) as document:
            for page in document:
                text = re.sub(r"\s+", "", page.get_text())
                words = page.get_text("words")
                cell_text = {}
                if "代码填写供应商代码" in text:
                    continue  # Unfilled instruction/example sheet, not process data.
                for table in page.find_tables().tables:
                    if table.col_count < 10:
                        continue
                    header_bottom = table.bbox[1]
                    headers = {}
                    categories = []
                    rows = table.extract()
                    for index, values in enumerate(rows[:10]):
                        for column, value in enumerate(values):
                            name = re.sub(r"\s+", "", str(value or ""))
                            cell = table.rows[index].cells[column]
                            if cell and name in fields:
                                headers[name] = fitz.Rect(cell)
                            if cell and name == "特性类别":
                                categories.append(fitz.Rect(cell))
                    if len(headers) == len(fields) and len(categories) >= 2:
                        layout = {**headers, "过程特性类别": max(categories, key=lambda cell: cell.x0)}
                        header_bottom = max(cell.y1 for cell in layout.values())
                    if not layout:
                        continue
                    cells = [fitz.Rect(cell) for cell in table.cells if cell]
                    for native_row in table.rows:
                        if native_row.bbox[1] < header_bottom - 0.1:
                            continue
                        # A row bbox includes vertically merged name cells. Its
                        # midpoint can lie exactly on another column's divider.
                        y = native_row.bbox[1] + min(0.1, (native_row.bbox[3] - native_row.bbox[1]) / 2)
                        record = {"page": page.number + 1}
                        located = {}
                        for name, column in layout.items():
                            x = (column.x0 + column.x1) / 2
                            matches = [cell for cell in cells if cell.x0 < x < cell.x1 and cell.y0 < y < cell.y1]
                            if not matches:
                                record[name] = ""
                                continue
                            bounds = min(matches, key=lambda cell: cell.get_area())
                            bounds = bounds & fitz.Rect(column.x0, bounds.y0, column.x1, bounds.y1)
                            located[name] = bounds
                            identity = tuple(bounds)
                            if identity not in cell_text:
                                groups = {}
                                for word in words:
                                    if bounds.x0 < (word[0] + word[2]) / 2 < bounds.x1 and bounds.y0 < (word[1] + word[3]) / 2 < bounds.y1:
                                        groups.setdefault((word[5], word[6]), []).append(word)
                                cell_text[identity] = "\n".join(" ".join(word[4] for word in sorted(group, key=lambda word: word[0])) for group in groups.values())
                            record[name] = cell_text[identity]
                        # The named operation can span several drawing/substep
                        # rows. Read its complete original description group.
                        if "工序名称" in located:
                            name_bounds = located["工序名称"]
                            column = layout["操作描述"]
                            record["操作描述"] = " ".join(word[4] for word in words if column.x0 < (word[0] + word[2]) / 2 < column.x1 and name_bounds.y0 < (word[1] + word[3]) / 2 < name_bounds.y1)
                        if record["工序名称"]:
                            records.append(record)
        self._native_flow_rows = records
        return records

    def _original_flow_check(self, rule_number: int) -> tuple[str, str] | None:
        records = ProcessFlowChartExcelAgent._original_flow_rows(self)
        if records is None:
            return None
        if not records:
            return "UNABLE", "无法确认原PDF流程图的行列对应关系。"
        for record in records:
            if rule_number == 3:
                category = re.sub(r"\s+", "", record["过程特性类别"]).lower()
                if category not in {"【g】", "【z】", "[g]", "[z]", "g", "z"}:
                    continue
                missing = not record["过程特殊特性"]
                field = "过程特殊特性"
            else:
                missing = not record["操作描述"]
                field = "操作描述"
            if missing:
                return "NG", f"原PDF第{record['page']}页工序“{record['工序名称']}”的{field}为空。"
        return "PASS", ""

    @rule("表头关键字段完整性检查")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则1：识别“过程流程图”中是否包含“零件号”、“零件名称”、“项目车型”、“供应商名称”、“代码”、“编制日期”、“版本”、“团队”字段

        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        block = table.field_block("过程流程图头部字段区")

        # 保持历史行为：代码字段不做非空校验，只要求存在。
        # block.field() 找不到字段会自动抛出 GuardStop
        block.field("代码字段")

        required_non_empty_field_names = (
            "零件号字段",
            "零件名称字段",
            "项目车型字段",
            "供应商名称字段",
            "编制日期字段",
            "版本字段",
            "团队字段",
        )
        block.expect(
            *required_non_empty_field_names,
            context="过程流程图头部字段区",
        )

        return "PASS", ""

    @rule("产品特殊特性检查")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则2：识别“过程流程图”中是否包含两个“特性类别”列
        当第一个“特性类别”列下内容为“【G】”或“【Z】”，且对应行的“产品特殊特性”列内容不为空则通过，否则不通过
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")

        section = sheets[0].section("过程流程主数据区")
        columns = section.columns("工序名称", "产品特殊特性", "过程特殊特性")
        if columns["过程特殊特性"].header_col <= columns["产品特殊特性"].header_col + 1:
            return (
                "NG",
                "未识别到第二个特性类别列，请检查“产品特殊特性”和“过程特殊特性”列配置。",
            )

        category_tokens = {"【g】", "【z】", "[g]", "[z]", "g", "z"}
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程主数据区为空表。"
        for row in rows:
            between_values = row.values_between("产品特殊特性", "过程特殊特性")
            has_category = any(re.sub(r"\s+", "", str(value or "")).lower() in category_tokens for value in between_values)
            if has_category:
                row.expect("产品特殊特性", context="特性类别")
        return "PASS", ""

    @rule("过程特殊特性检查")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则3：识别“过程流程图”中是否包含两个“特性类别”列
        当第二个“特性类别”列下内容为“【G】”或“【Z】”，且对应行的“过程特殊特性”列内容不为空则通过，否则不通过
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")

        section = sheets[0].section("过程流程主数据区")
        columns = section.columns("工序名称", "过程特殊特性", optional=("工序号",))
        header_category_cell = section.find_header_right(
            "过程特殊特性",
            max_offset=0,
            use_merged_anchor=True,
        )
        if header_category_cell is None:
            return "NG", "未找到“过程特殊特性”对应的特性类别表头。"
        header_value = header_category_cell.value(compact=True)
        if "特性类别" not in header_value:
            return (
                "NG",
                f"“过程特殊特性”右侧表头不是“特性类别”，当前识别为“{header_value}”。",
            )

        category_tokens = {"【g】", "【z】", "【x】", "[g]", "[z]", "[x]", "g", "z", "x"}
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程主数据区为空表。"
        for row in rows:
            category_cell = row.find_right(
                "过程特殊特性",
                max_offset=0,
                use_merged_anchor=True,
            )
            if category_cell is None:
                continue
            has_category = category_cell.value(compact=True).lower() in category_tokens
            if not has_category:
                continue
            if not row.is_present("过程特殊特性"):
                return (
                    "NG",
                    f"工序“{_process_label(row)}”的第二特性类别命中G/Z/X，但“过程特殊特性”为空。",
                )
            if "工序号" in columns:
                serial_lines = _split_non_empty_lines(row.value("工序号"))
                special_lines = _split_non_empty_lines(row.value("过程特殊特性"))
                if len(serial_lines) >= 2 and len(special_lines) < len(serial_lines):
                    original = ProcessFlowChartExcelAgent._original_flow_check(self, 3)
                    if original is not None:
                        return original
                    return "NG", f"工序“{_process_label(row)}”的多工序记录无法逐项确认过程特殊特性。"
        return "PASS", ""

    @rule("工序名称与操作描述检查")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则4：识别“工序名称”、“操作描述”两列是否存在，有一列不存在则不通过，否则继续识别；
        当“工序名称”不为空且对应行的“操作描述”列内容均不为空则通过，否则不通过
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")
        section = sheets[0].section("过程流程主数据区")
        columns = section.columns("工序名称", "操作描述", optional=("工序号",))
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程主数据区为空表。"
        for row in rows:
            if not row.is_present("工序名称"):
                continue
            if not row.is_present("操作描述"):
                return (
                    "NG",
                    f"工序“{_process_label(row)}”存在“工序名称”但“操作描述”为空。",
                )
            if "工序号" in columns:
                serial_lines = _split_non_empty_lines(row.value("工序号"))
                process_lines = _split_non_empty_lines(row.value("工序名称"))
                operation_lines = _split_non_empty_lines(row.value("操作描述"))
                if len(serial_lines) >= 2 and len(operation_lines) < max(len(serial_lines), len(process_lines)):
                    original = ProcessFlowChartExcelAgent._original_flow_check(self, 4)
                    if original is not None:
                        return original
                    return "NG", f"工序“{_process_label(row)}”的多工序记录无法逐项确认操作描述。"
        return "PASS", ""

    @rule("工序定义完整性检查")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则5：识别“工序名称”、“制造”、“运输”、“存储”、“检验”、“返工”、“报废”七列是否存在，有一列不存在则不通过，否则继续识别；
        当“工序名称”不为空且对应行的“制造”、“运输”、“存储”、“检验”、“返工”、“报废”有一列内容不为空则通过，否则不通过
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")
        section = sheets[0].section("过程流程工序定义区")
        columns = section.columns(
            "工序定义-工序名称",
            "工序定义-制造",
            "工序定义-运输",
            "工序定义-存储",
            "工序定义-检验",
            "工序定义-返工",
            "工序定义-报废",
        )
        process_definition_columns = (
            "工序定义-制造",
            "工序定义-运输",
            "工序定义-存储",
            "工序定义-检验",
            "工序定义-返工",
            "工序定义-报废",
        )

        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程工序定义区为空表。"
        for row in rows:
            if not row.is_present("工序定义-工序名称"):
                continue

            def has_process_definition(_value: object, current_row: Any = row) -> bool:
                return bool(current_row.count_present(*process_definition_columns) > 0)

            row.expect_condition(
                "工序定义-工序名称",
                has_process_definition,
                context="工序名称存在，但“制造/运输/存储/检验/返工/报废”均为空",
            )

        return "PASS", ""

    @rule("工序定义完整性检查（连线）")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则6：识别“工序名称”、“制造”、“运输”、“存储”、“检验”、“返工”、“报废”七列是否存在，有一列不存在则不通过，否则继续识别；
        当“工序名称”不为空且对应行的“制造”、“运输”、“存储”、“检验”、“返工”、“报废”有一列内容不为空则通过，否则不通过（检查连线）
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")
        section = sheets[0].section("过程流程工序定义区")
        ws = section.resolved.ws
        columns = section.columns(
            "工序定义-工序名称",
            "工序定义-制造",
            "工序定义-运输",
            "工序定义-存储",
            "工序定义-检验",
            "工序定义-返工",
            "工序定义-报废",
        )
        process_definition_columns = (
            "工序定义-制造",
            "工序定义-运输",
            "工序定义-存储",
            "工序定义-检验",
            "工序定义-返工",
            "工序定义-报废",
        )

        process_row_columns: dict[int, set[str]] = {}
        process_rows_by_index: dict[int, Any] = {}
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程工序定义区为空表。"
        for row in rows:
            if not row.is_present("工序定义-工序名称"):
                continue
            anchor_cell = row.anchor("工序定义-工序名称")
            if anchor_cell is None:
                continue
            anchor_row = anchor_cell.row_index
            present_columns = {column_name for column_name in process_definition_columns if row.is_present(column_name)}
            process_row_columns.setdefault(anchor_row, set()).update(present_columns)
            process_rows_by_index[anchor_row] = row

        ordered_rows = sorted(process_row_columns.items())
        if not ordered_rows:
            return "NG", "过程流程工序定义区未识别到有效工序行。"

        column_indices = [column.header_col for column in columns.values()]
        connector_rows: set[int] | None = None

        def load_connector_rows() -> set[int]:
            nonlocal connector_rows
            if connector_rows is None:
                process_row_indices = [row_index for row_index, _ in ordered_rows]
                connector_rows = self._rows_with_connector_evidence(
                    ws,
                    row_min=min(process_row_indices),
                    row_max=max(process_row_indices),
                    col_min=min(column_indices),
                    col_max=max(column_indices),
                )
            return connector_rows

        evidence_rows = load_connector_rows()

        def row_is_connected(row_index: int, present_columns: set[str]) -> bool:
            # 严格语义：行有效 = 图例列有命中 或 连线证据覆盖该行
            return bool(present_columns) or row_index in evidence_rows

        unresolved_rows: list[int] = []
        for row_index, present_columns in ordered_rows:
            row_obj = process_rows_by_index.get(row_index)
            if row_obj is None:
                if not row_is_connected(row_index, present_columns):
                    unresolved_rows.append(row_index)
                continue
            try:
                row_obj.expect_condition(
                    "工序定义-工序名称",
                    lambda _value, current_row=row_index, current_columns=present_columns: row_is_connected(
                        current_row,
                        current_columns,
                    ),
                    context="图例列非空或存在连线覆盖",
                )
            except GuardStop:
                unresolved_rows.append(row_index)

        row_breakpoints: list[int] = []
        for index in range(1, len(ordered_rows)):
            prev_row_index, prev_columns = ordered_rows[index - 1]
            row_index, present_columns = ordered_rows[index]

            has_direct_column_overlap = bool(prev_columns and present_columns and prev_columns.intersection(present_columns))
            if has_direct_column_overlap:
                continue

            segment_has_connector_coverage = all(candidate_row in evidence_rows for candidate_row in range(prev_row_index, row_index + 1))
            if not segment_has_connector_coverage:
                row_breakpoints.append(row_index)

        unresolved_rows = sorted(set(unresolved_rows + row_breakpoints))
        if not unresolved_rows:
            return "PASS", ""
        return "NG", f"存在流程断点行: {', '.join(str(r) for r in unresolved_rows[:3])}"

    def _xml_local_name(self, tag: str) -> str:
        return tag.split("}", 1)[-1]

    def _normalize_zip_target(self, base_dir: str, target: str) -> str:
        normalized_target = str(target or "").strip()
        if not normalized_target:
            return ""
        if normalized_target.startswith("/"):
            normalized_target = normalized_target.lstrip("/")
        if normalized_target.startswith("xl/"):
            return posixpath.normpath(normalized_target)

        merged = posixpath.normpath(posixpath.join(base_dir, normalized_target))
        while merged.startswith("../"):
            merged = merged[3:]
        if not merged.startswith("xl/"):
            merged = posixpath.normpath(posixpath.join("xl", merged))
        return merged.lstrip("/")

    def _resolve_sheet_xml_path(self, zf: zipfile.ZipFile, sheet_title: str) -> str:
        ns = {
            "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "pkg_rel": "http://schemas.openxmlformats.org/package/2006/relationships",
        }
        workbook_xml = ET.fromstring(zf.read("xl/workbook.xml"))
        workbook_rels_xml = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib.get("Id", ""): rel.attrib.get("Target", "") for rel in workbook_rels_xml.findall("pkg_rel:Relationship", ns)}

        rel_id_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        for sheet in workbook_xml.findall("main:sheets/main:sheet", ns):
            if sheet.attrib.get("name", "") != sheet_title:
                continue
            rel_id = sheet.attrib.get(rel_id_attr, "")
            target = rel_map.get(rel_id, "")
            resolved = self._normalize_zip_target("xl", target)
            if resolved in zf.namelist():
                return resolved
            break
        return ""

    def _sheet_rels_path(self, sheet_xml_path: str) -> str:
        parent = posixpath.dirname(sheet_xml_path)
        filename = posixpath.basename(sheet_xml_path)
        return posixpath.join(parent, "_rels", f"{filename}.rels")

    def _rows_with_connector_evidence(
        self,
        ws: Worksheet,
        *,
        row_min: int,
        row_max: int,
        col_min: int,
        col_max: int,
    ) -> set[int]:
        """
        Return rows covered by connectors whose endpoints attach to process symbols.
        Fail-closed: parsing failure returns an empty set.
        """
        source_path = str(self._source_path or "")
        if not source_path.lower().endswith((".xlsx", ".xlsm")) or not os.path.exists(source_path):
            return set()

        evidence_rows: set[int] = set()
        excel_ops = DefaultExcelOps()
        ns_rel = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
        drawing_rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ns_xdr = {"xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"}
        try:
            with zipfile.ZipFile(source_path, "r") as zf:
                sheet_xml_path = self._resolve_sheet_xml_path(zf, ws.title)
                if not sheet_xml_path:
                    return set()

                sheet_xml = ET.fromstring(zf.read(sheet_xml_path))
                rels_path = self._sheet_rels_path(sheet_xml_path)
                if rels_path not in zf.namelist():
                    return set()

                rels_xml = ET.fromstring(zf.read(rels_path))
                rel_map = {rel.attrib.get("Id", ""): rel.attrib.get("Target", "") for rel in rels_xml.findall("rel:Relationship", ns_rel)}
                drawing_targets: list[str] = []
                for node in sheet_xml.iter():
                    if self._xml_local_name(node.tag) != "drawing":
                        continue
                    rel_id = node.attrib.get(drawing_rel_attr, "")
                    target = self._normalize_zip_target(posixpath.dirname(sheet_xml_path), rel_map.get(rel_id, ""))
                    if target and target in zf.namelist():
                        drawing_targets.append(target)

                for drawing_path in drawing_targets:
                    drawing_xml = ET.fromstring(zf.read(drawing_path))
                    for anchor in drawing_xml.findall("xdr:twoCellAnchor", ns_xdr):
                        if anchor.find("xdr:cxnSp", ns_xdr) is None:
                            continue
                        from_node = anchor.find("xdr:from", ns_xdr)
                        to_node = anchor.find("xdr:to", ns_xdr)
                        if from_node is None or to_node is None:
                            continue

                        def child_int(node: ET.Element, tag: str) -> int:
                            child = node.find(tag, ns_xdr)
                            return int((child.text if child is not None else None) or "0")

                        from_row = child_int(from_node, "xdr:row") + 1
                        from_col = child_int(from_node, "xdr:col") + 1
                        from_row_offset = child_int(from_node, "xdr:rowOff")
                        from_col_offset = child_int(from_node, "xdr:colOff")
                        to_row = child_int(to_node, "xdr:row") + 1
                        to_col = child_int(to_node, "xdr:col") + 1
                        to_row_offset = child_int(to_node, "xdr:rowOff")
                        to_col_offset = child_int(to_node, "xdr:colOff")

                        connector = anchor.find("xdr:cxnSp", ns_xdr)
                        assert connector is not None
                        ns_transform = {**ns_xdr, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
                        transform = connector.find("xdr:spPr/a:xfrm", ns_transform)
                        flip_h = transform is not None and transform.attrib.get("flipH") in {"1", "true"}
                        flip_v = transform is not None and transform.attrib.get("flipV") in {"1", "true"}

                        first_row = to_row if flip_v else from_row
                        first_row_offset = to_row_offset if flip_v else from_row_offset
                        first_col = to_col if flip_h else from_col
                        first_col_offset = to_col_offset if flip_h else from_col_offset
                        second_row = from_row if flip_v else to_row
                        second_row_offset = from_row_offset if flip_v else to_row_offset
                        second_col = from_col if flip_h else to_col
                        second_col_offset = from_col_offset if flip_h else to_col_offset

                        shape_row_min = min(first_row, second_row)
                        shape_row_max = max(first_row, second_row)
                        shape_col_min = min(first_col, second_col)
                        shape_col_max = max(first_col, second_col)
                        rows_overlap = not (shape_row_max < row_min or shape_row_min > row_max)
                        cols_overlap = not (shape_col_max < col_min or shape_col_min > col_max)
                        if not (rows_overlap and cols_overlap):
                            continue

                        same_symbol_column = first_col == second_col
                        if not (
                            self._connector_endpoint_is_attached(
                                ws,
                                row=first_row,
                                col=first_col,
                                row_offset=first_row_offset,
                                col_offset=first_col_offset,
                                same_symbol_column=same_symbol_column,
                            )
                            and self._connector_endpoint_is_attached(
                                ws,
                                row=second_row,
                                col=second_col,
                                row_offset=second_row_offset,
                                col_offset=second_col_offset,
                                same_symbol_column=same_symbol_column,
                            )
                        ):
                            continue

                        overlap_start = max(row_min, shape_row_min)
                        overlap_end = min(row_max, shape_row_max)
                        for row in range(overlap_start, overlap_end + 1):
                            evidence_rows.add(row)

                        # Merged cells share the same visual endpoint area. If a connector
                        # endpoint lands inside a merged cell within the process-flow area,
                        # treat all rows in that merged range as covered evidence rows.
                        for endpoint_row, endpoint_col in ((first_row, first_col), (second_row, second_col)):
                            if not (row_min <= endpoint_row <= row_max and col_min <= endpoint_col <= col_max):
                                continue
                            merged_endpoint = excel_ops.get_detailed_merge_info(ws, endpoint_row, endpoint_col)
                            if not merged_endpoint:
                                continue
                            if merged_endpoint.max_col < col_min or merged_endpoint.min_col > col_max:
                                continue
                            merged_row_start = max(row_min, merged_endpoint.min_row)
                            merged_row_end = min(row_max, merged_endpoint.max_row)
                            for row in range(merged_row_start, merged_row_end + 1):
                                evidence_rows.add(row)
            return evidence_rows
        except Exception as exc:
            logger.warning("流程图连线解析失败: %s", exc)
            return set()

    def _connector_endpoint_is_attached(
        self,
        ws: Worksheet,
        *,
        row: int,
        col: int,
        row_offset: int,
        col_offset: int,
        same_symbol_column: bool,
    ) -> bool:
        if not DefaultExcelOps().get_effective_cell_value(ws, row, col, strip=True):
            return False

        column_dimension = ws.column_dimensions.get(get_column_letter(col))
        column_width = getattr(column_dimension, "width", None)
        if column_width is None:
            column_width = ws.sheet_format.defaultColWidth or 8.43
        column_emu = pixels_to_EMU(max(1, int(float(column_width) * 7 + 5)))
        if not 0.2 <= col_offset / column_emu <= 0.8:
            return False

        if same_symbol_column:
            return True

        row_height = ws.row_dimensions[row].height or ws.sheet_format.defaultRowHeight or 15
        row_emu = int(pixels_to_EMU(max(1, points_to_pixels(float(row_height)))))
        return bool(0.2 <= row_offset / row_emu <= 0.8)
