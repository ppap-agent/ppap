"""NIO measurement preparation facts, separate from engineering thresholds."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from audit.engine.rule_engine import Decision, GuardStop
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
            actual = sum(value is not None and str(value).strip() != "" for value in values)
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
                if abs(numeric(row.value(field)) - value) > Decimal("1e-6"):
                    return "NG", f"测点行{row.row_index}的{field}与逐件原料复算差超过1e-6。"
        return done(ctx) if applied else ("UNABLE", "没有正公差带且含逐件数值的复算对象。")

    checks = {
        "C-01": (c01, "B05"),
        "C-02": (c02, "B05"),
        "C-03": (c03, "B05"),
        "C-04": (c04, "B05"),
        "C-05": (c05, "B03"),
        "C-06": (c06, "W03"),
        "C-07": (c07, "B05"),
        "C-09": (c01, "B05"),
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
        if identifier in checks:
            check, code = checks[identifier]
            fact = checker_fact(checker, source["id"], check, code=code)
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
            fact = state_fact(source["id"], None, "本项尚未取得完整原件事实，未执行；不得视为通过。")
        fact["source_clause"] = source["criterion"]
        facts.append(fact)
    return facts
