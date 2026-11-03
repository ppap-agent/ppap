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
import re
from bisect import bisect_left, bisect_right
from typing import Any, cast

from audit.checkers.v3.cross_file_extractors import (
    _load_workbook_auto,
    extract_rows_from_workbook,
    normalize_key,
    normalize_text,
)
from audit.engine.rule_engine import RuleContext, Table
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


def _serial_text(row: Any, serial_column: str) -> str:
    serial_lines = _split_non_empty_lines(row.value(serial_column))
    return serial_lines[0] if serial_lines else ""


def _is_parent_process_row(rows: list[Any], index: int, serial_column: str, process_column: str) -> bool:
    serial = _serial_text(rows[index], serial_column)
    if not serial:
        return False
    if "." in serial:
        return False
    if not rows[index].is_present(process_column):
        return False

    child_prefix = f"{serial}."
    for next_row in rows[index + 1 :]:
        next_serial = _serial_text(next_row, serial_column)
        if not next_serial:
            continue
        if next_serial.startswith(child_prefix):
            return True
        if "." not in next_serial:
            return False
    return False


def _normalize_dense(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _contains_any_keyword(value: object, keywords: tuple[str, ...]) -> bool:
    normalized = _normalize_dense(value)
    return any(_normalize_dense(keyword) in normalized for keyword in keywords)


def _display_values(values: list[str], max_items: int = 5) -> str:
    picked = [value for value in values if value][:max_items]
    return "、".join(picked) if picked else "无"


_RULE9_PFD_FIELD_ALIASES: dict[str, list[str]] = {
    "product_char_name": ["产品特殊特性"],
    "product_char_type": ["产品特性类别", "产品特性"],
    "process_char_name": ["过程特殊特性", "过程特性"],
    "process_char_type": ["过程特性类别", "过程特性"],
}

_RULE9_SCL_FIELD_ALIASES: dict[str, list[str]] = {
    "char_class": ["特性分类"],
    "char_description": ["特性描述"],
    "char_value": ["特性值"],
    "special_type": ["特殊特性种类", "特殊特性类别"],
}

_RULE9_PFD_HEADER_VALUE_KEYS = {normalize_key(alias) for aliases in _RULE9_PFD_FIELD_ALIASES.values() for alias in aliases}

_RULE9_SCL_VALID_CHAR_CLASS_KEYS = {
    normalize_key("产品"),
    normalize_key("过程"),
}

_RULE6_START_INSPECTION_KEYWORDS: tuple[str, ...] = (
    "进料检验",
    "来料检验",
    "进货检验",
    "iqc",
    "incoming inspection",
    "receiving inspection",
    "原材料检验",
    "原材料检测",
    "原料检验",
    "原料检测",
    "材料检验",
    "材料检测",
    "外购件检验",
    "外购件检测",
    "外协采购件检验",
    "外协采购件检测",
)

_RULE7_FINAL_SHIPMENT_KEYWORDS: tuple[str, ...] = (
    "出货",
    "发运",
    "发货",
    "fqc",
    "oqc",
    "delivery",
    "shipment",
)

_RULE8_INSPECT_KEYWORDS: tuple[str, ...] = ("检验", "重检")


def _is_start_inspection_process(value: object) -> bool:
    return _contains_any_keyword(value, _RULE6_START_INSPECTION_KEYWORDS) or any(
        re.search(r"(?:原材料|原料|外购件|外协采购件).*?(?:检验|检测)", _normalize_dense(segment)) is not None for segment in re.split(r"[\r\n、，,；;。]+", str(value or ""))
    )


def _is_final_shipment_process(value: object) -> bool:
    return _contains_any_keyword(value, _RULE7_FINAL_SHIPMENT_KEYWORDS)


def _process_name_values(ctx: RuleContext) -> list[str]:
    values = []
    for sheet in Table(ctx).find_sheets("过程流程图"):
        cells = sheet.scan_non_empty(row_min=1, row_max=sheet.ws.max_row, col_min=1, col_max=sheet.ws.max_column)
        # Error cells are unknown observations, not matching process names or headers.
        known_cells = []
        for cell in cells:
            merged = ctx.ops.get_detailed_merge_info(cell.ws, cell.row_index, cell.col_index)
            source = cell.ws.cell(row=merged.min_row if merged else cell.row_index, column=merged.min_col if merged else cell.col_index)
            if getattr(source, "data_type", None) != "e":
                known_cells.append(cell)
        cells = known_cells
        columns: dict[int, int] = {}
        for header in cells:
            if header.exact("工序名称", compact=True):
                columns.setdefault(header.col_index, header.row_index)
        header_columns = sorted(columns)
        for cell in cells:
            if cell.exact("工序名称", compact=True):
                continue
            header_row = columns.get(cell.col_index)
            if header_row is not None and cell.row_index > header_row:
                values.append(str(cell.value()))
                continue
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, cell.row_index, cell.col_index)
            if merged is not None:
                intersecting_columns = header_columns[bisect_left(header_columns, merged.min_col) : bisect_right(header_columns, merged.max_col)]
                if any(columns[column] < merged.max_row for column in intersecting_columns):
                    values.append(str(cell.value()))
    return values


class ProcessFlowChartExcelAgent(ExcelAgent):
    """过程流程图Excel文件专用审核代理"""

    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    POSITIONAL_CLAUSE_CHECKS = True
    EXPECTED_RULE_CATEGORIES = (
        "表头字段完整性检查",
        "产品特殊特性及类别填写逻辑校验",
        "过程特殊特性及类别填写逻辑校验",
        "工序名称与操作描述填写逻辑校验",
        "工序名称与图例填写逻辑校验",
        "起始工序拆解完整性识别",
        "最终工序拆解完整性识别",
        "返工/返修工序检验环节识别",
        "跨文件一致性审核（PFD与特殊特性清单）",
    )

    @staticmethod
    def _find_special_characteristic_list_worksheet(workbook: Any) -> object | None:
        if not hasattr(workbook, "sheetnames"):
            return None
        for sheet_name in workbook.sheetnames:
            if "特殊特性清单" in str(sheet_name):
                return cast(object, workbook[sheet_name])
        return None

    @staticmethod
    def _is_valid_rule9_scl_row(row: dict[str, str]) -> bool:
        char_class_key = normalize_key(row.get("char_class", ""))
        if char_class_key not in _RULE9_SCL_VALID_CHAR_CLASS_KEYS:
            return False
        char_desc = normalize_text(row.get("char_description", ""))
        char_val = normalize_text(row.get("char_value", ""))
        return bool(char_desc or char_val)

    @staticmethod
    def _is_rule9_pfd_header_pair(*values: str) -> bool:
        value_keys = [normalize_key(value) for value in values if normalize_key(value)]
        return bool(value_keys) and all(value in _RULE9_PFD_HEADER_VALUE_KEYS for value in value_keys)

    def _note_extracted_cells(self, ctx: RuleContext, file_version_id: str):
        def note(sheet: str, row: int, column: int) -> None:
            ctx.note_cell(sheet, row, column, file_version_id=file_version_id)

        return note

    def _extract_rule9_scl_rows(self, ctx: RuleContext) -> tuple[list[dict[str, str]], str]:
        related_files = self.get_related_files(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        if not related_files:
            return [], "缺少特殊特性清单，请补充"

        rows: list[dict[str, str]] = []
        for related in related_files:
            file_url = str(getattr(related, "file_url", "") or "")
            if not file_url:
                return [], "特殊特性清单文件无法读取"
            workbook = _load_workbook_auto(file_url)
            try:
                if self._find_special_characteristic_list_worksheet(workbook) is None:
                    return [], "未找到特殊特性清单表"

                metadata = getattr(related, "file_metadata", {}) or {}
                version = str(metadata.get("file_version_id") or getattr(related, "audit_id", "") or "")
                ctx.bind_workbook(workbook, version)
                extracted_rows = extract_rows_from_workbook(
                    workbook=workbook,
                    field_aliases=_RULE9_SCL_FIELD_ALIASES,
                    required_fields=("char_class", "char_description", "char_value", "special_type"),
                    note_cell=self._note_extracted_cells(ctx, version),
                )
                if not extracted_rows:
                    return [], "特殊特性清单无法识别必要特性列结构"

                rows.extend(row for row in extracted_rows if self._is_valid_rule9_scl_row(row))
            finally:
                close = getattr(workbook, "close", None)
                if callable(close):
                    close()

        if not rows:
            return [], "特殊特性清单未提取到有效特性项"
        return rows, ""

    @rule("表头字段完整性检查", clause_id="1")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则1：识别“过程流程图”中是否包含“零件号”、“零件名称”、“项目车型”、“供应商名称”、“代码”、“编制日期”、“修订日期”、“版本”、“团队”字段

        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        block = table.field_block("过程流程图头部字段区")

        required_non_empty_field_names = (
            "零件号字段",
            "零件名称字段",
            "项目车型字段",
            "供应商名称字段",
            "代码字段",
            "编制日期字段",
            "修订日期字段",
            "版本字段",
            "团队字段",
        )
        block.expect(
            *required_non_empty_field_names,
            context="过程流程图头部字段区",
        )

        return "PASS", ""

    @rule("产品特殊特性及类别填写逻辑校验", clause_id="2")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则2：识别“过程流程图”中是否包含“产品特殊特性”“产品特性类别”两列；
        当“产品特性类别”列下内容不为空时，对应行的“产品特殊特性”列内容也必须不为空。
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")

        section = sheets[0].section("过程流程主数据区")
        columns = section.columns("产品特殊特性", "产品特性类别")
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程主数据区为空表。"
        for row in rows:
            if row.is_present("产品特性类别") and not row.is_present("产品特殊特性"):
                return (
                    "NG",
                    f"工作表 {row.sheet_title} 行号{row.row_index} “产品特性类别”不为空，但“产品特殊特性”为空。",
                )
        return "PASS", ""

    @rule("过程特殊特性及类别填写逻辑校验", clause_id="3")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        """
        规则3：识别“过程流程图”中是否包含“过程特殊特性”“过程特性类别”两列；
        当“过程特性类别”列下内容不为空时，对应行的“过程特殊特性”列内容也必须不为空。
        Args:
            ctx: RuleContext

        Returns:
            tuple: (bool, str) 是否通过，及原因
        """
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")

        section = sheets[0].section("过程流程主数据区")
        columns = section.columns("过程特殊特性", "过程特性类别")
        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "过程流程主数据区为空表。"
        for row in rows:
            if row.is_present("过程特性类别") and not row.is_present("过程特殊特性"):
                return (
                    "NG",
                    f"工作表 {row.sheet_title} 行号{row.row_index} “过程特性类别”不为空，但“过程特殊特性”为空。",
                )
        return "PASS", ""

    @rule("工序名称与操作描述填写逻辑校验", clause_id="4")
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
        for index, row in enumerate(rows):
            if not row.is_present("工序名称"):
                continue
            if "工序号" in columns and _is_parent_process_row(rows, index, "工序号", "工序名称"):
                ctx.note_build(
                    kind="exclusion",
                    location=f"{row.sheet_title}!{row.row_index}",
                    basis="父行已有子工序，不单独作为工序成员",
                )
                continue
            if not row.is_present("操作描述"):
                return (
                    "NG",
                    f"工序“{_process_label(row)}”存在“工序名称”但“操作描述”为空。",
                )
            if "工序号" not in columns:
                continue
            serial_lines = _split_non_empty_lines(row.value("工序号"))
            if len(serial_lines) < 2:
                continue
            process_lines = _split_non_empty_lines(row.value("工序名称"))
            operation_lines = _split_non_empty_lines(row.value("操作描述"))
            expected_lines = max(len(serial_lines), len(process_lines))
            if len(operation_lines) < expected_lines:
                return (
                    "NG",
                    (f"工序“{_process_label(row)}”疑似多工序合并（工序号{len(serial_lines)}条），但“操作描述”仅识别到{len(operation_lines)}条，存在缺失。"),
                )
        return "PASS", ""

    @rule("工序名称与图例填写逻辑校验", clause_id="5")
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
            optional=("工序定义-工序号",),
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
        for index, row in enumerate(rows):
            if not row.is_present("工序定义-工序名称"):
                continue
            if "工序定义-工序号" in columns and _is_parent_process_row(
                rows,
                index,
                "工序定义-工序号",
                "工序定义-工序名称",
            ):
                ctx.note_build(
                    kind="exclusion",
                    location=f"{row.sheet_title}!{row.row_index}",
                    basis="父行已有子工序，不单独作为工序成员",
                )
                continue

            def has_process_definition(_value: object, current_row: Any = row) -> bool:
                return bool(current_row.count_present(*process_definition_columns) > 0)

            row.expect_condition(
                "工序定义-工序名称",
                has_process_definition,
                context="工序名称存在，但“制造/运输/存储/检验/返工/报废”均为空",
            )

        return "PASS", ""

    @rule("起始工序拆解完整性识别", clause_id="6")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.require_one()
        process_names = _process_name_values(ctx)
        if any(_is_start_inspection_process(value) for value in process_names):
            ctx.mark_witness()
            return "PASS", ""
        if not process_names:
            return "NG", "工序名称列未识别到有效工序。"
        samples = "；".join(f"工序“{value}”" for value in process_names[:6])
        return "NG", f"工序名称列下未识别到进料检验、来料/进货检验或IQC内容（候选：{samples}）。"

    @rule("最终工序拆解完整性识别", clause_id="7")
    def rule_7(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.require_one()
        process_names = _process_name_values(ctx)
        if any(_is_final_shipment_process(value) for value in process_names):
            ctx.mark_witness()
            return "PASS", ""
        if not process_names:
            return "NG", "工序名称列未识别到有效工序。"
        return "NG", "工序名称列中未包含出货/发运、FQC或OQC内容。"

    @rule("返工/返修工序检验环节识别", clause_id="8")
    def rule_8(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("过程流程图")
        rows_by_sheet = []
        for sheet in sheets:
            section = sheet.section("过程流程主数据区")
            columns = section.columns("工序名称")
            rows = [row for row in section.rows_from(columns) if row.is_present("工序名称")]
            rows_by_sheet.append(rows)
        if not any(rows_by_sheet):
            return "NG", "工序名称列未识别到有效工序。"

        for rows in rows_by_sheet:
            for idx, row in enumerate(rows):
                process_name = str(row.value("工序名称") or "")
                if "返工" not in process_name and "返修" not in process_name:
                    continue
                if any(keyword in process_name for keyword in _RULE8_INSPECT_KEYWORDS):
                    continue
                if idx + 1 >= len(rows):
                    return "NG", f"工序“{process_name}”命中返工/返修语义，但无后续工序。"
                next_process_name = str(rows[idx + 1].value("工序名称") or "")
                if any(keyword in next_process_name for keyword in _RULE8_INSPECT_KEYWORDS):
                    continue
                return "NG", f"工序“{process_name}”命中返工/返修语义，但下一行不是检验或重检。"
        return "PASS", ""

    @rule("跨文件一致性审核（PFD与特殊特性清单）", clause_id="9")
    def rule_9(self, ctx: RuleContext) -> tuple[str, str]:
        pfd_rows = extract_rows_from_workbook(
            workbook=ctx.workbook,
            field_aliases=_RULE9_PFD_FIELD_ALIASES,
            required_fields=tuple(_RULE9_PFD_FIELD_ALIASES),
            note_cell=self._note_extracted_cells(ctx, self._file_version_label()),
        )
        if not pfd_rows:
            return "NG", "PFD表无法识别特殊特性列结构。"

        scl_rows, error = self._extract_rule9_scl_rows(ctx)
        if error:
            return "NG", error

        pfd_keys: set[str] = set()
        for row in pfd_rows:
            product_name = normalize_text(row.get("product_char_name", ""))
            product_type = normalize_text(row.get("product_char_type", ""))
            process_name = normalize_text(row.get("process_char_name", ""))
            process_type = normalize_text(row.get("process_char_type", ""))

            if product_type and product_name and not self._is_rule9_pfd_header_pair(product_type, product_name):
                pfd_keys.add(normalize_key(f"产品|{product_type}|{product_name}"))
            if process_type and process_name and not self._is_rule9_pfd_header_pair(process_type, process_name):
                pfd_keys.add(normalize_key(f"过程|{process_type}|{process_name}"))

        missing_items: list[str] = []
        for row in scl_rows:
            char_class = normalize_text(row.get("char_class", ""))
            special_type = normalize_text(row.get("special_type", ""))
            char_desc = normalize_text(row.get("char_description", ""))
            char_val = normalize_text(row.get("char_value", ""))
            if not char_desc and not char_val:
                continue

            feature_text = f"{char_desc}：{char_val}".strip("：")
            evidence = f"【{char_class}】【{special_type}】{feature_text}".strip("：")
            key = normalize_key(f"{char_class}|{special_type}|{feature_text}")
            if key and key not in pfd_keys:
                missing_items.append(evidence)

        ctx.record_execution()
        if missing_items:
            return "NG", f"特殊特性清单项目未在过程流程图覆盖：{_display_values(missing_items)}。"
        return "PASS", ""
