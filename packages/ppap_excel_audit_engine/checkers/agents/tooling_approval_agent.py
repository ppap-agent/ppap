"""工装模具认可报告审核代理（规则1~6）。"""

import logging
import re
import struct
from pathlib import Path
from typing import Any, cast

import fitz
import olefile
from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import RuleContext, Table

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
    # "零件外观符合要求",
    # "零件尺寸符合要求",
    # "零件性能符合要求",
    # "模具分供方是否符合IR报告要求",
    # "模具实物符合设计数据",
    # "模具是否存在重大加工缺陷如违规烧焊等",
    # "实物钢材选型符合模具标准",
    # "实物标准件符合模具标准",
    # "模具生产节拍符合IR要求",
    # "模具生产良率符合IR要求",
    # "模具性能符合要求生产平顺无异常",
    # "模具总体产能符合IR要求",
    # "模具维保手册齐全",
    # "模具数据保存完整",
    # "模具备件准备充分",
)

_RULE2_REQUIRED_NON_EMPTY_COLUMNS = (
    "工艺类型",
    "一级分总成零件名称",
    "分总成组件或分序模具名称",
    "模具制造供应商",
    "模具存放地点",
    "零件质量评估",
    "模具分供方及实物VS数据&标准验收",
    "模具量产性能评估",
    "模具维保评估",
    # "零件外观符合要求",
    # "零件尺寸符合要求",
    # "零件性能符合要求",
    # "模具分供方是否符合IR报告要求",
    # "模具实物符合设计数据",
    # "模具是否存在重大加工缺陷如违规烧焊等",
    # "实物钢材选型符合模具标准",
    # "实物标准件符合模具标准",
    # "模具生产节拍符合IR要求",
    # "模具生产良率符合IR要求",
    # "模具性能符合要求生产平顺无异常",
    # "模具总体产能符合IR要求",
    # "模具维保手册齐全",
    # "模具数据保存完整",
    # "模具备件准备充分",
)

_DIMENSION_PATTERN = re.compile(r"^\d+(\.\d+)?×\d+(\.\d+)?×\d+(\.\d+)?$")
_VALID_OBJECT_CONFIRM_VALUES = {"有", "无"}


class ToolingApprovalAgent(ExcelAgent):
    """工装模具认可报告专业审核代理"""

    DOC_TYPE = PpapDocumentType.TOOLING_APPROVAL_REPORT

    @staticmethod
    def _is_placeholder_signature(value: str) -> bool:
        normalized = str(value or "")
        if "签名" not in normalized:
            return False
        return "管理部门" in normalized or "负责人" in normalized or "合作伙伴" in normalized

    @staticmethod
    def _xls_picture_anchors_by_sheet(source_path: str) -> dict[int, list[tuple[int, int, int, int]]]:
        """Read one-based BIFF picture anchors without treating unrelated images as signatures."""
        path = Path(source_path)
        if path.suffix.lower() != ".xls" or not path.exists():
            return {}

        with olefile.OleFileIO(path) as compound_file:
            stream_name = "Workbook" if compound_file.exists("Workbook") else "Book"
            stream = compound_file.openstream(stream_name).read()

        sheet_index = -1
        active_sheet: int | None = None
        pending_anchors: list[tuple[int, int, int, int]] = []
        anchors_by_sheet: dict[int, list[tuple[int, int, int, int]]] = {}
        position = 0
        while position + 4 <= len(stream):
            record_id, data_length = struct.unpack_from("<HH", stream, position)
            data_start = position + 4
            data_end = data_start + data_length
            if data_end > len(stream):
                break
            data = stream[data_start:data_end]
            position = data_end

            if record_id == 0x0809 and data_length >= 4:  # BOF
                substream_type = struct.unpack_from("<H", data, 2)[0]
                if substream_type == 0x0010:  # worksheet
                    sheet_index += 1
                    active_sheet = sheet_index
                    pending_anchors = []
                continue
            if record_id == 0x000A:  # EOF
                active_sheet = None
                pending_anchors = []
                continue
            if active_sheet is None:
                continue

            if record_id == 0x00EC:  # MSODRAWING
                search_position = 0
                while True:
                    marker_position = data.find(b"\x10\xf0", search_position)
                    if marker_position < 2 or marker_position + 24 > len(data):
                        break
                    payload_length = struct.unpack_from("<I", data, marker_position + 2)[0]
                    if payload_length == 18:
                        _, col_min, _, row_min, _, col_max, _, row_max, _ = struct.unpack_from(
                            "<9H",
                            data,
                            marker_position + 6,
                        )
                        pending_anchors.append(
                            (
                                row_min + 1,
                                col_min + 1,
                                row_max + 1,
                                col_max + 1,
                            )
                        )
                    search_position = marker_position + 2
                continue

            if record_id != 0x005D or data_length < 10 or not pending_anchors:  # OBJ
                continue
            object_type = struct.unpack_from("<H", data, 4)[0]
            anchor = pending_anchors.pop(0)
            if object_type == 8:  # picture
                anchors_by_sheet.setdefault(active_sheet, []).append(anchor)

        return anchors_by_sheet

    def _signature_picture_anchors(self, ctx: RuleContext, ws: object) -> list[tuple[int, int, int, int]]:
        source_path = str(ctx.source_path or "")
        if not source_path.lower().endswith(".xls"):
            return []
        try:
            worksheets = list(getattr(getattr(ws, "parent", None), "worksheets", ()))
            sheet_index = worksheets.index(ws)
            return self._xls_picture_anchors_by_sheet(source_path).get(sheet_index, [])
        except (OSError, ValueError, olefile.OleFileError, struct.error):
            logger.warning("无法读取 .xls 图片锚点，按无签名图片处理: %s", source_path)
            return []

    def _pdf_signature_picture_evidence(self) -> tuple[bool, bool]:
        metadata = getattr(self.audit_file, "file_metadata", None)
        if not isinstance(metadata, dict):
            return False, False
        source_pdf = Path(str(metadata.get("source_pdf_url", "") or ""))
        if source_pdf.suffix.lower() != ".pdf" or not source_pdf.exists():
            return False, False

        compiler_picture = False
        reviewer_picture = False
        try:
            with fitz.open(source_pdf) as document:
                for page in document:
                    page_dict = cast(dict[str, Any], page.get_text("dict"))
                    blocks = cast(list[dict[str, Any]], page_dict.get("blocks", []))
                    spans: list[dict[str, Any]] = [
                        span for block in blocks if block.get("type") == 0 for line in cast(list[dict[str, Any]], block.get("lines", [])) for span in cast(list[dict[str, Any]], line.get("spans", []))
                    ]

                    def bbox(value: object) -> tuple[float, float, float, float]:
                        if not isinstance(value, (list, tuple)) or len(value) < 4:
                            return (0.0, 0.0, 0.0, 0.0)
                        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))

                    image_boxes = [bbox(block.get("bbox")) for block in blocks if block.get("type") == 1]

                    def label_span(*tokens: str) -> dict[str, Any] | None:
                        for span in spans:
                            text = "".join(str(span.get("text", "") or "").lower().split())
                            if any(token in text for token in tokens):
                                return span
                        return None

                    compiler_label = label_span("编制", "edit")
                    reviewer_label = label_span("审核", "check")
                    approval_label = label_span("批准", "approve")
                    if compiler_label is None or reviewer_label is None:
                        continue

                    compiler_box = bbox(compiler_label.get("bbox"))
                    reviewer_box = bbox(reviewer_label.get("bbox"))
                    approval_box = bbox(approval_label.get("bbox")) if approval_label else None

                    def image_in_field_band(label_box: tuple[float, ...], x_max: float) -> bool:
                        x_min = float(label_box[2])
                        y_min = float(label_box[1]) - 3
                        y_max = float(label_box[3]) + 3
                        return any(float(image_box[2]) >= x_min and float(image_box[0]) <= x_max and float(image_box[3]) >= y_min and float(image_box[1]) <= y_max for image_box in image_boxes)

                    compiler_picture = compiler_picture or image_in_field_band(
                        compiler_box,
                        float(reviewer_box[0]),
                    )
                    reviewer_picture = reviewer_picture or image_in_field_band(
                        reviewer_box,
                        approval_box[0] if approval_box else float(page.rect.width),
                    )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("无法读取 PDF 签名图片位置，按无签名图片处理: %s", exc)
            return False, False
        return compiler_picture, reviewer_picture

    def _pdf_has_picture_under_column(self, column_label: str) -> bool:
        metadata = getattr(self.audit_file, "file_metadata", None)
        if not isinstance(metadata, dict):
            return False
        source_pdf = Path(str(metadata.get("source_pdf_url", "") or ""))
        if source_pdf.suffix.lower() != ".pdf" or not source_pdf.exists():
            return False

        try:
            with fitz.open(source_pdf) as document:
                for page in document:
                    page_dict = page.get_text("dict")
                    image_boxes = [tuple(block.get("bbox", (0, 0, 0, 0))) for block in page_dict.get("blocks", []) if block.get("type") == 1]
                    for block in page_dict.get("blocks", []):
                        if block.get("type") != 0:
                            continue
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                text = "".join(str(span.get("text", "") or "").split())
                                if column_label not in text:
                                    continue
                                label_box = tuple(span.get("bbox", (0, 0, 0, 0)))
                                if any(
                                    float(image_box[2]) >= float(label_box[0]) and float(image_box[0]) <= float(label_box[2]) and float(image_box[1]) >= float(label_box[3])
                                    for image_box in image_boxes
                                ):
                                    return True
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("无法读取 PDF 列图片位置，按无图片处理: %s", exc)
        return False

    def _worksheet_has_picture_under_column(
        self,
        ctx: RuleContext,
        ws: object,
        *,
        header_col: int,
        data_start_row: int,
    ) -> bool:
        source_path = str(ctx.source_path or "")
        if source_path.lower().endswith(".xls"):
            return any(row_max >= data_start_row and col_min <= header_col <= col_max for row_min, col_min, row_max, col_max in self._signature_picture_anchors(ctx, ws))

        for image in getattr(ws, "_images", ()):
            anchor = getattr(image, "anchor", None)
            marker = getattr(anchor, "_from", None)
            if marker is None:
                continue
            image_row = int(getattr(marker, "row", -1)) + 1
            image_col = int(getattr(marker, "col", -1)) + 1
            if image_row >= data_start_row and image_col == header_col:
                return True
        return self._pdf_has_picture_under_column("实物照片")

    @rule("名称和日期检查")
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

    @rule("工艺类型及相关字段检查")
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
                    context=f"模具设计尺寸格式不正确（工作表{sheet.title}第{row.row_index}行：{dimension_value}）",
                )
            if not has_data_row:
                return "NG", f"工作表 '{sheet.title}' 未找到任何有效的工艺类型数据行"
        return "PASS", ""

    @rule("实物照片检查")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("模具验收报告")

        for sheet in sheets:
            columns = sheet.section("模具验收主数据区").columns("实物照片")
            photo_column = columns["实物照片"]
            if not self._worksheet_has_picture_under_column(
                ctx,
                sheet.ws,
                header_col=photo_column.header_col,
                data_start_row=photo_column.data_start_row,
            ):
                return "NG", f"工作表 '{sheet.title}' 的实物照片列下未检测到图片。"
        return "PASS", ""

    @rule("编制和审核字段检查")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        table.find_sheets("模具验收报告")

        header = table.field_block("模具验收头部字段区")
        compiler = header.field("编制字段")
        reviewer = header.field("审核字段")
        missing_labels = [name for name, field in (("编制字段", compiler), ("审核字段", reviewer)) if not field.exists()]
        if missing_labels:
            return "NG", f"‘模具验收头部字段区’存在缺失的关键字段标签: {missing_labels}"

        assert compiler.resolved is not None
        assert reviewer.resolved is not None
        picture_anchors = self._signature_picture_anchors(ctx, compiler.resolved.ws)

        def has_picture_in_band(*, row: int, col_min: int, col_max: int) -> bool:
            return any(
                anchor_row_min <= row <= anchor_row_max and anchor_col_max >= col_min and anchor_col_min <= col_max
                for anchor_row_min, anchor_col_min, anchor_row_max, anchor_col_max in picture_anchors
            )

        compiler_picture = has_picture_in_band(
            row=compiler.resolved.label_row,
            col_min=compiler.resolved.label_col + 1,
            col_max=reviewer.resolved.label_col - 1,
        )
        reviewer_picture = has_picture_in_band(
            row=reviewer.resolved.label_row,
            col_min=reviewer.resolved.label_col + 1,
            col_max=reviewer.resolved.label_col + 5,
        )
        pdf_compiler_picture, pdf_reviewer_picture = self._pdf_signature_picture_evidence()
        compiler_picture = compiler_picture or pdf_compiler_picture
        reviewer_picture = reviewer_picture or pdf_reviewer_picture

        def has_effective_signature(value: object, *, picture_present: bool) -> bool:
            text = str(value or "").strip()
            return picture_present or bool(text and not self._is_placeholder_signature(text))

        header.expect_condition(
            "编制字段",
            lambda value: has_effective_signature(value, picture_present=compiler_picture),
            context="编制字段有效签名",
        ).expect_condition(
            "审核字段",
            lambda value: has_effective_signature(value, picture_present=reviewer_picture),
            context="审核字段有效签名",
        )
        return "PASS", ""

    @rule("实物确认检查")
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
