"""UAES U07 revision 4; restrict special-characteristic coverage to the current material's own rows."""

from __future__ import annotations

import re
from dataclasses import replace

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.fmea import _LISTS, _PART_LABELS, Decision, Table, original_name, own_class, special_list, unable
from audit.checker_lib.uaes.identity import pair_identity
from audit.engine.rule_engine.contracts import ColumnModel

from .fmea import UaesPfmeaRevision3, characteristic_name, mark_class, mark_tokens, operation_labels, substantive, tolerance, unknown_marks
from .workbook_drawings_r4 import drawing_regions

_LIST_FIELDS = (
    ColumnModel(canonical_name="清单工序", section_name="特殊特性区", aliases=("工序名称", "过程名称", "Process Name")),
    ColumnModel(canonical_name="过程特性值", section_name="特殊特性区", aliases=("过程特性值", "过程特性规格")),
    ColumnModel(canonical_name="过程特性类别", section_name="特殊特性区", aliases=("过程特殊特性符号", "过程特性符号", "过程特性类别")),
)
_FMEA_OPERATION = ColumnModel(canonical_name="分析工序", section_name="PFMEA失效区", aliases=("工序名称", "过程名称", "Process Name", "Operation Name"))
_BASE = UaesPfmeaRevision3.EXTRA_RESOURCE


def action_failure_witness(description: str, function: str, mode: str) -> str | None:
    """For an aggregate row, use the source's own action-to-failure attribution, never a process-specific lookup."""
    source = operation_labels(description)
    actions = set().union(*(operation_labels(label) for label in re.split(r"[，,、；;]", function)))
    for entry in re.split(r"[；;\n]", mode):
        match = re.fullmatch(r"\s*([^:：]+)[:：]\s*(.+)", entry)
        if match and substantive(match[2]) and source & actions & operation_labels(match[1]):
            return f"PFD“{description}”，PFMEA“{function}”中该工艺的失效模式为“{entry.strip()}”"
    return None


def _field_key(text):
    return re.sub(r"[\s./]", "", str(text)).casefold()


_PART_FIELDS = {_field_key(x) for x in (*_PART_LABELS, *special_list.PART_NUMBER, "Part No/零件号", "零件号 Part No.")}
_PART_ROWS = _PART_FIELDS | {_field_key(x) for x in _BASE.columns["零件"].aliases} | {_field_key("产品名称/零件号 Name / Code")}


def _field_heading(raw: str) -> bool:
    return bool(re.search(r"(?:号|名称|日期|版本|签名|签字|\b(?:no\.?|number|name|date|revision|signature))\s*[:：]?$", raw, re.I))


def _number_field(value):
    raw = str(value or "").strip()
    return raw if raw and pair_identity(raw) and not _field_heading(raw) and not re.search(r"[:：\n]", raw) else None


class UaesPfmeaRevision4(UaesPfmeaRevision3):
    pgqp1_check_table_first = True
    BATCH_SCORE_DESCRIPTIONS = True
    _section = _BASE.sections["特殊特性区"]
    EXTRA_RESOURCE = replace(
        _BASE,
        columns={
            **_BASE.columns,
            "零件": _BASE.columns["零件"].model_copy(update={"aliases": (*_BASE.columns["零件"].aliases, *special_list.PART_NUMBER, "Part Number", "Part No.")}),
            "要求": _BASE.columns["要求"].model_copy(update={"aliases": (*_BASE.columns["要求"].aliases, "要求", "Requirement")}),
            **{field.canonical_name: field for field in (*_LIST_FIELDS, _FMEA_OPERATION)},
        },
        sections={
            **_BASE.sections,
            "特殊特性区": _section.model_copy(
                update={
                    "header_anchors": (*_section.header_anchors, "过程特性名称", "过程特性值", "过程特性类别"),
                    "row_anchors": (*_section.row_anchors, "过程特性名称", "过程特性值"),
                }
            ),
        },
    )

    def score_description_reader(self, items):
        from .score_descriptions_r4 import score_reader

        return score_reader(self, items)

    def process_witness(self, description: str, characteristic: str, item):
        found = super().process_witness(description, characteristic, item)
        if found is not None:
            return found
        mode = item.values.get("失效模式", "")
        if (sheet := getattr(item, "sheet", None)) is not None and substantive(mode):
            columns = self.__dict__.setdefault("_operation_columns", {})
            if id(sheet.ws) not in columns:
                field = self.section(sheet, "PFMEA失效区").columns(optional=("分析工序",)).get("分析工序")
                columns[id(sheet.ws)] = field.header_col if field else None
            if col := columns[id(sheet.ws)]:
                named = str(self.raw(sheet, item.row, col) or "").strip()
                if operation_labels(named) & operation_labels(description):
                    self.cite(sheet, item.row, col)
                    return f"PFMEA工序名称“{named}”，同行失效模式“{mode}”"
        return action_failure_witness(description, item.values.get("功能", ""), mode)

    def _header_parts(self, sheet, stop):
        """Only a part-number field above the actual table, never unrelated codes or values in other files."""
        _, cols = self.extent(sheet)
        parts = set()
        for cell in sheet.scan_non_empty(row_min=1, row_max=stop, col_min=1, col_max=cols):
            label, *inline = re.split("[:：]", str(cell.value()), maxsplit=1)
            if _field_key(label) not in _PART_FIELDS:
                continue
            row, col = cell.row_index, cell.col_index
            if inline and inline[0].strip():
                value = inline[0]
            else:
                merge = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row, col)
                right = (merge.max_col if merge else col) + 1
                value = self.raw(sheet, row, right) if right <= cols else None
                if _field_heading(str(value or "").strip()):
                    # Adjacent headings form a header row; their respective values are below, not each other.
                    row = (merge.max_row if merge else row) + 1
                    value = self.raw(sheet, row, col) if row <= stop and _number_field(self.raw(sheet, row, right)) else None
                else:
                    col = right
            if number := _number_field(value):
                self.cite(sheet, row, col)
                parts.add(number)
        return parts

    def part_number(self, ctx):
        found = set().union(*(self._header_parts(sheet, self.section(sheet, "PFMEA失效区").resolved.header_row - 1) for sheet in self.sheets(ctx)))
        if len(found) == 1:
            return next(iter(found)), ""
        return None, "PFMEA表头未取得唯一、明确标为零件号或物料编号的原值"

    def pgqp1_part_scope(self, sheet, part):
        found = self._header_parts(sheet, self.section(sheet, "重要特性区").resolved.header_row - 1)
        return ((part in found) if len(found) == 1 else None), sorted(found)

    def _same_part_lists(self, ctx, part, project):
        classified, unresolved = [], []
        lists = {item.audit_id: item for kind in _LISTS for item in self.batch_files(kind)}
        for item in lists.values():
            label = f"特殊特性清单“{original_name(item)}”"
            with self.reading(ctx, item, special_list):
                sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
                if not sheets:
                    unresolved.append(f"{label}的清单表格未能完整读取")
                for sheet in sheets:
                    section = self.section(sheet, "特殊特性区")
                    columns = section.columns(optional=("零件", "特性名称", "过程特性名称", "特性值", "特性类别", *(field.canonical_name for field in _LIST_FIELDS)))
                    header = section.resolved.header_row
                    if "零件" in columns and _field_key(self.raw(sheet, header, columns["零件"].header_col)) not in _PART_ROWS:
                        columns = {k: v for k, v in columns.items() if k != "零件"}
                    stated = self._header_parts(sheet, header - 1)
                    if "零件" not in columns and len(stated) != 1:
                        unresolved.append(f"{label}未能从表头确认唯一物料编号，也没有逐行的物料栏")
                        continue
                    theirs = self.project([sheet])
                    if theirs and project and theirs != project:
                        continue
                    matched, rows = False, []
                    for row in section.rows_from(columns):
                        # Only a row-bearing identity, or one genuinely single-material sheet
                        # whose identity is stated in its header, can own a characteristic.
                        if "零件" in columns:
                            owner = self.raw(sheet, row.row_index, columns["零件"].header_col)
                            if not pair_identity(owner):
                                unresolved.append(f"{label}第{row.row_index}行未能确认所属物料")
                                continue
                            text = str(owner).strip()
                            same = text == part
                            # Only a combined name/code column licenses separating an ID from the part name.
                            if not same and _field_key(self.raw(sheet, header, columns["零件"].header_col)) not in _PART_FIELDS:
                                ids = {token for token in re.findall(r"[A-Za-z0-9_.\-/]+", text) if any(char.isdigit() for char in token)}
                                if len(ids) != 1:
                                    unresolved.append(f"{label}第{row.row_index}行的名称/编号栏未能分出唯一的完整编号")
                                    continue
                                same = ids == {part}
                        else:
                            same = stated == {part}
                        if not same:
                            continue
                        matched = True
                        operation = str(row.value("清单工序") or "").strip() if "清单工序" in columns else ""
                        for keys in (("特性名称", "特性值", "特性类别"), ("过程特性名称", "过程特性值", "过程特性类别")):
                            name_key, spec_key, mark_key = keys
                            if name_key not in columns:
                                continue
                            name = str(row.value(name_key) or "").strip()
                            spec = str(row.value(spec_key) or "").strip() if spec_key in columns else ""
                            mark = str(self.raw(sheet, row.row_index, columns[mark_key].header_col) or "").strip() if mark_key in columns else ""
                            if not (name or spec or mark):
                                continue
                            # Text headed as a process characteristic, but actually carrying only a
                            # quoted instruction reference, needs review, not a fabricated name/spec pair.
                            if mark_key not in columns and not spec:
                                if name_key == "过程特性名称":
                                    unresolved.append(f"{label}第{row.row_index}行过程特性没有可定位的规格/符号")
                                continue
                            cell = sheet, row.row_index, columns[name_key].header_col
                            ctx.record_execution()  # evaluated ownership of this complete native row
                            rows.append((label, row.row_index, name, spec, mark, cell, operation if name_key == "过程特性名称" else ""))
                    if not matched:
                        continue
                    if not theirs or not project:
                        unresolved.append(f"{label}有同物料编号行，但项目未写明或不可比较")
                    elif not rows:
                        unresolved.append(f"{label}已确认同项目同物料，但未能读取该物料的特性行")
                    else:
                        classified.extend(rows)
        return classified, unresolved

    def _category_graphic(self, item, column):
        """Do not call an apparently empty native cell blank if some DrawingML covers it."""
        path = str(self.audit_file.file_url)
        if path.lower().endswith(".pdf"):
            return self.drawn(item.sheet, item.row, column) is not False
        if not path.lower().endswith((".xlsx", ".xlsm")):
            return True
        inventory = self.__dict__.setdefault("_category_regions", {})
        if item.sheet.title not in inventory:
            inventory[item.sheet.title] = drawing_regions(path, item.sheet.title)
        regions = inventory[item.sheet.title]
        return regions is None or any(top <= item.row <= bottom and left <= column <= right for top, left, bottom, right in regions)

    def _check_locally_marked_features(self, items):
        conventions = set()
        for item in items:
            suffix = own_class(item.values.get("功能", ""))
            code = self.raw(item.sheet, item.row, item.columns["类别"]) if "类别" in item.columns else ""
            if suffix and code and not unknown_marks(code) and mark_class(suffix) == mark_class(code):
                conventions.add((id(item.sheet.ws), mark_class(suffix)))

        for item in items:
            name, name_col = item.values.get("功能", ""), item.columns.get("功能")
            suffix = own_class(name)
            if not suffix or (id(item.sheet.ws), mark_class(suffix)) not in conventions:
                continue
            category = item.columns.get("类别")
            if name_col is None or category is None or str(self.raw(item.sheet, item.row, name_col) or "").strip() != name:
                continue
            if self.raw(item.sheet, item.row, category) not in (None, ""):
                continue
            place = self.at(item, "功能", "类别")
            if self._category_graphic(item, category):
                raise unable(f"{place}的类别栏可能含图形，不能认定漏标，需人工复核。")
            return f"{place}“{name}”按本表标注为{suffix}类，但该行独立类别栏为空，已知特殊特性漏标（第1项）。"
        return None

    def _qualitative(self, items, name, spec, mark, operation):
        """Match a qualitative process characteristic only on all four named keys."""
        wanted, spec_key = characteristic_name(name), tolerance(spec)
        if not wanted or not spec_key or wanted == spec_key or not operation:
            return None
        descriptors, matches = {}, []
        for item in items:
            sheet = item.sheet
            if sheet.title not in descriptors:
                column = self.section(sheet, "PFMEA失效区").columns(optional=("分析工序",)).get("分析工序")
                descriptors[sheet.title] = (column.header_col if column else None, self.step_column(sheet))
            col, step = descriptors[sheet.title]
            if col is not None:
                actual = self.raw(sheet, item.row, col)
            elif step is not None:
                start = next((i for i in range(item.row, 0, -1) if self.raw(sheet, i, step) not in (None, "")), None)
                actual = self.raw(sheet, start, item.columns["功能"]) if start and "功能" in item.columns else ""
            else:
                actual = ""
            fields = [item.values.get(field, "") for field in ("功能", "要求", "失效模式", "失效起因")]
            if not actual or not substantive(item.values.get("失效模式", "")) or not operation_labels(operation) & operation_labels(str(actual)):
                continue
            if wanted in {characteristic_name(field) for field in fields} and spec_key in {tolerance(field) for field in fields}:
                matches.append(item)
        if not matches:
            return "NG"
        codes = {mark_class(item.values.get("类别", "")) for item in matches}
        if len(codes) > 1 or any(unknown_marks(item.values.get("类别", "")) for item in matches):
            return None
        for item in matches[:2]:
            self.at(item, "功能", "要求", "类别", "失效模式")
        return "PASS" if codes == {mark_class(mark)} else "NG"

    @rule("按同项目同物料范围核对特殊特性")
    def rule_3(self, ctx):
        items = self.items(ctx)
        # The shared list reader strips glyph-only classes from item.values. Restore the exact
        # dedicated-cell value here so signed marks such as ★ remain actual evidence in R4.
        for item in items:
            if "类别" in item.columns:
                raw = self.raw(item.sheet, item.row, item.columns["类别"])
                mark = str(raw or "").strip()
                item.values["类别"] = mark
        if not any("类别" in item.columns for item in items):
            return "NG", "PFMEA未设特殊特性或分类列（第1项）。"
        if not any(mark_tokens(item.values.get("类别", "")) for item in items):
            if self.unreadable() or any(self._category_graphic(item, item.columns["类别"]) for item in items if "类别" in item.columns):
                raise unable("PFMEA特殊特性/分类列中的原件图形或页面未能读取，不能确认该列是否空白。")
            return "NG", "可读原件的PFMEA特殊特性/分类列无符号标识（第1项）。"
        if missing := self._check_locally_marked_features(items):
            return "NG", missing
        self.require_readable(_LISTS)
        other = self.untyped()
        if not any(self.batch_files(kind) for kind in _LISTS):
            if other:
                raise unable(f"同批{len(other)}份原件类型未确定，不能判定特殊特性清单是否缺件。")
            self.need("特殊特性清单")
            return "NG", "同批未提交独立特殊特性清单（第2项）。"
        part, unread = self.part_number(ctx)
        if not part:
            raise unable(f"{unread}，无法确认本物料的清单范围。")
        data, unresolved = self._same_part_lists(ctx, part, self.project(self.sheets(ctx)))
        if other:
            unresolved.append(f"同批另有{len(other)}份类型未识别的原件")
        if not data:
            if unresolved:
                raise unable("；".join(unresolved) + "，须人工核实第2项。")
            self.need("特殊特性清单", part=part)
            return "NG", f"同批独立特殊特性清单没有同项目、本物料号{part}的特性行（第2项）。"
        # No unknown symbol is passed or assigned a grade just because two spellings look the same.
        # For symbols with a legend the meaning still requires separate reading. Known signed codes,
        # including 3 and 5, continue to compare as distinct values in the revision 3 reader.
        rows, visual_input, qualitative, text_failures, text_pending = [], [], 0, [], []
        for label, number, name, spec, mark, cell, operation in data:
            if other_codes := unknown_marks(mark):
                unresolved.append(f"{label}第{number}行的符号“{'、'.join(other_codes)}”须核对清单图例，不能推断等级")
                continue
            if operation and spec and not re.search(r"\d", spec):
                comparison = self._qualitative(items, name, spec, mark, operation)
                where = f"{label}第{number}行“{operation}／{name}／{spec}／{mark}”"
                if comparison == "PASS":
                    qualitative += 1
                elif comparison == "NG":
                    self.cite(*cell)
                    text_failures.append(f"{where}在PFMEA无同工序、同名、同文字规格且同类别的过程特性")
                else:
                    text_pending.append(f"{where}缺少足以唯一核对的过程特性依据")
            elif not name and not spec:
                visual_input.append((label, number, name, spec, mark, {"mark": cell, "name": cell}))
            else:
                rows.append((f"{label}第{number}行", name, spec, mark, cell))
        if visual_input:
            not_placed, drawings = self.with_drawn_rows(visual_input)
            rows.extend((f"{label}第{number}行", name, spec, mark, cells["mark"]) for label, number, name, spec, mark, cells in not_placed)
        else:
            drawings = []
        failures, notes, pending = self.compare("特殊特性清单", rows, items, causes=True)
        failures += text_failures
        pending += text_pending
        if drawings:
            image_facts = self.item_dimensions(items) if any(name and spec for _, name, spec, _, _ in drawings) else {}
            broken, matched, uncertain = self.compare_drawn(drawings, items, image_facts)
            failures.extend(broken)
            notes.extend(matched)
            pending.extend(uncertain)
        if failures:
            return "NG", f"按本物料{part}的特性核对：" + "；".join(failures[:5]) + "（第1、3、4项）。"
        pending += unresolved
        if pending:
            raise unable("；".join(pending[:5]) + "，须人工核实。")
        return "PASS", f"本物料号{part}的独立特殊特性清单中{len(rows) + len(drawings) + qualitative}项特性均在PFMEA中按名称、规格及符号核对一致。" + ("；" + "；".join(notes[:3]) if notes else "")
