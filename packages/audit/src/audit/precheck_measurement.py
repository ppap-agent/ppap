"""NIO measurement preparation facts, separate from engineering thresholds."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from openpyxl.utils.cell import get_column_letter

from audit.checkers.v3.dimension_measurement_excel_agent import _is_exact_label_value
from audit.contracts import AuditEvidence, EvidenceSource
from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Decision, GuardStop, Table
from audit.precheck_facts import checker_fact, state_fact
from audit.precheck_sources import l1_criteria


def compact(value):
    return "".join(unicodedata.normalize("NFKC", str(value if value is not None else "")).split()).casefold()


def numeric(value):
    if value is None or not str(value).strip():
        raise GuardStop(Decision.UNABLE, "数值为空，尚不能执行本项复算。")
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(unicodedata.normalize("NFKC", str(value)).strip())
        if not result.is_finite():
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise GuardStop(Decision.NG, "判定依据数值含不可解析的已知文本。") from None


_EXTRA_FIELDS = {
    "供应商": ("供应商", "零件供应商", "Part Supplier", "Supplier"),
    "测量员": ("测量员", "Measured by", "Measure By"),
    "版本": ("版本", "版本号", "版本 Rev", "Rev", "Revision"),
    "批次": ("批次号", "批次", "提交批次"),
    "提交日期": ("提交日期", "Submission date"),
    "车型与阶段": ("车型与阶段", "车型/阶段", "model_phase"),
    "签字区": ("签字栏", "签字栏 Signature", "Signature"),
    "工程师": ("责任工程师", "Engineer"),
    "客户": ("客户名称", "客户", "Customer"),
    "报告日期": ("报告日期", "Report date"),
}
_PLACEHOLDERS = {"", "0", "/", "-", "—", "na", "n/a", "tbd", "xxx", "xx", "待填", "待定", "____"}
_ERRORS = {"#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NAME?", "#NUM!", "#NULL!"}


def _fields(checker):
    return {**{name: tuple(config["aliases"]) for name, config in checker.COVER_FIELD_CONFIG.items()}, **_EXTRA_FIELDS}


def _source_value(checker, ctx, sheet_name, row, column, seen=None):
    seen = set() if seen is None else seen
    identity = (sheet_name, row, column)
    if identity in seen or len(seen) >= 16:
        raise GuardStop(Decision.UNABLE, "元信息引用链循环或超出有界回溯范围。")
    seen.add(identity)
    value = Table(ctx).sheet(sheet_name).cell(row, column).value()
    native = getattr(checker, "_native_source_tables", {}).get(sheet_name)
    if native is None:
        return value
    item = effective(native, row, column)
    formula = item.get("formula")
    if not formula:
        return value
    if item.get("state") != "known":
        raise GuardStop(Decision.UNABLE, "元信息公式无可读缓存，未推断其结果。")
    match = re.fullmatch(r"=\s*(?:(?:'((?:[^']|'')+)'|([^'!]+))!)?\$?([A-Z]+)\$?(\d+)\s*", str(formula), re.I)
    if not match:
        raise GuardStop(Decision.UNABLE, "元信息公式不是可安全回溯的单一内部引用，未采信未经回溯的缓存。")
    name = (match.group(1) or match.group(2) or sheet_name).replace("''", "'")
    if name not in ctx.workbook.sheetnames or "[" in name:
        raise GuardStop(Decision.UNABLE, "元信息引用目标不在当前冻结原件内。")
    from openpyxl.utils.cell import coordinate_to_tuple

    target_row, target_column = coordinate_to_tuple(match.group(3).upper() + match.group(4))
    return _source_value(checker, ctx, name, target_row, target_column, seen)


def _extra_columns(checker, ctx, sheet, names):
    aliases = {"公差带": ("公差带", "tolerance range", "tol_band"), "均值": ("均值", "平均值", "Mean", "Average"), "极差": ("极差", "Rng"), "状态": ("状态", "Status", "判定状态")}
    result = {}
    for name in names:
        found = {}
        for cells in sheet.ws.iter_rows(max_row=min(12, sheet.ws.max_row), max_col=min(80, sheet.ws.max_column)):
            for raw in cells:
                if _is_exact_label_value(raw.value, aliases[name]):
                    span = ctx.ops.get_detailed_merge_info(sheet.ws, raw.row, raw.column)
                    found[(span.min_row if span else raw.row, span.min_col if span else raw.column)] = raw.column
                    sheet.cell(raw.row, raw.column).value()
        if len(found) > 1:
            raise GuardStop(Decision.UNABLE, "同一页有多个" + name + "表头，需确认语义范围。")
        if found:
            result[name] = next(iter(found.values()))
    return result


def _cover_values(checker, ctx, field, *, sheet_name="CoverSheet", required=True):
    fields = _fields(checker)
    sheets = [Table(ctx).sheet(sheet_name)] if sheet_name in ctx.workbook.sheetnames else []
    if len(sheets) != 1:
        raise GuardStop(Decision.UNABLE, "未定位唯一" + sheet_name + "，须核对受控模板（S05）。")
    sheet = sheets[0]
    if sheet.ws.max_row > 120 or sheet.ws.max_column > 80:
        raise GuardStop(Decision.UNABLE, "表头超出有界字段定位范围。")
    labels = []
    for cells in sheet.ws.iter_rows():
        for raw in cells:
            for name, aliases in fields.items():
                if _is_exact_label_value(raw.value, aliases):
                    span = ctx.ops.get_detailed_merge_info(sheet.ws, raw.row, raw.column)
                    labels.append((name, raw.row, raw.column, span.max_row if span else raw.row, span.max_col if span else raw.column))
    candidates = []
    matched = False

    def is_label(value):
        return any(_is_exact_label_value(value, aliases) for aliases in fields.values())

    for name, row, column, bottom, right in labels:
        if name != field:
            continue
        matched = True
        sheet.cell(row, column).value()
        value = _source_value(checker, ctx, sheet_name, row, right + 1)
        if value is not None and str(value).strip() and not is_label(value):
            candidates.append(value)
            continue
        column_form = any(other_column == column and other_row != row and abs(other_row - row) <= 12 and other_name != field for other_name, other_row, other_column, _bottom, _right in labels)
        header_row = any(other_row == row and (other_column == right + 1 or other_right + 1 == column) for _name, other_row, other_column, _bottom, other_right in labels)
        if _is_exact_label_value(_source_value(checker, ctx, sheet_name, bottom + 1, column), fields[field]):
            candidates.append(_source_value(checker, ctx, sheet_name, bottom + 2, column))
        elif header_row:
            translated = _source_value(checker, ctx, sheet_name, bottom + 1, column)
            if _is_exact_label_value(translated, fields[field]):
                translated = _source_value(checker, ctx, sheet_name, bottom + 2, column)
            candidates.append(None if is_label(translated) else translated)
        elif column_form:
            candidates.append(None)
        elif _is_exact_label_value(_source_value(checker, ctx, sheet_name, bottom + 1, column), fields[field]):
            candidates.append(_source_value(checker, ctx, sheet_name, bottom + 2, column))
        elif header_row:
            candidates.append(_source_value(checker, ctx, sheet_name, bottom + 1, column))
        else:
            # A single isolated label can be a vertical field. An unrelated
            # note is never accepted as its value without a header layout.
            candidates.append(None)
    if not matched:
        raise GuardStop(Decision.UNABLE, "未定位" + field + "字段；须核对受控模板结构（S05），不能推定供应商删栏。")
    values = {compact(value) for value in candidates if value is not None and str(value).strip()}
    if len(values) > 1:
        raise GuardStop(Decision.UNABLE, field + "有多个不同值，尚不能任取一处。")
    if required and not values:
        raise GuardStop(Decision.NG, field + "字段已定位但实际内容为空。")
    return candidates


def _cover_one(checker, ctx, field, *, sheet_name="CoverSheet", required=True):
    values = _cover_values(checker, ctx, field, sheet_name=sheet_name, required=required)
    return next((value for value in values if value is not None and str(value).strip()), None)


def _native_error_fact(checker, source_id, *, derived=False):
    """Known Excel error strings differ from an unknown formula cache."""
    ctx = None
    sources: list[dict] = []
    unknown = False
    failure = None
    try:
        ctx = checker.rule_ctx(checker._load_workbook())
        native = getattr(checker, "_native_source_tables", {})
        for sheet, row, columns in checker._rows(ctx, "上公差", "下公差", "名义值", "测量偏差值", "Centering", "Range", optional=("IQG",)):
            table = native.get(sheet.ws.title)
            if table is None:
                raise ValueError("native_original_unavailable")
            selected = (
                [columns[name].header_col for name in ("Centering", "Range")]
                if derived
                else [columns[name].header_col for name in ("上公差", "下公差", "名义值")]
                + list(range(columns["测量偏差值"].header_col, columns["IQG" if "IQG" in columns else "Centering"].header_col))
            )
            for column in selected:
                address = get_column_letter(column) + str(row.row_index)
                item = effective(table, row.row_index, column)
                value = item.get("value")
                error = str(value or "").upper() in _ERRORS
                source = {
                    "kind": "structure",
                    "locator": json.dumps({"kind": "xlsx_cell", "sheet": sheet.ws.title, "cell": address, "file_version_id": checker.audit_file.audit_id}, ensure_ascii=False, separators=(",", ":")),
                    "excerpt": str(value if value is not None else "空单元格")[:500],
                }
                if len(sources) < 100 or error:
                    sources.append(source)
                if error:
                    failure = "派生统计列含明确Excel错误，可置空重算。" if derived else "判定依据列含明确Excel错误，尚不能作为数值依据。"
                    break
                if item.get("state") != "known":
                    unknown = True
            if failure:
                break
        status = "defect" if failure else "unknown" if unknown else "satisfied"
        evidence = AuditEvidence(
            required="structure",
            used="structure" if sources else "none",
            source_format=cast(Any, Path(checker.audit_file.file_url).suffix.lower().lstrip(".")),
            prepared_format=cast(Any, "xlsm" if Path(checker.audit_file.file_url).suffix.lower() == ".xlsm" else "xlsx"),
            sources=tuple(EvidenceSource.model_validate(source) for source in sources),
        ).model_dump(mode="json")
        return {
            **state_fact(
                source_id,
                "W05" if derived and failure else "B05" if failure else None,
                failure or "存在未计算公式，尚不能核实本项。" if unknown else failure or "已核实本项被审列，无明确Excel错误。",
                status=status,
            ),
            "evidence": evidence,
            "certainty": "confirmed" if failure else "unknown",
        }
    except Exception:
        return state_fact(source_id, None, "原件本项错误值事实尚未取得；未把未计算公式或读取失败当供应商缺陷。", status="unknown")


def execute(checker, entry="N01"):
    if entry != "N01":
        raise ValueError("measurement_customer_not_supported")

    def done(ctx):
        ctx.mark_scope_complete()
        return "PASS", "已核实本项准备事实；不构成工程合规结论。"

    def rows(ctx, *fields, optional=()):
        return checker._rows(ctx, *fields, optional=optional)

    def family(ctx):
        workbook = ctx.workbook
        values = [
            compact(cell.value)
            for sheet in workbook.worksheets
            for cells in sheet.iter_rows(max_row=min(sheet.max_row, 40), max_col=min(sheet.max_column, 80))
            for cell in cells
            if cell.value is not None
        ]
        signatures = [
            "CoverSheet" in workbook.sheetnames and any(sheet.title.casefold().startswith(("datum-", "gap&flush-")) for sheet in workbook.worksheets),
            all(any(re.fullmatch(r"part" + str(index), value) for value in values) for index in range(1, 6)),
            any(value == "centering" for value in values) and any(value == "range" for value in values),
        ]
        if sum(signatures) < 2:
            raise GuardStop(Decision.UNABLE, "原件族签名不足两项，NIO族特定检查尚不能执行；客户选择不替代族识别。")

    def c01(ctx):
        for _, row, columns in rows(ctx, "上公差", "下公差", "测量偏差值", "Centering", optional=("IQG",)):
            for field in ("上公差", "下公差"):
                if compact(row.value(field)) not in {"", "na", "n/a"}:
                    numeric(row.value(field))
            for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering"):
                if value is not None and str(value).strip():
                    numeric(value)
        return done(ctx)

    def c02(ctx):
        for _, row, _ in rows(ctx, "上公差", "下公差"):
            if any(compact(row.value(field)) in {"na", "n/a"} for field in ("上公差", "下公差")):
                continue
            if numeric(row.value("上公差")) < numeric(row.value("下公差")):
                return "NG", "上公差小于下公差，公差方向颠倒。"
        return done(ctx)

    def c03(ctx):
        count = 0
        for _, row, _ in rows(ctx, "上公差", "下公差"):
            if any(compact(row.value(field)) in {"na", "n/a"} for field in ("上公差", "下公差")):
                continue
            count += numeric(row.value("上公差")) - numeric(row.value("下公差")) > 0
        return done(ctx) if count else ("NG", "整表没有公差带大于零的可复算测点；个别零宽度行未单独记问题。")

    def c04(ctx):
        for _, row, columns in rows(ctx, "坐标X", "坐标Y", "坐标Z", "上公差", "下公差", "测量偏差值", "Centering", optional=("IQG",)):
            if any(abs(numeric(row.value(field))) >= Decimal("1e5") for field in ("坐标X", "坐标Y", "坐标Z")):
                return "NG", "坐标绝对值达到1e5 mm，量级异常。"
            if any(compact(row.value(field)) in {"na", "n/a"} for field in ("上公差", "下公差")):
                continue
            width = numeric(row.value("上公差")) - numeric(row.value("下公差"))
            if width > 0 and any(abs(numeric(value)) > width * 10 for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")):
                return "NG", "偏差绝对值超过正公差带十倍，量级异常。"
        return done(ctx)

    def c05(ctx):
        seen = set()
        for sheet, row, _ in rows(ctx):
            identity = (sheet.ws.title, compact(row.value("测点号")))
            if identity in seen:
                return "NG", f"同页测点号重复：{row.value('测点号')}。"
            seen.add(identity)
        return done(ctx)

    def c06(ctx):
        values = [compact(row.value("类型")) for _, row, _ in rows(ctx, "类型") if compact(row.value("类型"))]
        if not values:
            return "UNABLE", "类型列没有可核实的值；必填空值另由M-05记录。"
        invalid = [value for value in values if value not in {"cc", "sc", "ac"}]
        if invalid:
            return "NG", "类型列出现NIO的CC/SC/AC值域之外的内容。"
        return done(ctx)

    def c07(ctx):
        family(ctx)
        allowed = {"x", "y", "z", "nd", "gap", "flush", "nd-", "gap-", "flush-"}
        for _, row, _ in rows(ctx, "主要控制方向"):
            if compact(row.value("主要控制方向")) not in allowed:
                return "NG", "方向码超出NIO九值集合；未进行跨族近似映射。"
        return done(ctx)

    def m05(ctx):
        fields = ("坐标X", "坐标Y", "坐标Z", "上公差", "下公差", "主要控制方向", "类型")
        for _, row, _ in rows(ctx, *fields):
            if row.missing(*fields):
                return "NG", f"测点行{row.row_index}存在可确定的必填空值。"
        return done(ctx)

    def m07(ctx, *, require_minimum=True):
        family(ctx)
        for _, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            values = row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")
            actual = sum(value is not None and str(value).strip() != "" and compact(value) not in _PLACEHOLDERS - {"0"} for value in values)
            if require_minimum and actual < 5:
                return "NG", f"测点行{row.row_index}实填{actual}件，少于NIO最少5件。"
            if not require_minimum and actual >= 5 and actual < len(values):
                return "NG", f"测点行{row.row_index}实填列数少于声明列数；已达最少5件，不重复记M-07。"
        return done(ctx)

    def m08(ctx):
        any_data = False
        for _, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            values = row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")
            any_data |= any(value is not None and str(value).strip() for value in values)
        return done(ctx) if any_data else ("NG", "没有逐件实测原料，不能仅依赖均值或派生统计列。")

    def s06(ctx):
        family(ctx)
        applied = False
        for _, row, columns in rows(ctx, "上公差", "下公差", "测量偏差值", "Centering", "Range", optional=("IQG",)):
            if any(compact(row.value(field)) in {"na", "n/a"} for field in ("上公差", "下公差")):
                continue
            width = numeric(row.value("上公差")) - numeric(row.value("下公差"))
            if width <= 0:
                continue
            values = [numeric(value) for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering") if value is not None and str(value).strip()]
            if not values:
                continue
            applied = True
            # NIO observes deviations: nominal is zero for this preparation
            # formula. Neither 0.33 nor 0.66 is a preparation threshold.
            expected = {"Centering": sum(values) / len(values) / width, "Range": (max(values) - min(values)) / width}
            for field, value in expected.items():
                if row.value(field) is None or not str(row.value(field)).strip():
                    return "NG", f"测点行{row.row_index}的{field}漏填，可由逐件原料复算。"
                actual = numeric(row.value(field))
                if abs(actual - value) > Decimal("1e-6"):
                    if actual * value < 0 or value != 0 and abs(actual) >= abs(value) * 1000:
                        return "NG", f"测点行{row.row_index}的{field}存在可确证的符号/量级错误。"
                    return "NG", f"测点行{row.row_index}的{field}与逐件原料复算差超过1e-6。"
        return done(ctx) if applied else ("UNABLE", "没有正公差带且含逐件数值的复算对象。")

    def metadata(ctx):
        for field in (*checker.COVER_FIELD_CONFIG, "供应商", "测量员"):
            value = _cover_one(checker, ctx, field)
            if compact(value) in _PLACEHOLDERS:
                return "NG", field + "为占位或空值。"
        return done(ctx)

    def c08(ctx):
        value = _cover_one(checker, ctx, "日期")
        parsed = normalize_excel_date(value)
        if parsed is None:
            return "NG", "测量日期不是合法日期。"
        if parsed == date(2017, 1, 10):
            return "NG", "日期仍为模板默认2017-01-10，需确认。"
        try:
            submitted = normalize_excel_date(_cover_one(checker, ctx, "提交日期"))
        except GuardStop as exc:
            if exc.status != Decision.UNABLE:
                raise
            submitted = None
        if submitted is not None and parsed > submitted:
            return "NG", "测量日期晚于原件明确提交日期。"
        return done(ctx)

    def c11(ctx):
        units: dict[int, set[str]] = {}
        for _sheet, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            for index, value in enumerate(row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")):
                match = re.search(r"(mm|[μµu]m)\s*$", str(value), re.I)
                if match:
                    units.setdefault(index, set()).add(match.group(1).casefold().replace("µ", "μ").replace("um", "μm"))
        if any(len(values) > 1 for values in units.values()):
            return "NG", "同一测量列混用mm与μm单位。"
        return done(ctx)

    def c12(ctx):
        part = _cover_one(checker, ctx, "零件号")
        name = _cover_one(checker, ctx, "零件名")
        if re.fullmatch(r"[\u4e00-\u9fff]+", str(part).strip()) or str(part).strip().startswith("备注"):
            return "NG", "零件号位置填入名称或备注，关键标识错位。"
        if normalize_excel_date(name) is not None:
            return "NG", "名称字段填入日期。"
        return done(ctx)

    def c13(ctx):
        applied = False
        for sheet, row, _columns in rows(ctx, "上公差", "下公差"):
            extra = _extra_columns(checker, ctx, sheet, ("公差带",))
            if "公差带" not in extra:
                continue
            if any(compact(row.value(field)) in {"na", "n/a"} for field in ("上公差", "下公差")):
                continue
            expected = numeric(row.value("上公差")) - numeric(row.value("下公差"))
            if expected == 0:
                continue
            applied = True
            if abs(numeric(sheet.cell(row.row_index, extra["公差带"]).value()) - expected) > Decimal("1e-6"):
                return "NG", "卷面公差带不等于上公差减下公差。"
        return done(ctx) if applied else ("NA", "NIO原件没有可比较的卷面公差带列；仍按上下公差独立复算。")

    def c14(ctx):
        failures = 0
        actual_rows = rows(ctx, "上公差", "下公差", "测量偏差值", "Centering", optional=("IQG",))
        for _sheet, row, columns in actual_rows:
            values = [compact(value) for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering") if value is not None and str(value).strip()]
            quantitative = any(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value) for value in values)
            qualitative = any(value in {"ok", "ng", "nok", "合格", "不合格", "pass", "fail", "y", "n"} for value in values)
            bounds = [compact(row.value(field)) for field in ("上公差", "下公差")]
            attribute = all(value in {"na", "n/a"} for value in bounds) or all(value not in {"na", "n/a"} for value in bounds) and numeric(row.value("上公差")) == numeric(row.value("下公差"))
            if quantitative and qualitative or attribute and any(value in {"0", "1", "0.0", "1.0"} for value in values):
                failures += 1
        return ("NG", "整列/页出现伪定量或定性数值混用。" if failures == len(actual_rows) else "个别行出现伪定量或定性数值混用。") if failures else done(ctx)

    def c15(ctx):
        for _sheet, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            if any(compact(value) in {"合格", "不合格", "nok", "pass", "fail", "y", "n"} for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")):
                return "NG", "定性实测值域存在未标准化混用。"
        return done(ctx)

    def c16(ctx):
        for _sheet, row, _columns in rows(ctx, "坐标X", "坐标Y", "坐标Z", "主要控制方向"):
            if any(compact(row.value(field)) in _PLACEHOLDERS for field in ("坐标X", "坐标Y", "坐标Z", "主要控制方向")):
                return "NG", "参考坐标/方向列含可清洗的占位或脏值。"
        return done(ctx)

    def m01(ctx):
        actual_rows = rows(ctx, "上公差", "下公差", "测量偏差值", "Centering")
        if not actual_rows:
            return "NG", "没有可定位的特性和原始测量行。"
        family(ctx)
        return done(ctx)

    def m04(ctx):
        required = ("类型", "坐标X", "坐标Y", "坐标Z", "名义值", "上公差", "下公差", "主要控制方向", "测量偏差值", "Centering", "Range")
        checker._sections(ctx, "测点号", *required)
        return done(ctx)

    def m06(ctx):
        actual_rows = rows(ctx, "测量偏差值", "Centering", optional=("IQG",))
        real = 0
        for _sheet, row, columns in actual_rows:
            identity = str(row.value("测点号") or "")
            if re.search(r"\bi\.e\.|example|示例", identity, re.I):
                continue
            values = row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")
            if any(value is not None and str(value).strip() for value in values):
                real += 1
        return done(ctx) if real else ("NG", "排除说明/示例后没有真实测量数据行。")

    def m09(ctx):
        present = False
        for sheet, row, columns in rows(ctx, optional=("测点图/截面",)):
            if "测点图/截面" not in columns:
                raise GuardStop(Decision.UNABLE, "测点图区未定位，须核对受控模板（S05）。")
            value = row.value("测点图/截面")
            if value is not None and str(value).strip():
                present = True
            native = getattr(checker, "_native_source_tables", {}).get(sheet.ws.title, {})
            if native.get("visual_evidence"):
                raise GuardStop(Decision.UNABLE, "图区有尚未解读的视觉原件，存在图像不代表其标记可审。")
        return ("UNABLE", "图区标记可读，但没有完整原件图本体证据；标记文字不能代替图本体。") if present else ("NG", "已定位测点图区为空，无可核实图或标记。")

    def m10(ctx):
        sheet = Table(ctx).sheet("CoverSheet")
        for role in ("Drafter", "DE", "VE/DRE", "ME", "PQM"):
            found = False
            for cells in sheet.ws.iter_rows(max_row=min(120, sheet.ws.max_row), max_col=min(80, sheet.ws.max_column)):
                for raw in cells:
                    if compact(raw.value) == compact(role):
                        sheet.cell(raw.row, raw.column).value()
                        found = True
            if not found:
                raise GuardStop(Decision.UNABLE, "NIO签字角色栏" + role + "尚未定位，须核对受控模板（S05）；未要求其已签字。")
        return done(ctx)

    def m11(ctx):
        version = _cover_one(checker, ctx, "版本")
        histories = [sheet for sheet in ctx.workbook.worksheets if any(term in sheet.title.casefold() for term in ("更新记录", "变更记录", "history"))]
        if not histories:
            return "NG", "本文件未定位到变更记录页，版本追溯需补核。"
        # Read actual history cells, never take the last nonempty free text.
        values = []
        for raw_sheet in histories:
            sheet = Table(ctx).sheet(raw_sheet.title)
            for cells in raw_sheet.iter_rows(max_row=min(120, raw_sheet.max_row), max_col=min(80, raw_sheet.max_column)):
                for raw in cells:
                    if compact(raw.value) in {"版本", "version", "rev", "revision"}:
                        sheet.cell(raw.row, raw.column).value()
                        for index in range(raw.row + 1, raw_sheet.max_row + 1):
                            value = sheet.cell(index, raw.column).value()
                            if value is not None and str(value).strip():
                                values.append(value)
        if not values:
            raise GuardStop(Decision.UNABLE, "变更记录版本列未能可靠定位。")
        return ("NG", "当前版本与变更记录最后版本不一致。") if compact(version) != compact(values[-1]) else done(ctx)

    def m12(ctx):
        present = False
        for sheet in ctx.workbook.worksheets:
            wrapped = Table(ctx).sheet(sheet.title)
            for cells in sheet.iter_rows(max_row=min(80, sheet.max_row), max_col=min(80, sheet.max_column)):
                for raw in cells:
                    if compact(raw.value) in {"合格率", "passrate", "状态", "判定", "status", "判定结果"}:
                        wrapped.cell(raw.row, raw.column).value()
                        present = True
        return done(ctx) if present else ("NG", "未定位到判定栏/合格率结构；方向仍交正式审核。")

    def m14(ctx):
        family(ctx)
        rows(ctx, "测量偏差值", "Centering")
        return done(ctx)

    def m16(ctx):
        for sheet, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            identity = str(row.value("测点号") or "")
            if re.search(r"\bi\.e\.|example|示例", identity, re.I):
                return "NG", "示例行仍在当前实际业务候选行中，须明确排除。"
            cells = [sheet.ws.cell(row.row_index, column) for column in range(columns["测量偏差值"].header_col, columns["Centering"].header_col)]
            if cells and all(cell.protection.locked and cell.fill.patternType == "solid" for cell in cells):
                raise GuardStop(Decision.UNABLE, "整行锁定且带底色，需确认是否说明/示例，未当真实数据通过。")
        return done(ctx)

    def m17(ctx):
        for _sheet, row, _columns in rows(ctx):
            row.value("测点号")
        return done(ctx)

    def s01(ctx):
        for field in ("零件号", "零件名", "项目", "项目阶段"):
            expected = compact(_cover_one(checker, ctx, field))
            for sheet, _section, _columns in checker._sections(ctx, "测点号"):
                try:
                    value = _cover_one(checker, ctx, field, sheet_name=sheet.title)
                except GuardStop as exc:
                    if exc.status == Decision.UNABLE:
                        raise GuardStop(Decision.UNABLE, "明细页的" + field + "未独立读取，封面不能代替明细元信息。") from None
                    raise
                if compact(value) != expected:
                    return "NG", "封面与明细页的" + field + "不一致。"
        return done(ctx)

    def s03(ctx):
        name = str(checker.audit_file.file_metadata.get("original_filename", Path(checker.audit_file.file_url).name))
        part = _cover_one(checker, ctx, "零件号")
        match = re.search(r"(?:PN|零件号)[_ -]?([A-Za-z0-9]+)", name, re.I)
        if match and compact(match.group(1)) != compact(part):
            return "NG", "文件名明确零件号与原件内零件号不一致。"
        if not match:
            raise GuardStop(Decision.UNABLE, "文件名没有可可靠解析的零件号/版本/日期声明，未自行猜测。")
        return done(ctx)

    def s05(ctx):
        applied = False
        for sheet, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            extra = _extra_columns(checker, ctx, sheet, ("均值", "极差"))
            if "均值" not in extra and "极差" not in extra:
                continue
            values = [numeric(value) for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering") if value is not None and str(value).strip()]
            if not values:
                raise GuardStop(Decision.UNABLE, "均值/极差存在但没有可读逐件原料。")
            expected = {"均值": sum(values) / len(values), "极差": max(values) - min(values)}
            for field, value in expected.items():
                if field not in extra:
                    continue
                applied = True
                if abs(numeric(sheet.cell(row.row_index, extra[field]).value()) - value) > Decimal(".005"):
                    return "NG", "均值/极差与逐件原料复算差超过5e-3；0计入样本。"
        return done(ctx) if applied else ("NA", "原件未印均值/极差列，本项没有可比的卷面对象。")

    def s12(ctx):
        samples = []
        for _sheet, row, columns in rows(ctx, "测量偏差值", "Centering", optional=("IQG",)):
            samples.append(row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering"))
        for index in range(max(len(values) for values in samples)):
            values = [values[index] for values in samples if index < len(values)]
            if any(value is None or not str(value).strip() for value in values) and any(value is not None and str(value).strip() and numeric(value) == 0 for value in values):
                raise GuardStop(Decision.UNABLE, "同列有空白与真实0，但无证据证明二者同义；未把0当空样本或推定混用。")
        return done(ctx)

    def s16(ctx):
        value = str(_cover_one(checker, ctx, "车型与阶段"))
        parts = [part.strip() for part in re.split(r"[/|]", value)]
        return done(ctx) if len(parts) == 2 and all(parts) else ("NG", "车型与阶段复合值不能拆成两个明确部分。")

    def s17(ctx):
        applied = False
        for sheet, row, columns in rows(ctx):
            extra = _extra_columns(checker, ctx, sheet, ("状态",))
            if "状态" not in extra:
                continue
            applied = True
            value = sheet.cell(row.row_index, extra["状态"]).value()
            if value is None or not str(value).strip():
                column = extra["状态"]
                if sheet.ws.cell(row.row_index, column).fill.patternType == "solid":
                    return "NG", "单点状态仅由底色承载，没有独立文本/数值。"
                raise GuardStop(Decision.UNABLE, "状态文本为空，尚不能确定实际状态。")
        return done(ctx) if applied else ("NA", "本原件无单点状态字段，未用底色或统计阈值推断工程方向。")

    def s18(ctx):
        part = _cover_one(checker, ctx, "零件号")
        if not isinstance(part, str):
            raise GuardStop(Decision.UNABLE, "零件号以数值存储，需核对前导零原文；未凭数值推定已经丢失。")
        value = _cover_one(checker, ctx, "日期")
        if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            return "NG", "日期未按YYYY-MM-DD格式保存；单号缺失不计问题。"
        return done(ctx)

    def s19(ctx):
        for _sheet, _section, columns in checker._sections(ctx, "测点号", "类型", "主要控制方向", "测量偏差值", "Centering", "Range"):
            positions = [column.header_col for column in columns.values()]
            if len(set(positions)) != len(positions):
                return "NG", "同一原件列被映射为两个不同字典字段。"
        return done(ctx)

    def m02(ctx):
        m01(ctx)
        m04(ctx)
        for field in (*checker.COVER_FIELD_CONFIG, "供应商", "测量员", "版本"):
            _cover_values(checker, ctx, field, required=False)
        m09(ctx)
        m10(ctx)
        return done(ctx)

    def s02(ctx):
        cover = Table(ctx).sheet("CoverSheet")
        index = None
        for cells in cover.ws.iter_rows(max_row=min(40, cover.ws.max_row), max_col=min(80, cover.ws.max_column)):
            title = [cell.column for cell in cells if compact(cell.value) in {"pagetitle", "分页名称", "页签名"}]
            count = [cell.column for cell in cells if compact(cell.value) in {"pagecount", "页数", "分页数"}]
            if len(title) == 1 and len(count) == 1:
                index = cells[0].row, title[0], count[0]
                break
        if index is None:
            raise GuardStop(Decision.UNABLE, "封面页签索引未定位；没有以工作表列表代替原件声明。")
        header, title_col, count_col = index
        cover.cell(header, title_col).value()
        cover.cell(header, count_col).value()
        main = [sheet.title for sheet, _section, _columns in checker._sections(ctx, "测点号")]
        found = False
        for row_index in range(header + 1, min(header + 20, cover.ws.max_row) + 1):
            value = cover.cell(row_index, title_col).value()
            if value is None or not str(value).strip():
                continue
            name = compact(value)
            if name.startswith(("总共页数", "total")):
                expected = len(main) + 1
            else:
                expected = 1 if name == "coversheet" else sum(compact(title).startswith(name) for title in main)
            count = cover.cell(row_index, count_col).value()
            if count is None or not str(count).strip():
                break
            found = True
            if expected != numeric(count):
                return "NG", "封面分页索引的页数与当前实际报告页不一致：" + str(value)
            if name.startswith(("总共页数", "total")):
                break
        if not found:
            raise GuardStop(Decision.UNABLE, "索引结构存在但未取得可核对的分页条目。")
        return done(ctx)

    def s11(ctx):
        for field in checker.COVER_FIELD_CONFIG:
            _cover_values(checker, ctx, field, required=False)
        return done(ctx)

    def single_side(ctx):
        family(ctx)
        m14(ctx)
        return "NA", "已从NIO单方实测结构确认没有双边对标对象，未要求UAES/供应商双方数据。"

    def related_fact(source_id, *, mode):
        group = checker.group_context
        if group is None:
            return state_fact(source_id, None, "未提供完整冻结文件集合；本项跨文件/同时在批的两批检查未执行，未推定范围外或通过。", status="not_checked")
        if group.unavailable_files:
            return state_fact(source_id, None, "本批已提供文件不可读取，未把技术失败当缺件。", status="error")
        others = [item for item in group.by_audit_id.values() if item.audit_id != checker.audit_file.audit_id]
        if not others:
            return state_fact(source_id, "S01", "本批只有本原件，没有同时在批的对端；未追索外部上一批。", status="not_checked")
        if mode == "forms":
            original = str(checker.audit_file.file_metadata.get("original_filename", Path(checker.audit_file.file_url).name))
            candidates = [
                item
                for item in others
                if Path(str(item.file_metadata.get("original_filename", Path(item.file_url).name))).stem == Path(original).stem
                and Path(item.file_url).suffix.lower() != Path(checker.audit_file.file_url).suffix.lower()
            ]
            if len(candidates) != 1:
                return state_fact(source_id, None, "同源双形态配对尚未成立，文件名相似不能替代同源身份。", status="unknown")
        else:
            typed = {item.audit_id for values in group.by_doc_type.values() for item in values}
            if any(item.audit_id not in typed for item in others):
                return state_fact(source_id, None, "本批有原件未确认类型，未按文件名推定没有另一份测量报告。", status="unknown")
            by_id = {
                item.audit_id: item
                for kind, values in group.by_doc_type.items()
                if kind in {checker.rule.type, "T01", "全功能检验报告", "测量报告"}
                for item in values
                if item.audit_id != checker.audit_file.audit_id
            }
            candidates = list(by_id.values())
            if not candidates:
                return state_fact(source_id, "S01", "冻结类型集合没有同类另一份报告，未引用外部历史结果。", status="not_checked")
        contexts = []
        status, code, message = "satisfied", None, "已核对当前冻结集合的同源/同件报告。"
        try:
            current = checker.rule_ctx(checker._load_workbook())
            contexts.append((checker, current))
            part = _cover_one(checker, current, "零件号")
            project = _cover_one(checker, current, "项目")
            stage = _cover_one(checker, current, "项目阶段")
            version = _cover_one(checker, current, "版本")
            for audit in candidates:
                if Path(audit.file_url).suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
                    raise GuardStop(Decision.UNABLE, "同源对端尚无可验证的完整原件结构和图示；没有触发新的OCR/视觉调用。")
                reference = type(checker)(audit, checker.rule, group_context=group)
                foreign = reference.rule_ctx(reference._load_workbook())
                contexts.append((reference, foreign))
                family(foreign)
                expected_customer = compact(checker.audit_file.customer or (checker.rule.customers[0] if len(checker.rule.customers) == 1 else ""))
                customer = compact(audit.customer or _cover_one(reference, foreign, "客户"))
                if not expected_customer or customer not in {expected_customer, "nio", "蔚来", "蔚来汽车"}:
                    raise GuardStop(Decision.UNABLE, "对端客户身份未能确认一致，不能由NIO模板族反推客户。")
                other_part = _cover_one(reference, foreign, "零件号")
                other_project = _cover_one(reference, foreign, "项目")
                if compact(part) != compact(other_part) or compact(project) != compact(other_project):
                    if mode == "forms":
                        raise GuardStop(Decision.UNABLE, "双形态零件/项目不一致，同源配对尚未成立。")
                    continue
                other_stage = _cover_one(reference, foreign, "项目阶段")
                other_version = _cover_one(reference, foreign, "版本")
                if mode == "forms":
                    if compact(stage) != compact(other_stage) or compact(version) != compact(other_version):
                        raise GuardStop(Decision.UNABLE, "双形态阶段或版本不同，不能当同一份原件比对。")

                    def records(owner, context):
                        result = {}
                        for _sheet, row, columns in owner._rows(context, "名义值", "上公差", "下公差", "测量偏差值", "Centering", "Range", optional=("IQG",)):
                            identity = compact(row.value("测点号"))
                            if identity in result:
                                raise GuardStop(Decision.UNABLE, "跨页重复测点须有明确配对映射，未按末条覆盖。")
                            result[identity] = (
                                compact(row.value("名义值")),
                                compact(row.value("上公差")),
                                compact(row.value("下公差")),
                                tuple(compact(value) for value in row.values_between("测量偏差值", "IQG" if "IQG" in columns else "Centering")),
                            )
                        return result

                    if records(checker, current) != records(reference, foreign):
                        raise GuardStop(Decision.NG, "同源两种形态的测点集合或逐件原始数值不一致。")
                    raise GuardStop(Decision.UNABLE, "同源结构化数值已比对；完整测点图本体的跨形态一致性仍需核实。")
                supplier = _cover_one(checker, current, "供应商")
                other_supplier = _cover_one(reference, foreign, "供应商")
                if compact(stage) != compact(other_stage) or compact(supplier) != compact(other_supplier):
                    code = "B02"
                    raise GuardStop(Decision.NG, "同项目同零件的多份测量报告阶段或供应商互斥。")
                if mode == "batch":
                    if compact(version) == compact(other_version):
                        code = "W04"
                        raise GuardStop(Decision.NG, "同件同版本重复报告须确认，未任取一份覆盖。")
                    continue
                batch = _cover_one(checker, current, "批次")
                other_batch = _cover_one(reference, foreign, "批次")
                if compact(batch) == compact(other_batch):
                    continue
                left_date = normalize_excel_date(_cover_one(checker, current, "日期"))
                right_date = normalize_excel_date(_cover_one(reference, foreign, "日期"))
                if left_date is None or right_date is None:
                    raise GuardStop(Decision.UNABLE, "两批日期未可靠解析，未猜测版本倒挂。")

                def revision(value):
                    text = str(value).strip()
                    match = re.fullmatch(r"[vV]?(\d+)", text)
                    if match:
                        return int(match.group(1))
                    if re.fullmatch(r"[A-Z]", text):
                        return ord(text)
                    raise GuardStop(Decision.UNABLE, "版本排序体系不明，未按普通字符串判倒挂。")

                left, right = revision(version), revision(other_version)
                if left_date > right_date and left < right or right_date > left_date and right < left:
                    code = "B02"
                    raise GuardStop(Decision.NG, "同时在当前批内的两批报告存在日期新、版本旧的倒挂。")
                if left == right:
                    code = "W04"
                    raise GuardStop(Decision.NG, "两批同版本报告须说明新旧与重复原因。")
        except GuardStop as exc:
            status = "defect" if exc.status == Decision.NG else "unknown"
            message = exc.message
            if status == "defect" and code is None:
                code = "B06"
        except Exception:
            status, code, message = "error", None, "已提供原件读取失败或本项执行失败，尚无业务结论。"
        if any(context.has_unresolved() for _owner, context in contexts):
            status, code, message = "unknown", None, "关联原件存在不可读取事实，未转为通过或供应商缺陷。"
        sources: list[dict] = []
        evidence = None
        for owner, context in contexts:
            observed = owner._evidence_from_reads(context)
            if observed:
                evidence = evidence or observed.model_dump(mode="json")
                sources.extend(source.model_dump(mode="json") for source in observed.sources)
        if evidence:
            evidence["sources"] = sources
        if status == "satisfied" and not sources:
            status, message = "unknown", "尚未取得可追溯原件位置，不能声称已核对。"
        return {**state_fact(source_id, code, message, status=status), "evidence": evidence, "certainty": "confirmed" if status == "defect" else "unknown"}

    checks = {
        "C-01": (c01, "B05"),
        "C-02": (c02, "B05"),
        "C-03": (c03, "B05"),
        "C-04": (c04, "B05"),
        "C-05": (c05, "B03"),
        "C-06": (c06, "W03"),
        "C-07": (c07, "B05"),
        "C-08": (c08, "B05"),
        "C-09": (c01, "B05"),
        "C-11": (c11, "B05"),
        "C-12": (c12, "B03"),
        "C-13": (c13, "B06"),
        "C-14": (c14, "W02"),
        "C-15": (c15, "W03"),
        "C-16": (c16, "W03"),
        "M-01": (m01, "W01"),
        "M-02": (m02, "B05"),
        "M-03": (metadata, "B05"),
        "M-04": (m04, "B05"),
        "M-06": (m06, "B04"),
        "M-09": (m09, "B05"),
        "M-10": (m10, "B05"),
        "M-11": (m11, "W01"),
        "M-12": (m12, "W01"),
        "M-14": (m14, "B04"),
        "M-15": (single_side, "B05"),
        "M-16": (m16, "B04"),
        "M-17": (m17, "W05"),
        "S-01": (s01, "B06"),
        "S-02": (s02, "W06"),
        "S-11": (s11, None),
        "S-03": (s03, "W04"),
        "S-05": (s05, "W06"),
        "S-09": (m11, "W04"),
        "S-12": (s12, "W03"),
        "S-16": (s16, "W03"),
        "S-17": (s17, "B05"),
        "S-18": (s18, "W03"),
        "S-19": (s19, "B06"),
        "M-05": (m05, "W02"),
        "M-07": (m07, "W01"),
        "M-08": (m08, "B05"),
        "S-04": (checker.rule_6, "B06"),
        "S-06": (s06, "W06"),
        "S-10": (lambda ctx: m07(ctx, require_minimum=False), "W01"),
    }
    facts = []
    for source in l1_criteria(entry):
        identifier = source["criterion_id"]
        if identifier in {"S-08", "S-14", "S-15"}:
            fact = related_fact(source["id"], mode={"S-08": "forms", "S-14": "batch", "S-15": "history"}[identifier])
        elif identifier in {"C-10", "C-17"}:
            fact = _native_error_fact(checker, source["id"], derived=identifier == "C-17")
        elif identifier in checks:
            check, code = checks[identifier]
            fact = checker_fact(checker, source["id"], check, code=code)
            if fact["status"] == "not_checked":
                fact["code"] = "S01"
            if fact["status"] == "defect":
                if identifier == "M-03" and fact["message"].startswith(("零件号", "零件名", "项目字段", "项目为")):
                    fact["code"] = "B03"
                if identifier == "S-06" and "符号/量级" in fact["message"]:
                    fact["code"] = "B06"
                elif identifier == "S-06" and "漏填" in fact["message"]:
                    fact["code"] = "W05"
                if identifier == "C-08" and any(term in fact["message"] for term in ("模板默认", "晚于原件明确提交")):
                    fact["code"] = "W03"
                if identifier == "C-14" and "整列/页" in fact["message"]:
                    fact["code"] = "B05"
                if identifier == "C-12" and "名称字段" in fact["message"]:
                    fact["code"] = "W03"
            if identifier == "M-01" and fact["status"] == "unknown" and "族签名不足" in fact["message"]:
                fact["code"] = "W01"
            if fact["status"] == "unknown" and "S05" in fact["message"]:
                fact["code"] = "S05"
            if identifier == "C-06" and fact["status"] == "defect":
                # Only a complete A/B column proves the semantic substitution.
                ctx = checker.rule_ctx(checker._load_workbook())
                try:
                    values = [compact(row.value("类型")) for _, row, _ in rows(ctx, "类型")]
                    if values and all(value in {"a", "b"} for value in values):
                        fact["code"] = "B05"
                except GuardStop:
                    pass
        elif identifier == "S-07":
            fact = state_fact(source["id"], "S03", "合格率与超差行数的工程结论移交正式审核；预检不记问题。", status="handoff")
        else:
            fact = state_fact(source["id"], None, "本项源事实尚未核实：" + source["criterion"] + "；需要对应原件定位，不能视为通过。", status="unknown")
        fact["source_clause"] = source["criterion"]
        facts.append(fact)
    element_checks = {
        "E01": m01,
        "E02": lambda ctx: _cover_values(checker, ctx, "零件号", required=False),
        "E03": lambda ctx: _cover_values(checker, ctx, "零件号", required=False),
        "E04": lambda ctx: _cover_values(checker, ctx, "零件名", required=False),
        "E05": lambda ctx: _cover_values(checker, ctx, "项目", required=False),
        "E06": lambda ctx: _cover_values(checker, ctx, "项目阶段", required=False),
        "E07": lambda ctx: _cover_values(checker, ctx, "日期", required=False),
        "E08": lambda ctx: _cover_values(checker, ctx, "供应商", required=False),
        "E09": lambda ctx: _cover_values(checker, ctx, "测量员", required=False),
        "E10": lambda ctx: rows(ctx),
        "E11": lambda ctx: checker._sections(ctx, "测点号", "类型"),
        "E12": lambda ctx: checker._sections(ctx, "测点号", "坐标X", "坐标Y", "坐标Z"),
        "E13": lambda ctx: checker._sections(ctx, "测点号", "名义值"),
        "E14": lambda ctx: checker._sections(ctx, "测点号", "上公差"),
        "E15": lambda ctx: checker._sections(ctx, "测点号", "下公差"),
        "E16": lambda ctx: checker._sections(ctx, "测点号", "主要控制方向"),
        "E17": lambda ctx: checker._sections(ctx, "测点号", "测量偏差值"),
        "E18": lambda ctx: checker._sections(ctx, "测点号", "Centering"),
        "E19": lambda ctx: checker._sections(ctx, "测点号", "Range"),
        "E20": m09,
        "E21": m12,
        "E22": m10,
        "E23": lambda ctx: _cover_values(checker, ctx, "版本", required=False),
    }
    elements = []
    for element, operation in element_checks.items():

        def presence(ctx, operation=operation):
            observed = operation(ctx)
            if isinstance(observed, tuple) and len(observed) == 2 and observed[0] in {"NG", "UNABLE", "NA"}:
                return observed
            return done(ctx)

        fact = checker_fact(checker, "N01:M-02:" + element, presence, code="B05")
        fact["source_id"] = "N01:M-02"
        if fact["status"] == "unknown" and "S05" in fact["message"]:
            fact["code"] = "S05"
        elements.append(fact)
    for root in facts:
        if root["id"] == "N01:M-02":
            root["elements"] = elements
    return facts
