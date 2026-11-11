"""
### 控制计划评审规则

**规则 1: 文件适用阶段检查**
- **检查内容**: 检查文件适用阶段是否正确。
- **判定逻辑**:
    - **PASS**: 适用阶段勾选了“生产”或“SOP”。
    - **NG**: 适用阶段为“样件”、“试生产”、“PP”等其他任何字样。

**规则 2: 特殊特性分类列检查**
- **检查内容**: 检查是否存在“特殊特性分类”列，并验证其内容。
- **判定逻辑**:
    1.  首先，检查是否存在标题为“特殊特性分类”的列。
        - 如果该列不存在，则结果为 **NG**。
    2.  如果该列存在，则检查列中的内容。
        - **PASS**: 内容是“关键特殊特性”、“重要特殊特性”、“NA”、“[G]”或“[Z]”中的任意一个。
        - **NG**: 内容为上述允许值之外的任何其他值。

**规则 3: 特殊特性分类、特性列列检查**
- **检查内容**: 识别是否有“特殊特性分类”、“特性”列，且“特性”列下存在“产品”、“过程”两列。
- **判定逻辑**:
    1.  首先，检查是否存在标题为“特殊特性分类”、“特性”的列，且“特性”列下存在“产品”、“过程”两列。
        - 如果该列不存在，则结果为 **NG**。
    2.  如果该列存在，则检查列中的内容。
        - **PASS**: 当“特殊特性分类”下的内容不为空，且对应行的“产品”、“特性”两列有一列内容不为空。
        - **NG**: 内容为上述允许情况之外的任何其他值。

**规则 4: 特殊特性分类、控制方法列检查**
- **检查内容**: 检查是否存在特殊特性分类、控制方法列，并验证其内容格式。
- **判定逻辑**:
    1.  首先，检查是否存在标题为“特殊特性分类”、“控制方法”的列。
        - 如果该列不存在，则结果为 **NG**。
    2.  如果该列存在，则检查列中的内容。
        - **PASS**: 当“特殊特性分类”下的内容为“关键特殊特性”或“重要特殊特性”或[G]或[Z]，且对应行的“控制方法”列内容不为空。
        - **NG**: 内容为上述允许值之外的任何其他值。

**规则 5: 频率/频次列检查**
- **检查内容**: 检查是否存在“频率”或“频次”列，并验证其内容不为空。
- **判定逻辑**:
    1.  首先，检查是否存在标题为“频率”或“频次”的列。
        - 如果这些列都不存在，则结果为 **NG**。
    2.  如果该列存在，则检查列中的内容。
        - **PASS**: 内容不为空（有填写信息）。
        - **NG**: 内容为空。

**规则 6: 表头关键信息检查**
- **检查内容**: 识别表头区域是否有以下字段：
    控制计划类型、控制计划编号、联系人contact/电话phone、日期date（编制compi.）、
    版本Rev.、页数、页码、零件号、零件名称、多功能小组、公司、公司代码、
    公司批准/日期、其它批准/日期（如需要）、顾客工程批准/日期(如需要）、
    顾客质量批准/日期(如需要)。
    如果字段缺失则不通过；字段存在时继续识别：
    控制计划编号、联系人contact/电话phone、日期date（编制compi.）、版本Rev.、
    页数、页码、零件号、零件名称、多功能小组、公司、公司代码、公司批准/日期、
    其它批准/日期（如需要）、顾客工程批准/日期(如需要）、
    顾客质量批准/日期(如需要)对应内容均不为空，且控制计划类型仅有一个选项被勾选，
    则通过，否则不通过。
- **判定逻辑**:
    1.  首先，检查表头是否包含上述字段。
        - 如果该列不存在，则结果为 **NG**。
    2.  如果该列存在，则检查列中的内容。
        - **PASS**: 内容不为空（有填写信息）。
        - **NG**: 内容为空。
"""

from typing import Any, Tuple

from audit.engine.checkers.control_plan_section_headers import (
    detail_section_header_merge,
    detail_table_bounds,
)
from audit.engine.rule_engine import RuleContext, Table, resolver
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

_SPECIAL_ALLOWED = {"关键特殊特性", "重要特殊特性", "NA", "[G]", "[Z]", "G", "Z"}
_SPECIAL_REQUIRE_CONTROL_METHOD = {"关键特殊特性", "重要特殊特性", "[g]", "[z]", "g", "z"}
_CHECKED_MARKS = ("■", "☑", "✓", "√", "✔")
_UNCHECKED_MARKS = ("□", "☐")
_STAGE_SCAN_ROW_PADDING = 2
_STAGE_SCAN_COL_WIDTH = 40
_STAGE_WINDOW_MAX_ROW = 60
_STAGE_WINDOW_MAX_COL = 40
_STAGE_MARK_NEAR_COL_GAP = 1
_STAGE_FIELD_NAMES = ("控制计划类型字段", "阶段样件字段", "阶段试生产字段", "阶段生产字段")
_STAGE_TOKEN_GROUPS = {
    "sample": (("样件", "sample"), ("样件",), ("sample",), ("ots",)),
    "pre_launch": (("试生产", "pre-launch"), ("试生产",), ("pre-launch",), ("pp",)),
    "production": (("生产", "production"), ("生产",), ("production",), ("sop",)),
}


class ControlPlanAgent(ExcelAgent):
    """Control Plan 审核代理（规则 1-6）。"""

    DOC_TYPE = PpapDocumentType.CONTROL_PLAN

    def _extract_inline_value(self, raw_value: object, aliases: tuple[str, ...]) -> str:
        text = str(raw_value or "").lower()
        if not text:
            return ""
        for alias in aliases:
            alias_key = str(alias or "").lower()
            if alias_key and alias_key in text:
                text = text.replace(alias_key, "")
        return text.replace("：", "").replace(":", "").strip()

    @staticmethod
    def _normalize_stage_text(value: Any) -> str:
        return resolver.normalize_label_text(value)

    def _contains_check_mark(self, compact_text: str) -> bool:
        return any(mark in compact_text for mark in _CHECKED_MARKS)

    def _contains_checkbox_marker(self, compact_text: str) -> bool:
        return any(mark in compact_text for mark in (*_CHECKED_MARKS, *_UNCHECKED_MARKS))

    @staticmethod
    def _normalized_stage_token_groups() -> dict[str, tuple[tuple[str, ...], ...]]:
        normalized: dict[str, tuple[tuple[str, ...], ...]] = {}
        for stage_name, groups in _STAGE_TOKEN_GROUPS.items():
            normalized_groups: list[tuple[str, ...]] = []
            for group in groups:
                normalized_group = tuple(token for token in (resolver.normalize_label_text(item) for item in group) if token)
                if normalized_group:
                    normalized_groups.append(normalized_group)
            normalized[stage_name] = tuple(normalized_groups)
        return normalized

    def _collect_stage_signal_cells(self, block: Any, main_sheet: Any) -> list[tuple[int, int, str]]:
        seen: set[tuple[int, int, str]] = set()
        cells: list[tuple[int, int, str]] = []

        def _append_cell(row: int, col: int, text: str) -> None:
            key = (row, col, text)
            if text and key not in seen:
                seen.add(key)
                cells.append(key)

        for cell in main_sheet.scan_non_empty(
            row_min=1,
            row_max=_STAGE_WINDOW_MAX_ROW,
            col_min=1,
            col_max=_STAGE_WINDOW_MAX_COL,
        ):
            _append_cell(cell.row_index, cell.col_index, self._normalize_stage_text(cell.value()))

        for field_name in _STAGE_FIELD_NAMES:
            field = block.field(field_name)
            resolved = field.resolved
            if resolved is not None:
                ws = resolved.ws
                row_start = max(1, resolved.label_row - _STAGE_SCAN_ROW_PADDING)
                row_end = min(ws.max_row, resolved.label_row + _STAGE_SCAN_ROW_PADDING)
                col_start = max(1, resolved.label_col)
                col_end = min(ws.max_column, resolved.label_col + _STAGE_SCAN_COL_WIDTH)
                for cell in main_sheet.scan_non_empty(
                    row_min=row_start,
                    row_max=row_end,
                    col_min=col_start,
                    col_max=col_end,
                ):
                    _append_cell(cell.row_index, cell.col_index, self._normalize_stage_text(cell.value()))
            _append_cell(0, 0, self._normalize_stage_text(field.value()))

        return cells

    def _is_stage_checked(
        self,
        cells: list[tuple[int, int, str]],
        token_groups: tuple[tuple[str, ...], ...],
        masked_tokens: tuple[str, ...] = (),
    ) -> bool:
        mark_cells = {(row, col) for row, col, text in cells if self._contains_check_mark(text)}
        for row, col, text in cells:
            # “生产”是“试生产”的子串：先去掉包含本阶段词的其他阶段词再匹配。
            option_text = text
            for token in masked_tokens:
                option_text = option_text.replace(token, "")
            if not any(all(token in option_text for token in group) for group in token_groups):
                continue
            if self._contains_check_mark(text):
                return True
            if self._contains_checkbox_marker(text):
                continue
            for mark_row, mark_col in mark_cells:
                if abs(mark_row - row) <= 1 and abs(mark_col - col) <= _STAGE_MARK_NEAR_COL_GAP:
                    return True
        return False

    def _resolve_stage_flags(self, block: Any, main_sheet: Any) -> dict[str, bool]:
        stage_cells = self._collect_stage_signal_cells(block, main_sheet)
        flags = {
            "sample": block.checked_count("阶段样件字段") > 0,
            "pre_launch": block.checked_count("阶段试生产字段") > 0,
            "production": block.checked_count("阶段生产字段") > 0,
        }
        stage_groups = self._normalized_stage_token_groups()
        for stage_name, groups in stage_groups.items():
            if flags[stage_name]:
                continue
            own_tokens = {token for group in groups for token in group}
            masked_tokens = tuple(
                sorted(
                    {
                        other
                        for other_name, other_groups in stage_groups.items()
                        if other_name != stage_name
                        for other_group in other_groups
                        for other in other_group
                        if any(token != other and token in other for token in own_tokens)
                    },
                    key=len,
                    reverse=True,
                )
            )
            flags[stage_name] = self._is_stage_checked(stage_cells, groups, masked_tokens)
        return flags

    def _iter_detail_rows(self, section: Any, columns: dict[str, Any]) -> list[Any]:
        table_left, table_right = detail_table_bounds(
            section.resolved.ws,
            header_row=section.resolved.header_row,
            columns=(column.header_col for column in columns.values()),
        )

        def is_data_row(row: Any) -> bool:
            return (
                detail_section_header_merge(
                    row.ws,
                    row.row_index,
                    table_left=table_left,
                    table_right=table_right,
                )
                is None
            )

        return [row for row in section.rows_from(columns) if is_data_row(row)]

    @rule("文件适用阶段检查")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        main_sheet = table.sheet("控制计划")
        block = table.field_block("控制计划头部字段区")

        stage_flags = self._resolve_stage_flags(block, main_sheet)
        block.expect_condition(
            "控制计划类型字段",
            lambda _value: not (stage_flags["sample"] or stage_flags["pre_launch"]),
            context="不得勾选样件/试生产阶段",
        )
        block.expect_condition(
            "控制计划类型字段",
            lambda _value: stage_flags["production"],
            context="必须勾选生产/SOP阶段",
        )
        return "PASS", "适用阶段勾选正确"

    @rule("特殊特性分类列检查")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        section = table.sheet("控制计划").section("控制计划主数据区")
        # 优先使用工序名称作为行锚点，确保“触发行+特殊特性分类为空”也会被校验到。
        columns = section.columns("特殊特性分类", optional=("工序名称",))
        rows = self._iter_detail_rows(section, columns)
        if not rows:
            return "NG", "控制计划主数据区为空表"

        checked_rows = 0
        for row in rows:
            checked_rows += 1
            row.expect_condition(
                "特殊特性分类",
                lambda value: str(value or "").strip() in _SPECIAL_ALLOWED,
                context=f"特殊特性分类非法值；允许集合为{sorted(_SPECIAL_ALLOWED)}",
            )

        if checked_rows == 0:
            return "NG", "特殊特性分类列无有效值"
        return "PASS", "特殊特性分类列符合要求"

    @rule("特殊特性分类、特性列列检查")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        section = table.sheet("控制计划").section("控制计划主数据区")
        columns = section.columns("特殊特性分类", "产品", "过程")
        rows = self._iter_detail_rows(section, columns)
        if not rows:
            return "PASS", ""
        checked_rows = 0

        for row in rows:
            if not row.is_present("特殊特性分类"):
                continue
            checked_rows += 1
            row.expect_condition(
                "产品",
                lambda _value: row.count_present("产品", "过程") > 0,
                context="特殊特性分类对应行的产品、过程列至少一列有值",
            )

        if checked_rows == 0:
            return "PASS", ""
        return "PASS", ""

    @rule("特殊特性分类、控制方法列检查")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        section = table.sheet("控制计划").section("控制计划主数据区")
        columns = section.columns("特殊特性分类", "控制方法")
        rows = self._iter_detail_rows(section, columns)
        if not rows:
            return "PASS", ""
        checked_rows = 0

        for row in rows:
            special_value = row.value("特殊特性分类", compact=True).lower()
            if special_value not in _SPECIAL_REQUIRE_CONTROL_METHOD:
                continue
            checked_rows += 1
            row.expect("控制方法", context="特殊特性分类")

        if checked_rows == 0:
            return "PASS", ""
        return "PASS", ""

    @rule("频率/频次列检查")
    def rule_5(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        section = table.sheet("控制计划").section("控制计划主数据区")
        columns = section.columns(
            "工序名称",
            "频率频次",
            optional=("特殊特性分类", "产品", "过程", "控制方法"),
        )
        check_cols = [column_name for column_name in ("工序名称", "特殊特性分类", "产品", "过程", "控制方法") if column_name in columns]
        rows = self._iter_detail_rows(section, columns)
        if not rows:
            return "NG", "控制计划主数据区为空表"
        checked_rows = 0

        for row in rows:
            if row.count_present(*check_cols) == 0:
                continue
            checked_rows += 1
            row.expect("频率频次", context="频率/频次")

        if checked_rows == 0:
            return "NG", "未识别到需要校验频率/频次列的数据行"
        return "PASS", ""

    @rule("表头关键信息检查")
    def rule_6(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        main_sheet = table.sheet("控制计划")
        block = table.field_block("控制计划头部字段区")
        required_fields = {
            "控制计划类型": "控制计划类型字段",
            "控制计划编号": "控制计划编号字段",
            "联系人contact/电话phone": "联系人电话字段",
            "日期date（编制compi.）": "日期编制字段",
            "日期date（修订update）": "日期修订字段",
            "版本Rev.": "版本字段",
            "页数": "页数字段",
            "页码": "页码字段",
            "零件号": "零件号字段",
            "零件名称": "零件名称字段",
            "多功能小组": "多功能小组字段",
            "公司": "公司字段",
            "公司代码": "公司代码字段",
            "公司批准/日期": "公司批准日期字段",
            "其它批准/日期（如需要）": "其它批准日期字段",
            "顾客工程批准/日期(如需要）": "顾客工程批准日期字段",
            "顾客质量批准/日期(如需要)": "顾客质量批准日期字段",
        }

        block.expect(*required_fields.values(), context="控制计划头部字段区")
        stage_flags = self._resolve_stage_flags(
            block,
            main_sheet,
        )
        block.expect_condition(
            "控制计划类型字段",
            lambda _value: sum(bool(value) for value in stage_flags.values()) == 1,
            context="控制计划类型字段必须有且仅有一个阶段项被勾选",
        )

        right_fields = [
            "控制计划编号",
            "联系人contact/电话phone",
            "日期date（编制compi.）",
            "日期date（修订update）",
            "页数",
            "页码",
        ]
        for display_name in right_fields:
            field_name = required_fields[display_name]
            block.expect_condition(
                field_name,
                lambda value: str(value or "").strip().lower() not in {"", "none", "na"},
                context=f"{display_name}字段值不能为空",
            )

        inline_fields = {
            "版本Rev.": ("版本rev.", "版本rev", "版本"),
            "零件号": ("零件号partnumber", "零件号"),
            "零件名称": ("零件名称partname", "零件名称"),
            "多功能小组": ("多功能小组mulfunctionteam", "多功能小组"),
            "公司": ("公司company", "公司"),
            "公司代码": ("公司代码companycode", "公司代码"),
            "公司批准/日期": ("公司批准/日期", "companyappr./date", "companyappr/date"),
            "其它批准/日期（如需要）": ("其它批准/日期（如需要）", "其它批准/日期(如需要)", "otherappro./date(ifneed)"),
            "顾客工程批准/日期(如需要）": ("顾客工程批准/日期(如需要）", "customerengneeringappro./date(ifneed)"),
            "顾客质量批准/日期(如需要)": ("顾客质量批准/日期(如需要)", "customerqualityappro./date(ifneed)"),
        }
        for display_name, aliases in inline_fields.items():
            field_name = required_fields[display_name]

            def has_inline_value(value: object, field_aliases: tuple[str, ...] = aliases) -> bool:
                return self._extract_inline_value(value, field_aliases).lower() not in {"", "none", "na"}

            block.expect_condition(
                field_name,
                has_inline_value,
                context=f"{display_name}字段值不能为空",
            )

        return "PASS", ""
