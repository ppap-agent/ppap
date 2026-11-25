"""UAES U04 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.materials import (
    _DRAWING,
    UaesMaterialsAgentR3,
    compact,
    layout_id,
    original_name,
    unable,
)
from audit.engine.rule_engine import Decision, GuardStop, RuleContext

from .drawing_title_r4 import drawing_pages

_HAN = re.compile(r"[\u3400-\u9fff]")


_ACTIVE = {"在用", "有效", "现行", "active", "current"}


_INACTIVE = {"作废", "失效", "已作废", "已替代", "obsolete", "superseded", "withdrawn", "inactive"}


def complete_designations(value: str) -> tuple[set[str], bool]:
    """Only two complete printed language variants; no substring, semantic or token matching."""
    value = value.strip()
    if not _HAN.search(value) or not re.search("[A-Za-z]", value):
        return {value}, True
    splits = list(re.finditer(r"\s*(?:[\r\n]+|/|／|\||｜)\s*", value))
    pairs = [(value[: m.start()], value[m.end() :]) for m in splits]
    if m := re.fullmatch(r"(.+?)\s*[(（]([^()（）]+)[)）]", value):
        pairs.append((m.group(1), m.group(2)))
    if not pairs:
        # Plain side-by-side text can also have an unambiguous language boundary.
        for m in re.finditer(r"(?<=[\u3400-\u9fff])\s+(?=[A-Za-z])|(?<=[A-Za-z])\s+(?=[\u3400-\u9fff])", value):
            left, right = value[: m.start()], value[m.end() :]
            if not re.search("[A-Za-z]", left if _HAN.search(left) else right):
                pairs.append((left, right))
    # Permit a mixed model code in the Chinese name, but never carve a suffix out of an English title.
    variants = {
        (left.strip(), right.strip())
        for left, right in pairs
        if left.strip()
        and right.strip()
        and bool(_HAN.search(left)) != bool(_HAN.search(right))
        and re.search("[A-Za-z]", right if _HAN.search(left) else left)
        and not any(sep in (right if _HAN.search(left) else left) for sep in ("\n", "｜", "|"))
    }
    if len(variants) == 1:
        return {value, *next(iter(variants))}, True
    return {value}, False


class UaesMaterialsAgentR4(UaesMaterialsAgentR3):
    """Reader for signed UAES v1.0 revision 4."""

    def identity_fields(self, ctx: RuleContext, item: Any) -> list[dict]:
        """A PDF drawing's identity from its own title block (drawing_title_r4); a workbook's as revision 3 reads it."""
        if Path(str(item.file_url)).suffix.lower() != ".pdf":
            return super().identity_fields(ctx, item)
        return drawing_pages(self, item)

    @rule("同批图纸的零件名称与零件号一致")
    def msl_3(self, ctx: RuleContext):
        sheets = self.lists(ctx)
        if len(sheets) != 1:
            raise unable(f"原件有{len(sheets)}份材料及分供方清单，尚不能确定按哪一份核对图纸，需人工复核。")
        sheet = sheets[0]
        refs = self.lib.related_by_content(self, _DRAWING)
        if not refs.items:
            self.need("图纸")
            return "NG", "同批次没有图纸独立文件。"
        part = self.field_value(ctx, sheet, "零件号", self.res.PART_NUMBER)
        if self.lib._placeholder(compact(part)):
            raise unable(f"清单零件号为“{part}”，不能用于配对图纸。")
        records = []
        for item in refs.items:
            try:
                records.extend(self.identity_fields(ctx, item))
            except GuardStop as stop:
                if stop.status != Decision.UNABLE:
                    raise
                records.append({"kind": "unknown", "where": original_name(item)})
        drawings = [r for r in records if r["kind"] == "drawing" and r.get("number")]
        unknown = [r["where"] for r in records if r["kind"] == "unknown" or r["kind"] == "drawing" and not r.get("number")]
        lists = "；已按表头排除零件清单：" + "、".join(r["where"] for r in records if r["kind"] == "parts_list") if any(r["kind"] == "parts_list" for r in records) else ""
        same = [r for r in drawings if layout_id(r["number"]) == layout_id(part)]
        if not same:
            if unknown:
                raise unable("尚不能确认图纸类型或本件图号：" + "、".join(unknown) + lists + "。")
            self.need("图纸", part=part)
            if not drawings:
                return "NG", "同批次没有图纸独立文件" + lists + "。"
            details = "；".join(f"{r['where']} 图号“{r['number']}”" for r in drawings)
            return "NG", f"比对源错配：清单零件号“{part}”；{details}。没有同号图纸，未比较零件名称{lists}。"
        # Project and designation cannot obscure an already established wrong-number source.
        project = self.field_value(ctx, sheet, "UAES项目编号", self.res.PROJECT)
        name = self.field_value(ctx, sheet, "零件名称", self.res.PART_NAME)
        if self.lib._placeholder(compact(project)) or not name:
            raise unable("已找到同号图纸，但清单的项目或零件名称无法用于核对。")
        eligible = [r for r in same if r.get("project") and layout_id(r["project"]) == layout_id(project)]
        if not eligible:
            if unknown or any(not r.get("project") for r in same):
                raise unable(f"找到零件号“{part}”的图纸，但尚不能从图纸字段确认项目“{project}”。")
            return "NG", f"同号图纸所列项目（{'、'.join(r['project'] for r in same)}）均不是清单项目“{project}”。"
        # A superseded drawing cannot supply the one matching name. If different candidates exist,
        # a positive match needs evidence it is current; an unconfirmed candidate cannot prove a negative.
        usable = [r for r in eligible if compact(r.get("status")) not in _INACTIVE]
        if not usable and not unknown and all(r.get("project") for r in same):
            return "NG", f"同项目、同件号“{part}”的图纸均明确标记为非在用。"
        matches = [r for r in usable if name in complete_designations(r.get("name", ""))[0]]
        current = next((r for r in matches if compact(r.get("status")) in _ACTIVE), None)
        distinct = len({r.get("name") for r in usable}) > 1
        matched = current or next((r for r in matches if not distinct and not r.get("status")), None)
        if matched:
            return "PASS", f"{matched['where']} 的项目“{project}”、图号“{matched['number']}”、图纸名称“{matched['name']}”与清单完整名称“{name}”一致。"
        if matches:
            raise unable("同号同项目图纸虽有相同的完整名称，但不能从原件确认该图在用。")
        if unknown or any(not r.get("project") for r in same) or any(not r.get("name") or not complete_designations(r["name"])[1] for r in usable):
            raise unable("候选图纸身份、项目或完整名称边界未读全，不能判为名称不一致。")
        names = "；".join(f"{r['where']}：{r['name']}" for r in usable)
        return "NG", f"零件名称不一致：同项目、同号图纸名称“{names}”，清单名称“{name}”。"

    @rule("按材料或外协工序逐项双向溯源")
    def msl_4(self, ctx: RuleContext):
        from .material_flow_r4 import check_trace

        return check_trace(self, ctx)
