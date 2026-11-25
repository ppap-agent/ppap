"""R4 U08 reads written requirements in their original row; the checker applies the clause."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.excel import compact, unable
from audit.checker_lib.uaes.resources import pgqp1, pgqp1_counterpart
from audit.engine.rule_engine import Decision, GuardStop, Table

from .control_plan import _CARRIERS, _STAGES, UaesControlPlanRevision3, _fold, _stage
from .scoped_text_r4 import read_facts


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    complete: bool
    read_rows: list[str]


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str
    quote: str


class Order(Statement):
    kind: Literal["oldest_first", "different_order", "mention", "unknown"]


class OrderReading(Reading):
    facts: list[Order]


class AuditItem(Statement):
    kind: Literal["type_test", "annual_material", "dimension_only_excluded", "other_annual", "other", "unknown"]
    name: str
    translation: str | None


class AuditReading(Reading):
    facts: list[AuditItem]


class Application(Statement):
    name: str
    kind: Literal["test_named", "test_instruction", "mention", "unknown"]


class ApplicationReading(Reading):
    facts: list[Application]


def _read_rows(task, rows, instruction, model):
    """One bounded request per reading step. Identical original rows share a reading, not a location."""
    if not rows:
        return []
    unique = list(dict.fromkeys(tuple(row.items()) for row in rows))
    indices = {row: i for i, row in enumerate(unique)}
    keys = {f"R{i}|{k}": (i, k, text) for i, row in enumerate(unique) for k, text in row}
    fields = {key: text for key, (_, _, text) in keys.items()}
    output = [[] for _ in unique]
    try:
        raw = read_facts(
            task,
            sources=fields,
            instructions=instruction + "R编号代表独立的原行，禁止跨编号补义。source填完整的输入键；quote逐字抄该字段全文，含否定和例外。read_rows逐个列出已读完的R编号；完整读完所有行才标complete。",
            schema=model.model_json_schema(),
        )
        read = model.model_validate(raw)
        if not read.complete or set(read.read_rows) != {f"R{i}" for i in range(len(unique))}:
            raise unable("未能完整读取表格范围。")
        for f in read.facts:
            if not f.quote.strip() or fields.get(f.source) != f.quote:
                raise unable("不能把读取的事实对应到指定原行的完整文字。")
            i, k, _ = keys[f.source]
            output[i].append(f.model_copy(update={"source": k}))
    except (ValidationError, GuardStop) as exc:
        if isinstance(exc, GuardStop) and exc.decision != Decision.UNABLE:
            raise
        return [None] * len(rows)
    return [output[indices[tuple(row.items())]] for row in rows]


def _row_fields(row, names=None):
    """Capture the literal cell and its actual merged anchor; never append a neighbouring row."""
    sheet = row.sheet
    if sheet is None:
        raise unable("未能定位控制计划原行。")
    if names is None:
        columns = {c.col_index for c in sheet.scan_non_empty(row_min=row.row, row_max=row.row, col_min=1, col_max=sheet.ws.max_column)}
        columns.update(row.columns.values())
    else:
        columns = {row.columns[k] for k in names if k in row.columns}
    labels = {col: name for name, col in row.columns.items()}
    fields = {}
    for col in sorted(columns):
        merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row.row, col)
        cell = sheet.ws.cell(merged.min_row, merged.min_col) if merged else sheet.ws.cell(row.row, col)
        if cell.value is not None and str(cell.value).strip():
            fields[labels.get(col, f"第{col}列")] = (str(cell.value), cell.row, cell.column)
    return fields


def _text(fields):
    return {key: value[0] for key, value in fields.items()}


_NAMES = ("CP产品特性", "CP过程特性", "CP规格公差")
_ORDER_PROMPT = (
    "读同一工序行的取用顺序，oldest_first指当前明确要求先入库批次先领/先发，含等义描述；different_order指明确不执行此顺序或要求其他顺序。"
    "历史、假设、示例、仅列名称、时限、效率、培训内容都不等于现行要求，记mention；无顺序陈述返回空数组。须连同本行其他单元格理解，不截断否定。"
)
_AUDIT_PROMPT = (
    "这是同一条产品审核项目及描述。分别摘每一项实际定义的型式试验(type_test)、年度材质检测(annual_material)、仅年度复核尺寸且明确排除型式试验和年度材质检测"
    "(dimension_only_excluded)、另有年度确认但性质未定(other_annual)、其他事项(other)。否定或举例不是实际定义，不用相邻行补义。"
    "有多个事项须分开列。name逐字抄本行标识该事项的完整项目名称，不用‘年度’‘试验’等上位词代替具体名称。若同一原行并列写出同一事项的另一语言名称，translation填该原文名称，否则null。quote须出自给出该性质或限定的字段。"
)
_APPLICATION_PROMPT = (
    "PG开头字段是所查项目及其原行定义，不能作为控制计划证据。name照抄PG项目名；仅引用本行控制计划字段。test_named指产品/过程/规格公差列实际命名该项；"
    "test_instruction指本行明确给出针对同一试验的分析或实施要求（方法、规格、频次、控制等）；注意同对象不同试验，不能把无关要求当成它的分析。"
    "同一字段兼有两者则分别摘。被否定、假设、历史或只是例子的标mention，不得当实际项目。"
)


class ControlPlanRequirementsR4(UaesControlPlanRevision3):
    @rule("同环节逐行核对追溯与实际取用顺序")
    def rule_2(self, ctx):
        rows = self.plan(ctx)
        if not self.plan_column(ctx, "CP规格公差"):
            return super().rule_2(ctx)  # preserves the existing distinction between missing and unlocated heading
        problems, unknown, notes = [], [], []
        pending, states = [], {}
        for stage, _ in _STAGES:
            mine, possible = [], []
            for row in rows:
                stages = {_stage(name) for name in row.owners()}
                if stage in stages:
                    (mine if len(stages) == 1 else possible).append(row)
            if not mine:
                (unknown if possible else problems).append(f"未能确认{stage}环节行" if possible else f"缺少{stage}环节")
                continue
            carrier = next((r for r in mine if any(word in _fold(self.spanned(r, "CP规格公差")) for word in _CARRIERS)), None)
            if carrier:
                notes.append(f"{stage}追溯标识：{self.at(carrier, 'CP规格公差')}")
            else:
                (unknown if possible or self.graphic_in_blank(mine, "CP规格公差") else problems).append(f"{stage}规格/公差列未读到明确的追溯标识载体")
            if stage != "进料":
                states[stage] = (mine, possible, set())
                for row in mine:
                    fields = _row_fields(row)
                    # Operation and step establish the scope but can themselves contain instructions. Retain
                    # written words, not numeric-only entries that cannot express an ordering requirement.
                    fields = {k: v for k, v in fields.items() if any(c.isalpha() for c in v[0])}
                    plain = {k: v for k, v in fields.items() if k not in ("工序名称", "CP工序号") and _fold(v[0]) not in _CARRIERS}
                    keys = {k for k, v in plain.items() if k in ("CP规格公差", "CP控制方法") and _fold(v[0]) in ("先进先出", "先入先出", "fifo")}
                    if keys and set(plain) == keys and _fold(row.operation) in dict(_STAGES)[stage]:
                        states[stage][2].add("oldest_first")
                        for k in keys:
                            text, r, c = fields[k]
                            self.cite(row.sheet, r, c)
                            notes.append(f"{stage}{row.where}「{text}」")
                    else:
                        pending.append((stage, row, fields))
        if problems:
            return "NG", "；".join(problems + unknown + notes) + "。"
        readings = _read_rows("cp_fifo_order", [_text(f) for _, _, f in pending], _ORDER_PROMPT, OrderReading)
        for (stage, row, fields), facts in zip(pending, readings, strict=True):
            seen = states[stage][2]
            if facts is None:
                seen.add("unknown")
                continue
            for fact in facts:
                seen.add(fact.kind)
                if fact.kind in ("oldest_first", "different_order"):
                    _, r, c = fields[fact.source]
                    self.cite(row.sheet, r, c)
                    notes.append(f"{stage}{row.where}「{fact.quote}」")
        for stage, (mine, possible, seen) in states.items():
            if "unknown" in seen or {"oldest_first", "different_order"} <= seen:
                unknown.append(f"{stage}未完整确认现行取用顺序，或同环节存在互不一致的要求")
            elif "oldest_first" not in seen:
                drawings = any(self._resource_graphic(r.sheet, r.row, col) for r in mine for col in r.columns.values())
                (unknown if possible or drawings else problems).append(f"{stage}未确认先入批次先领/先发的现行要求")
        status = "NG" if problems else "UNABLE" if unknown else "PASS"
        return status, "；".join(problems + unknown + notes) + "。"

    def _audit_rows(self, ctx, plan):
        """Bind project + description under the same product audit heading, not subsequent numbered sections."""
        rows = []
        found_table = False
        with self.reading(ctx, plan, pgqp1):
            for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                height, width = self.extent(sheet)
                cells = sheet.scan_non_empty(row_min=1, row_max=height, col_min=1, col_max=width)

                def heading(cell):
                    text = re.sub(r"^\d+[.、]?", "", compact(cell.value()))
                    return any(text.startswith(compact(t)) for t in pgqp1_counterpart.PRODUCT_AUDIT_TITLE)

                titles = [c for c in cells if heading(c)]
                for title in titles:
                    start = title.row_index
                    end = next((c.row_index for c in titles if c.row_index > start), height + 1)
                    item_labels = {*map(compact, pgqp1_counterpart.PRODUCT_AUDIT_ITEM), "项目", "items"}
                    content_labels = {*map(compact, pgqp1_counterpart.PRODUCT_AUDIT_CONTENT), "内容描述", "contentdescription", "contentdescription/内容描述"}
                    head = next((c for c in cells if start < c.row_index < end and compact(c.value()) in item_labels), None)
                    desc = next((c for c in cells if head and c.row_index == head.row_index and compact(c.value()) in content_labels), None)
                    if head is None or desc is None:
                        raise unable("PGQP1产品审核要求的项目、内容描述两列表头未能完整定位。")
                    found_table = True
                    for r in range(head.row_index + 1, end):
                        title_cell = sheet.ws.cell(r, title.col_index)
                        if re.match(r"^\s*\d+[.、]\s*[^\d]", str(title_cell.value or "")):
                            span = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, r, title.col_index)
                            if span and all(span.min_col <= c.col_index <= span.max_col for c in (head, desc)):
                                break  # a new numbered heading spans both columns, not a numbered item
                        fields = {}
                        for key, c in (("项目", head.col_index), ("内容", desc.col_index)):
                            merge = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, r, c)
                            cell = sheet.ws.cell(merge.min_row, merge.min_col) if merge else sheet.ws.cell(r, c)
                            if merge and merge.min_col <= min(head.col_index, desc.col_index) and merge.max_col >= max(head.col_index, desc.col_index):
                                raise unable(f"PGQP1第{r}行横跨项目和内容两栏，不能拆为独立的项目和描述。")
                            if cell.value is not None and str(cell.value).strip():
                                fields[key] = str(cell.value)
                                self.cite(sheet, r, c)
                        if fields:
                            rows.append((f"PGQP1「{sheet.title}」第{r}行", fields))
        if not found_table or not rows:
            raise unable("未取得PGQP1产品审核要求表的可读项目行，不能据此断言没有定义试验。")
        return rows

    @rule("按PGQP1产品审核项目逐行核对实际定义的型式试验")
    def rule_10(self, ctx):
        plan, why = self.plan_only(ctx)
        if plan is None:
            return "NG", why + "，请补充后复审。"
        required, notes, unknown = [], [], []
        audit = self._audit_rows(ctx, plan)
        # A bare exact classification used as the sole item defines itself; any descriptive text
        # (including restrictions in either column) is read together instead of relying on keywords.
        readings = [None] * len(audit)
        pending = []
        for i, (_, fields) in enumerate(audit):
            if fields.keys() == {"项目"} and (kind := {"型式试验": "type_test", "年度材质检测": "annual_material"}.get(_fold(fields["项目"]))):
                readings[i] = [AuditItem(kind=kind, name=fields["项目"], quote=fields["项目"], source="项目", translation=None)]
            else:
                pending.append(i)
        for i, facts in zip(pending, _read_rows("cp_product_audit_item", [audit[i][1] for i in pending], _AUDIT_PROMPT, AuditReading), strict=True):
            readings[i] = facts
        for (where, fields), facts in zip(audit, readings, strict=True):
            if not facts or any(not n.strip() or not any(n in value for value in fields.values()) for f in facts for n in (f.name, *([f.translation] if f.translation is not None else []))):
                unknown.append(f"{where}：未取得能对应原行的所有项目定义")
                continue
            for fact in facts:
                note = f"{where}「{fact.name}」：{fact.quote}"
                if fact.kind in ("type_test", "annual_material"):
                    required.append((fact.name, {fact.name, fact.translation} - {None}, fields, note))
                elif fact.kind in ("other_annual", "unknown"):
                    unknown.append(f"{note}（类别未明确）")
                else:
                    notes.append(note)
        rows = self.plan(ctx) if required else []
        candidates = []
        for row in rows:
            fields = _row_fields(row, _NAMES)
            for i, (name, names, pg, _) in enumerate(required):
                if any(_fold(n) in _fold(v[0]) for n in names for v in fields.values()):
                    cells = _row_fields(row, (*_NAMES, "评价测量技术", "CP频率", "CP控制方法"))
                    sources = {"PG项目名": name, **{"PG原" + k: v for k, v in pg.items()}, **_text(cells)}
                    candidates.append((i, row, cells, sources))
        readings = _read_rows("cp_test_application", [source for _, _, _, source in candidates], _APPLICATION_PROMPT, ApplicationReading)
        covered, undecided = {}, set()
        for (i, row, fields, _), facts in zip(candidates, readings, strict=True):
            name, names, _, _ = required[i]
            if facts is None or any(f.source not in fields or f.name != name or f.kind == "unknown" for f in facts):
                undecided.add(i)
                continue
            if (
                any(f.kind == "test_named" and f.source in _NAMES and any(_fold(n) in _fold(f.quote) for n in names) for f in facts)
                and any(f.kind == "test_instruction" for f in facts)
                and i not in covered
            ):
                for f in facts:
                    _, r, c = fields[f.source]
                    self.cite(row.sheet, r, c)
                covered[i] = f"控制计划{row.where}：" + "；".join(dict.fromkeys(f.quote for f in facts))
        absent = []
        incomplete = len(covered) < len(required) and (not rows or any(not self.plan_column(ctx, k) or self.graphic_in_blank(rows, k) for k in _NAMES))
        for i, (_, _, _, note) in enumerate(required):
            if i in covered:
                notes.append(f"{note}；{covered[i]}")
            else:
                (unknown if incomplete or i in undecided else absent).append(f"{note}；未在控制计划指定列读到同一行对应的项目及分析")
        status = "NG" if absent else "UNABLE" if unknown else "PASS" if required else "NA"
        prefix = "产品审核表未定义本条范围内的型式试验或年度材质检测：" if status == "NA" else ""
        return status, prefix + "；".join(absent + unknown + notes) + "。"
