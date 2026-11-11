"""工装模具认可报告审核代理（规则1~6）。"""

import logging
import re
from pathlib import Path

from audit.engine.rule_engine import RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

_RULE2_REQUIRED_COLUMNS = (
    "工艺类型",
    "一级分总成零件名称",
    "分总成组件或分序模具名称",
    "模具制造供应商",
    "模具存放地点",
    "零件质量评估",
    "模具分供方及实物VS数据&标准验收",
    "模具量产性能评估",
    "模具维保评估",
    "零件外观符合要求",
    "零件尺寸符合要求",
    "零件性能符合要求",
    "模具分供方是否符合IR报告要求",
    "模具实物符合设计数据",
    "模具是否存在重大加工缺陷如违规烧焊等",
    "实物钢材选型符合模具标准",
    "实物标准件符合模具标准",
    "模具生产节拍符合IR要求",
    "模具生产良率符合IR要求",
    "模具性能符合要求生产平顺无异常",
    "模具总体产能符合IR要求",
    "模具维保手册齐全",
    "模具数据保存完整",
    "模具备件准备充分",
)

_RULE2_REQUIRED_NON_EMPTY_COLUMNS = (
    "一级分总成零件名称",
    "分总成组件或分序模具名称",
    "模具制造供应商",
    "模具存放地点",
    "零件外观符合要求",
    "零件尺寸符合要求",
    "零件性能符合要求",
    "模具分供方是否符合IR报告要求",
    "模具实物符合设计数据",
    "模具是否存在重大加工缺陷如违规烧焊等",
    "实物钢材选型符合模具标准",
    "实物标准件符合模具标准",
    "模具生产节拍符合IR要求",
    "模具生产良率符合IR要求",
    "模具性能符合要求生产平顺无异常",
    "模具总体产能符合IR要求",
    "模具维保手册齐全",
    "模具数据保存完整",
    "模具备件准备充分",
)

_DIMENSION_PATTERN = re.compile(r"^\d+(\.\d+)?×\d+(\.\d+)?×\d+(\.\d+)?$")
_VALID_OBJECT_CONFIRM_VALUES = {"有", "无"}


class ToolingApprovalAgent(ExcelAgent):
    """工装模具认可报告专业审核代理"""

    DOC_TYPE = PpapDocumentType.TOOLING_APPROVAL_REPORT
    EXPECTED_RULE_CATEGORIES = (
        "零件供应商名称与提交日期字段检查",
        "关键列完整性与内容非空检查",
        "模具设计尺寸格式检查",
        "实物照片列检查",
        "编制与审核字段检查",
        "实物确认列内容检查",
    )

    @staticmethod
    def _is_placeholder_signature(value: str) -> bool:
        normalized = str(value or "")
        if "签名" not in normalized:
            return False
        return "管理部门" in normalized or "负责人" in normalized or "合作伙伴" in normalized

    def _read_primary_source_bytes(self, ctx: RuleContext) -> bytes:
        candidates: list[str] = []
        metadata = getattr(self.audit_file, "file_metadata", None)
        if isinstance(metadata, dict):
            source_pdf = str(metadata.get("source_pdf_url", "") or "").strip()
            if source_pdf:
                candidates.append(source_pdf)
        if ctx.source_path:
            candidates.append(ctx.source_path)

        for path in candidates:
            try:
                file_path = Path(path)
                if file_path.exists():
                    return file_path.read_bytes()
            except Exception:
                continue
        return b""

    def _has_signature_image_evidence(self, ctx: RuleContext) -> bool:
        data = self._read_primary_source_bytes(ctx)
        if not data:
            return False
        image_markers = data.count(b"PNG") + data.count(b"JFIF") + data.count(b"JPEG")
        return image_markers >= 3

    @rule("零件供应商名称与提交日期字段检查")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        table.find_sheets("模具验收报告")

        header = table.field_block("模具验收头部字段区")
        header.expect(
            "零件供应商名称字段",
            "提交日期字段",
            context="模具验收头部字段区",
        )
        return "PASS", ""

    @rule("关键列完整性与内容非空检查")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("模具验收报告")

        for sheet in sheets:
            section = sheet.section("模具验收主数据区")
            columns = section.columns(*_RULE2_REQUIRED_COLUMNS)
            has_data_row = False
            for row in section.rows_from(columns):
                process_type = row.value("工艺类型", compact=True).lower()
                if not process_type or "请填写" in process_type:
                    continue
                has_data_row = True
                row.expect(*_RULE2_REQUIRED_NON_EMPTY_COLUMNS, context="工艺类型")
            if not has_data_row:
                return "NG", f"工作表 '{sheet.title}' 未找到任何有效的工艺类型数据行"
        return "PASS", ""

    @rule("模具设计尺寸格式检查")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("模具验收报告")

        for sheet in sheets:
            section = sheet.section("模具验收主数据区")
            columns = section.columns("工艺类型", "模具设计尺寸")
            has_data_row = False
            for row in section.rows_from(columns):
                process_type = row.value("工艺类型", compact=True).lower()
                if not process_type or "请填写" in process_type:
                    continue
                has_data_row = True
                row.expect("模具设计尺寸", context="工艺类型")
                dimension_value = str(row.value("模具设计尺寸", compact=True))

                def matches_dimension(_value: object, captured: str = dimension_value) -> bool:
                    return bool(_DIMENSION_PATTERN.match(captured))

                row.expect_condition(
                    "模具设计尺寸",
                    matches_dimension,
                    context=f"模具设计尺寸格式不正确，命中值[{dimension_value}]，规则要求[长×宽×高数字格式]",
                )
            if not has_data_row:
                return "NG", f"工作表 '{sheet.title}' 未找到任何有效的工艺类型数据行"
        return "PASS", ""

    @rule("实物照片列检查")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("模具验收报告")

        for sheet in sheets:
            sheet.section("模具验收主数据区").columns("实物照片")

        try:
            data = Path(ctx.source_path).read_bytes() if ctx.source_path else b""
        except Exception:
            data = b""
        image_markers = data.count(b"JFIF") + data.count(b"PNG")
        if image_markers < 10:
            return "NG", "实物照片数量不足，请补充清晰的实物照片。"
        return "PASS", ""

    @rule("编制与审核字段检查")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        table.find_sheets("模具验收报告")

        header = table.field_block("模具验收头部字段区")
        has_signature_image = self._has_signature_image_evidence(ctx)
        has_compiler_label = header.exists("编制字段")
        has_reviewer_label = header.exists("审核字段")

        if not has_signature_image:
            header.expect("编制字段", "审核字段", context="模具验收头部字段区")
        else:
            # PDF签名通常以图片存在，文本值可能为空；有图片证据时仅要求字段标签可定位。
            missing_labels: list[str] = []
            if not has_compiler_label:
                missing_labels.append("编制字段")
            if not has_reviewer_label:
                missing_labels.append("审核字段")
            if missing_labels:
                return "NG", f"‘模具验收头部字段区’存在缺失的关键字段标签: {missing_labels}"

        def _has_effective_signature(value: str) -> bool:
            if self._is_placeholder_signature(value) and not has_signature_image:
                return False
            return bool(str(value or "").strip())

        header.expect_condition(
            "编制字段",
            _has_effective_signature,
            context="编制字段有效签名",
        ).expect_condition(
            "审核字段",
            _has_effective_signature,
            context="审核字段有效签名",
        )
        return "PASS", ""

    @rule("实物确认列内容检查")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("模具验收报告")

        def _compact(value: object) -> str:
            return "".join(str(value or "").split()).lower()

        section_spec = ctx.resource.sections["模具验收主数据区"]
        max_blank_streak = max(1, int(section_spec.row_max_blank_streak))
        stop_markers = tuple(marker.strip().lower() for marker in section_spec.row_stop_markers if marker.strip())

        for sheet in sheets:
            section = sheet.section("模具验收主数据区")
            columns = section.columns("工艺类型", "实物确认")
            process_col = columns["工艺类型"]
            confirm_col = columns["实物确认"]
            scan_col_min = max(1, min(process_col.header_col, confirm_col.header_col))
            scan_col_max = min(sheet.ws.max_column, max(process_col.header_col, confirm_col.header_col))
            start_row = min(process_col.data_start_row, confirm_col.data_start_row)
            has_data_row = False
            blank_streak = 0

            for row_index in range(start_row, sheet.ws.max_row + 1):
                row_cells = [sheet.ws.cell(row=row_index, column=col_index).value for col_index in range(scan_col_min, scan_col_max + 1)]
                row_texts = [_compact(value) for value in row_cells]
                if stop_markers and any(any(marker in text for marker in stop_markers) for text in row_texts if text):
                    break

                row_non_empty_count = sum(1 for text in row_texts if text)
                if row_non_empty_count == 0:
                    blank_streak += 1
                    if blank_streak >= max_blank_streak:
                        break
                    continue
                blank_streak = 0

                process_type = _compact(sheet.ws.cell(row=row_index, column=process_col.header_col).value)
                if "请填写" in process_type:
                    continue

                # 兼容PDF转换场景：即使工艺类型为空，只要该行存在业务内容，也应检查实物确认。
                if not process_type and row_non_empty_count < 2:
                    continue

                has_data_row = True
                object_confirm_value = _compact(sheet.ws.cell(row=row_index, column=confirm_col.header_col).value)
                if object_confirm_value in _VALID_OBJECT_CONFIRM_VALUES:
                    continue

                if object_confirm_value:
                    message = f"工作表 {sheet.title} 行号{row_index}列[实物确认]命中值[{object_confirm_value}]不属于允许值['有', '无']"
                else:
                    message = f"工作表 {sheet.title} 行号{row_index} ‘工艺类型’对应行存在空值，空值列: ['实物确认']"

                if ctx.collect_ng_enabled:
                    ctx.record_ng(message)
                else:
                    return "NG", message
            if not has_data_row:
                return "NG", f"工作表 '{sheet.title}' 未找到任何有效的工艺类型数据行"
        return "PASS", ""
