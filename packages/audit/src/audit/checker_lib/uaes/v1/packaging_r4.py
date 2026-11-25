"""UAES U09 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

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
from audit.checker_lib.uaes.v1.scoped_text_r4 import read_facts
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


_KINDS = ("part_identity", "supplier_identity", "basic_requirements", "part_specific_protection", "packing_configuration", "packaging_materials")


class _BodyFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["part_identity", "supplier_identity", "basic_requirements", "part_specific_protection", "packing_configuration", "packaging_materials"]
    source: str
    value: str


class _BodyReading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    coverage: Literal["complete", "uncertain"]
    facts: list[_BodyFact]


def read_body_blocks(facts, records, missing):
    """Read only otherwise-unclassified body records. Each value must occur in its cited row."""
    sources = {f"r{i}": " | ".join(v for v, _ in row) for i, row in enumerate(records)}
    if not sources:
        return set(), True
    try:
        result = _BodyReading.model_validate(
            read_facts(
                "uaes_packaging_body_facts",
                sources=sources,
                schema=_BodyReading.model_json_schema(),
                instructions=(
                    "只从这些正文记录提取实际填写或实际规定的内容，按字段含义标识事实；每项单独给出所在记录的source和原文中完整的具体取值或要求句。"
                    "不从目录、填表说明、示例、模板提示、标题、空栏推测。不得跨行拼接；多个格或段落在同一编号内以 | 分隔。"
                    "basic_requirements是包装通用要求，part_specific_protection必须是针对该零件的特定包装约束而不是通用建议，"
                    "packing_configuration是实际包装方案，packaging_materials是实际包材明细。只需提取以下种类：" + ", ".join(_KINDS[i] for i in sorted(missing))
                ),
            )
        )
    except GuardStop as exc:
        if exc.status != "UNABLE":
            raise
        return set(), False
    except ValidationError:
        return set(), False
    if result.coverage != "complete":
        return set(), False
    found = set()
    for observation in result.facts:
        kind = _KINDS.index(observation.kind)
        if kind not in missing or observation.source not in sources or not observation.value.strip() or compact(observation.value) in EMPTY | GENERIC_FIELDS:
            return set(), False
        row = records[int(observation.source[1:])]
        matches = [value for value, _ in row if observation.value.strip() in {value.strip(), re.split("[:：]", value, maxsplit=1)[-1].strip()}]
        if len(matches) != 1:
            return set(), False
        # Retain the original record, including the heading when it identifies the value's role.
        for value, loc in row:
            facts.witness(loc, value, facts.version)
        found.add(kind)
    # Only an identified substantive record proves presence. Unclassified prose cannot prove absence.
    return found, missing <= found


def package_content(facts, blocks):
    """Read the native document title and local heading/field-value rows, never a flattened keyword list."""
    groups = {}
    for value, loc in facts.observations():
        kind = loc["kind"]
        if kind not in {"xlsx_cell", "xls_cell", "pdf_block", "docx_cell", "docx_paragraph"} or facts.format == ".pdf" and kind != "pdf_block":
            continue
        if kind in {"xlsx_cell", "xls_cell"}:
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
    unresolved = []

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
        orphan_heading = None
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
                active = orphan_heading = None
                continue
            # A contents/index row naming several sections supplies no block values.
            starts = [(j, ids[0]) for j, t in enumerate(texts) if (ids := block_indices(re.sub(r"^(?:\d+[.、)）]|[一二三四五六]+、)", "", re.split("[:：]", t, maxsplit=1)[0]), 0))]
            if len(starts) > 1:
                active = orphan_heading = None
                continue
            if starts:
                j, active = starts[0]
                cite(row[j])
                suffix = re.split("[:：]", texts[j], maxsplit=1)
                if len(suffix) == 2 and data(suffix[1]):
                    seen.add(active)
            # New unrecognised inline labels or later rows are not automatically values of a preceding block.
            unknown = []
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
                elif data(t) and not TITLE_FIELD.fullmatch(t):
                    if active is not None and (starts or (active not in seen and not sep)):
                        seen.add(active)
                        cite(entry)
                    else:
                        unknown.append(entry)
            if unknown:
                # A standalone unrecognised heading can introduce the next nearby statement or value.
                within = pos and positions[pos][0] <= positions[pos - 1][0] + 1
                if orphan_heading is not None and within:
                    unresolved.append(orphan_heading + unknown)
                else:
                    unresolved.append(unknown)
                orphan_heading = unknown
            else:
                orphan_heading = None
    # A Word document title belongs to its opening body, not to each later table.
    primary = [item for kind, item in titles if kind == "body"]
    allowed_titles = explicit or primary or [item for _kind, item in titles]
    has_title = next((entry for entry in allowed_titles if "包装方案" in compact(entry[0])), None)
    if has_title:
        cite(has_title)
    complete = True
    if has_title and len(seen) < len(blocks):
        supplements, complete = read_body_blocks(facts, unresolved, set(range(len(blocks))) - seen)
        seen.update(supplements)
    missing = ([] if has_title else ["含“包装方案”的文件内容标题"]) + [f"“{block}”实质内容" for i, block in enumerate(blocks) if i not in seen]
    if missing:
        gaps = "、".join(missing)
        return ("UNABLE", f"未能在可读取内容中确认{gaps}，原件仍有未读取内容。") if not complete or facts.has_unread_content() else ("NG", f"原件缺少{gaps}。")
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
