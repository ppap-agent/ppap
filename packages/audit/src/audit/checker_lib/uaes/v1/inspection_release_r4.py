"""Read item-specific customer decisions from a frozen, native-text release, without a prescribed template."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from audit.checker_lib.uaes.v1.reader import compact, version_of
from audit.checker_lib.uaes.v1.scoped_text_r4 import read_facts
from audit.engine.checkers.text_evidence import _fragments, _source
from audit.engine.rule_engine import GuardStop


class _Field(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str
    value: str


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    issuer: _Field
    part: _Field
    supplier: _Field | None
    item: _Field
    specification: _Field | None
    statement: _Field
    action: Literal["accepted", "refused", "unclear"]


class _Decisions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    coverage: Literal["complete", "uncertain"]
    decisions: list[_Decision]


_INSTRUCTIONS = (
    "只读原件中本次顾客对具体尺寸超差项实际作出的处理，不决定审核结果。不返回仅笼统认可整件的文字，不提取填表说明、申请人的建议或历史案例。"
    "每条记录分别摘录签发顾客、其批准对象的零件号、供应商、尺寸项号、原要求（若写有）及顾客决定原句。来源键带原页/原表位置，"
    "所有字段只取明确属于同一份有效决定的内容，不可跨记录/跨附件拼接。不要把发给顾客的申请或第三方说法当成顾客批准。"
    "value必须逐字来自对应source。statement必须照录所在来源的完整原文，保留否定、对象、限制和条件。"
    "accepted只用于已对该项本次实测偏差明确无条件放行；明确拒绝写refused；尚待审批、附条件、相互冲突、对象或范围不明确写unclear。"
    "若放行范围受批号、日期、图版、数值上限或特定实测值等限制而未能核对，action必须为unclear，不可默认为覆盖本次。声明只对本次该项实测差异无条件放行才为accepted。"
    "无法认定出具主体或条目归属，或无法完整识别当前决定时，coverage为uncertain。不得根据推定的惯例补字段。"
)


def read_releases(agent, items):
    """Batch only the pending native releases. An unobserved original cannot prove non-coverage."""
    sources, evidence = {}, {}
    for doc, item in enumerate(items):
        path, digest = _source(item, {})
        if path.suffix.lower() not in {".pdf", ".docx", ".xlsx", ".xlsm", ".xls"} or agent.unread_pages(str(path)):
            continue
        fragments, complete = _fragments(path)
        if not complete or not fragments or len(fragments) > 120:
            continue
        for index, fragment in enumerate(fragments):
            # Distinct documents cannot lend an issuer or subject to one another.
            loc = fragment["locator"]
            key = f"d{doc}:{index}@{loc.get('sheet', loc.get('table', loc.get('page', 'body')))}:{loc.get('cell', loc.get('row', loc.get('paragraph', loc.get('block', index))))}"
            sources[key] = fragment["text"]
            evidence[key] = (version_of(item), {**loc, "source_sha256": digest})
    if not sources:
        return {}
    try:
        data = read_facts("uaes_ir_release", sources=sources, instructions=_INSTRUCTIONS, schema=_Decisions.model_json_schema())
        result = _Decisions.model_validate(data)
    except GuardStop as e:
        if e.status != "UNABLE":
            raise
        return {}
    except ValidationError:
        return {}
    if result.coverage != "complete":
        return {}
    grouped = {}
    for event in result.decisions:
        origin = evidence.get(event.statement.source)
        fields = [f for f in (event.issuer, event.part, event.supplier, event.item, event.specification, event.statement) if f is not None]
        if not origin or any(f.source not in evidence or evidence[f.source][0] != origin[0] for f in fields):
            return {}
        for f in fields:
            text = sources[f.source]
            if not f.value.strip() or f.value not in text:
                return {}
            # A shorter identifier cannot become a different issuer, subject or item.
            if f is not event.statement and not re.search(rf"(?<![\w.±/-]){re.escape(f.value)}(?![\w.±/-])", text, re.ASCII):
                return {}
            if f is event.statement and text.strip() != f.value.strip():
                return {}
        for f in fields:
            version, locator = evidence[f.source]
            agent.witness(locator, sources[f.source], version)
        grouped.setdefault(origin[0], []).append(event)
    return grouped


def item_disposition(events, part, suppliers, index, requirement, unique_index):
    if not events or not part or part == "未写" or "、" in part:
        return "unknown"
    relevant = set()
    for e in events:
        if e.part.value != part or e.item.value != index:
            continue
        if e.issuer.value.strip() not in {"UAES", "联合汽车电子有限公司", "United Automotive Electronic Systems Co., Ltd."}:
            return "unknown"
        if e.supplier and (len(suppliers) != 1 or None in suppliers or e.supplier.value not in suppliers):
            return "unknown"
        if e.specification and compact(e.specification.value) != compact(requirement) or not e.specification and not unique_index:
            return "unknown"
        relevant.add(e.action)
    if not relevant:
        return "other"
    if relevant == {"accepted"}:
        return "covered"
    # Other items or a partial account do not prove absence. A clear refusal does establish no coverage here.
    return "refused" if relevant == {"refused"} else "unknown"
