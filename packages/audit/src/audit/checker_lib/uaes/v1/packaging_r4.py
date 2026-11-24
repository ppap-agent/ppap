"""UAES U09 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import re
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.packaging import (
    SIGNERS,
    UaesPackagingAgentR3,
    UaesPackagingDocxAgentR3,
    compact,
    sign_fields,
    signer,
    unable,
)
from audit.engine.rule_engine import Decision, GuardStop, RuleContext


def judge_signatures(names: list[str], values: dict[str, Any]) -> tuple[str, str]:
    unsigned = [name for index, name in enumerate(names) if values.get(f"sign_{index}") is False]
    problems = []
    if unsigned:
        problems.append("、".join(f"“{name}”" for name in unsigned) + "栏没有手写签字")
    if values.get("seal") is False:
        problems.append("编制、审核、批准签署区没有受控印章或公章")
    if problems:
        return "NG", "；".join(problems)
    if any(values.get(field) is not True for field in sign_fields(names)):
        return "UNABLE", "编制、审核、批准签署栏的签字或印章尚未能从原件全部核实，需人工复核"
    return "PASS", "编制、审核、批准签署栏均有手写签字，签署区有印章"


SECTIONS = (
    (r"(?:零件|部件|产品)(?:基本|基础)?(?:信息|资料|概况|标识)", r"(?:零件|部件|产品)(?:号(?:码)?|图号|名称)"),
    (r"(?:供应商|供方|供货商|制造商)(?:基本|基础)?(?:信息|资料|概况)", r"(?:供应商|供方|供货商|制造商)(?:名称|代码|地址|联系人)"),
    (r"(?:包装|包装设计)(?:的)?(?:基本|基础|通用|一般|常规)(?:要求|原则|规范)", r"(?:包装|包装设计)(?:通用|基本|一般)(?:防护)?要求"),
    (
        r"(?:针对该零件包装的特殊要求|(?:本件|该零件|产品|零件)?(?:专用包装|包装特殊|特殊包装|包装专用)(?:要求|约束)|(?:零件|产品)特殊要求)",
        r"(?:本件|该零件|本零件)(?:专用|特殊)?(?:防护|包装)(?:要求|措施)",
    ),
    (r"(?:包装|装箱|装载)(?:方案|配置|方式|设计)(?:信息|明细|说明|参数|详情)?", r"(?:装箱(?:方式|数量)|每箱数量|每层数量|每托箱数|码放方式|包装方式)"),
    (r"(?:包材|包装材料|包装物料)(?:清单|明细|列表|一览表)", r"(?:包材|包装材料|包装物料)(?:名称|规格|型号|用量|编码)"),
)


EMPTY = {"待填", "待定", "未填", "待补", "tbd", "n/a", "na", "-", "/", "—", "□", "…"}


GENERIC_FIELDS = {"名称", "编号", "规格", "数量", "单位", "材质", "备注", "项目", "要求", "内容", "地址", "联系人", "电话"}


TITLE_FIELD = re.compile(r"^(?:文件(?:名称|标题)|文档(?:名称|标题)|标题)(?::(.*))?$")


def block_indices(label, index):
    return [i for i, pair in enumerate(SECTIONS) if re.fullmatch(pair[index], label)]


def package_content(facts, blocks):
    """Read the native document title and local heading/field-value rows, never a flattened keyword list."""
    groups = {}
    for value, loc in facts.observations():
        kind = loc["kind"]
        if kind not in {"xlsx_cell", "pdf_block", "docx_cell", "docx_paragraph"} or facts.format == ".pdf" and kind != "pdf_block":
            continue
        if kind == "xlsx_cell":
            group, row = ("sheet", loc["sheet"]), int(re.search(r"\d+$", loc["cell"])[0])
        elif kind == "docx_cell":
            group, row = ("table", loc["table"]), loc["row"]
        elif kind == "docx_paragraph":
            group, row = ("body", 0), loc["paragraph"]
        else:
            group, row = ("page", loc["page"]), loc["block"]
        for n, line in enumerate(value.splitlines() or [value]):
            if compact(line):
                groups.setdefault(group, {}).setdefault((row, n), []).append((line, loc))
    titles, explicit, seen = [], [], set()

    def cite(entry):
        value, loc = entry
        facts.witness(loc, value, facts.version)

    def column(entry):
        loc = entry[1]
        return loc.get("column") or re.sub(r"\d+$", "", loc.get("cell", ""))

    def data(value):
        value = compact(value)
        return value and value not in EMPTY | GENERIC_FIELDS and not block_indices(value, 0) and not block_indices(value, 1) and not signer(value)

    for (kind, number), source_rows in groups.items():
        active = None
        positions = sorted(source_rows)
        rows = [source_rows[k] for k in positions]
        claimed = set()
        for pos, row in enumerate(rows):
            texts = [compact(value) for value, _ in row]
            for j, (value, loc) in enumerate(row):
                label = TITLE_FIELD.fullmatch(compact(value))
                if label:
                    if label[1]:
                        explicit.append((label[1], loc))
                    elif j + 1 < len(row):
                        explicit.append(row[j + 1])
            if pos == 0 and (kind != "page" or number == 1):
                # The document heading is distinct from a body section called 包装方案信息.
                # For Word, leading paragraphs are title-bearing; tables also can have an explicit title field.
                if len(row) == 1 and (not block_indices(texts[0], 0) or texts[0] == "包装方案") and not TITLE_FIELD.fullmatch(texts[0]):
                    titles.append((kind, row[0]))
                    continue
            if any(signer(t) or re.fullmatch(r"(?:uaes|客户)(?:签字|审批|批准)", t) for t in texts):
                active = None
                continue
            # A contents/index row naming several sections supplies no block values.
            starts = [(j, ids[0]) for j, t in enumerate(texts) if (ids := block_indices(re.sub(r"^(?:\d+[.、)）]|[一二三四五六]+、)", "", re.split("[:：]", t, maxsplit=1)[0]), 0))]
            if len(starts) > 1:
                active = None
                continue
            if starts:
                j, active = starts[0]
                cite(row[j])
                suffix = re.split("[:：]", texts[j], maxsplit=1)
                if len(suffix) == 2 and data(suffix[1]):
                    seen.add(active)
            for j, entry in enumerate(row):
                if (starts and starts[0][0] == j) or id(entry) in claimed:
                    continue
                t = texts[j]
                label, sep, inline = t.partition(":")
                fields = block_indices(label, 1)
                if fields:
                    candidate = (inline, entry[1]) if sep and inline else row[j + 1] if j + 1 < len(row) else None
                    if not (candidate and data(candidate[0])) and pos + 1 < len(rows) and positions[pos + 1][0] == positions[pos][0] + 1 and column(entry):
                        candidate = next((x for x in rows[pos + 1] if column(x) == column(entry)), None)
                    if candidate and data(candidate[0]):
                        # A filled part-number field remains part data even if it is printed
                        # beneath a materials heading. Do not double-count its value.
                        if not inline:
                            claimed.add(id(candidate))
                        seen.update(fields)
                        cite(entry)
                        cite(candidate)
                elif active is not None and data(t) and not TITLE_FIELD.fullmatch(t):
                    seen.add(active)
                    cite(entry)
    # A Word document title belongs to its opening body, not to each later table.
    primary = [item for kind, item in titles if kind == "body"]
    allowed_titles = explicit or primary or [item for _kind, item in titles]
    has_title = next((entry for entry in allowed_titles if "包装方案" in compact(entry[0])), None)
    if has_title:
        cite(has_title)
    missing = ([] if has_title else ["含“包装方案”的文件内容标题"]) + [f"“{block}”实质内容" for i, block in enumerate(blocks) if i not in seen]
    if missing:
        gaps = "、".join(missing)
        return ("UNABLE", f"未能在可读取内容中确认{gaps}，原件仍有未读取内容。") if facts.has_unread_content() else ("NG", f"原件缺少{gaps}。")
    return "PASS", "原件标题含“包装方案”，六个内容块均有实际内容。"


class UaesPackagingAgentR4(UaesPackagingAgentR3):
    """Reader for signed UAES v1.0 revision 4."""

    @rule("包装方案标题、六个内容块与供应商签署")
    def pkg_1(self, ctx: RuleContext):
        try:
            content_status, content = self._from_legacy(lambda agent: package_content(agent.native(), self.READER.resources.PACKAGING_BLOCKS))
        except GuardStop as stop:
            content_status, content = stop.status, stop.message
        names = self.signers(ctx)
        if not names:
            sign_status, sign = self.lacking("供应商编制、审核、批准签署栏")
        else:
            try:
                values, _unreadable = self.visual_values(sign_fields(names))
                sign_status, sign = judge_signatures(names, values)
            except GuardStop as stop:
                sign_status, sign = stop.status, "签字和印章未能读取：" + stop.message.rstrip("。")
            missing = [name for name in SIGNERS if name not in names]
            if missing and sign_status != "NG":
                sign_status, sign = self.lacking("、".join(f"“{name}”" for name in missing) + "签署栏")
        problems = [text.rstrip("。") for status, text in ((content_status, content), (sign_status, sign)) if status == "NG"]
        if problems:
            return "NG", "包装方案" + "；".join(problems) + "。"
        if "ERR" in (content_status, sign_status):
            raise GuardStop(Decision.ERR, "；".join(text.rstrip("。") for status, text in ((content_status, content), (sign_status, sign)) if status != "PASS") + "。")
        if "UNABLE" in (content_status, sign_status):
            raise unable("；".join(text.rstrip("。") for status, text in ((content_status, content), (sign_status, sign)) if status == "UNABLE") + "。")
        return "PASS", content.rstrip("。") + "；" + sign + "。"


class UaesPackagingDocxAgentR4(UaesPackagingDocxAgentR3):
    """Reader for signed UAES v1.0 revision 4."""

    @rule("包装方案标题、六个内容块与供应商签署")
    def pkg_1(self):
        facts = self.native()
        status, content = package_content(facts, self.READER.resources.PACKAGING_BLOCKS)
        if status == "NG":
            return status, content
        names = [name for name in SIGNERS if facts.contains(name)]
        if not names:
            return "NG", "包装方案原件中没有供应商编制、审核、批准签署栏。"
        values, _unreadable = self.visual_observation(sign_fields(names))
        sign_status, sign = judge_signatures(names, values)
        missing = [name for name in SIGNERS if name not in names]
        if sign_status == "NG" or missing:
            return "NG", "包装方案" + "；".join([*([sign] if sign_status == "NG" else []), *(["原件中没有" + "、".join(missing) + "签署栏"] if missing else [])]) + "。"
        if sign_status == "UNABLE":
            return "UNABLE", sign + "。"
        return status, content.rstrip("。") + "；" + sign + "。"
