"""UAES U08 candidate: control plan clauses of UAES v1.0 revision 3 (CP:1-CP:13).

The reader is the UAES control plan implementation that resources.py declares; this leaf adds the
revision 3 clause methods on top of it at run time. A leaf may not import a company implementation,
so the class and its module's helpers are taken from the carrier the shared loader built.
Verdicts are code over cell facts; a model only reads (through the implementation's reading stack).
"""

import re
import sys
import unicodedata

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from resources import RESOURCE

# ---- text helpers (pure) ----------------------------------------------------------------


def _fold(value):
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold()


def _cjk(text):
    return "".join(char for char in text if "一" <= char <= "鿿")


def _name_key(text):
    """A characteristic's name for pairing: its words without the number, the class marks or a bracketed note.

    CP:8 and CP:13 pair by name and tolerance, never by characteristic number ("2#").
    """
    folded = re.sub(r"[（(][^）)]*[）)]", "", _fold(text))
    words = _cjk(folded)
    return words or re.sub(r"^\d+(?:-\d+)?#", "", folded)


# A written tolerance or specification value: digits with a sign, a limit word, a diameter or a fit.
_SPEC = re.compile(r"\d")


def _spec_like(text):
    folded = _fold(text)
    return bool(_SPEC.search(folded)) and any(token in folded for token in ("±", "+", "-", "/", "max", "min", "φ", "ø", "r", "h7", "≤", "≥", "<", ">", "°", "度", "."))


def _tolerance(text):
    """The tolerance written inside a characteristic's text ("熔化温度（650°±15°）"), or ""."""
    folded = _fold(text)
    for inner in re.findall(r"[（(]([^）)]*)[）)]", folded):
        if _spec_like(inner):
            return inner
    rest = re.sub(r"[（(][^）)]*[）)]", "", re.sub(r"^\d+(?:-\d+)?#", "", folded))
    rest = "".join(char for char in rest if not ("一" <= char <= "鿿"))
    # Class marks appended to a name ("尺寸3,5") are no tolerance.
    return rest if _spec_like(rest) and not re.fullmatch(r"[\d,，]+", rest) else ""


def _marks(text):
    """Class marks as a set: "3,5", "3 5" and "3，5" are the same marks."""
    return frozenset(part for part in re.split(r"[,，、/\s;；]+", unicodedata.normalize("NFKC", str(text or "")).strip().casefold()) if part)


# CP:2 traceability carriers and FIFO words (revision 3: 批次号／炉号／序列号／流转卡等; "按批次管理" or "可追溯" alone is no carrier).
_CARRIERS = ("批次号", "批号", "炉号", "序列号", "流水号", "条码", "条形码", "二维码", "流转卡", "随工单", "lotno", "lotnumber", "batchno", "batchnumber", "serialno", "serialnumber")
_FIFO = ("先进先出", "先入先出", "fifo", "按批次先后", "批次先后")
_NOT_FIFO = ("库龄", "库存天数", "保质期", "周转率")
# CP:2 stages: 出货 and 发运 are one stage; 出入库 covers warehousing and issuing.
_STAGES = (
    ("进料", ("进料", "来料", "收货", "原材料", "原料", "物料检验", "iqc", "incoming")),
    ("出入库", ("入库", "出库", "仓", "库存", "储存", "存储", "存放", "领用", "领料", "发料", "storage", "stock", "warehouse")),
    ("出货", ("出货", "发货", "发运", "出荷", "交付", "delivery", "shipping", "outgoing")),
)

# CP:1: operations that do not physically depend on equipment, as the clause lists them.
_NO_DEVICE = ("目视", "手工", "人工", "自然冷却")
_NO_DEVICE_FILLS = ("/", "n/a", "na")

# CP:6: the control plan number label as written in the originals (776/A40 "Control Plan Number控制计划编号").
_PLAN_NUMBER = ("控制计划编号", "控制计划号", "CP编号")

# CP:10: a type test (including the annual material test) and an explicit statement that something is not one.
_TYPE_TEST = ("型式试验", "typetest")
_MATERIAL = ("材料", "材质", "material")
_ANNUAL = ("/年", "每年", "年度", "1次/年", "peryear", "eachyear", "yearly", "annual")
_NOT_TYPE_TEST = ("非型式试验", "不是型式试验", "不属于型式试验", "nottypetest")


def _stage(name):
    text = _fold(name)
    return next((stage for stage, words in _STAGES if any(word in text for word in words)), None)


# ---- CP:13 sampling -----------------------------------------------------------------------

_EVERY = ("全检", "100%", "每件", "每个零件", "逐件")
_UNITS = (
    ("批", ("每批", "每个批次", "批次", "perbatch", "eachlot", "perlot", "eachbatch")),
    ("班", ("每班", "eachshift", "pershift")),
    ("天", ("每天", "每日", "/天", "天/次", "perday", "eachday", "daily")),
    ("周", ("每周", "/周", "perweek", "weekly")),
    ("月", ("每月", "/月", "permonth", "monthly")),
    ("年", ("每年", "/年", "peryear", "eachyear", "yearly")),
    ("箱", ("每箱", "eachbox", "perbox")),
)


def _sampling(text):
    """(every piece, sample size, per cavity, hours between samples, other frequency unit) written in `text`."""
    folded = _fold(text)
    every = any(word in folded for word in _EVERY)
    size = re.search(r"(\d+)(件|个|pcs|pc|只|块)", folded)
    per_cavity = bool(size) and any(word in folded[size.end() : size.end() + 6] for word in ("/腔", "/cav", "每腔"))
    hours = None
    found = re.search(r"(\d+(?:\.\d+)?)次/(\d+(?:\.\d+)?)?(?:h|小时|hr)", folded)
    if found:
        hours = float(found.group(2) or 1) / float(found.group(1))
    elif found := re.search(r"每(\d+(?:\.\d+)?)?(?:h|小时)", folded):
        hours = float(found.group(1) or 1)
    elif found := re.search(r"(\d+(?:\.\d+)?)(?:h|小时)(?:/次|一次)", folded):
        hours = float(found.group(1))
    elif found := re.search(r"/(\d+(?:\.\d+)?)(?:h|小时)", folded):
        hours = float(found.group(1))
    unit = next((name for name, words in _UNITS if any(word in folded for word in words)), None)
    return every, (int(size.group(1)) if size else None), per_cavity, hours, unit


def _compare(required, size_text, freq_text, has_size_column, m):
    """(True / False / None for undecided, reasons): whether one control plan row samples as much and as often as PGQP1 requires."""
    p_every, p_size, _p_cavity, p_hours, p_unit = _sampling(required)
    every = _sampling(f"{size_text} {freq_text}")[0]
    state, why = True, []

    def undecided(reason):
        nonlocal state
        state = None if state is True else state
        why.append(reason)

    def fails(reason):
        nonlocal state
        state = False
        why.append(reason)

    # Full inspection (全检, 100%, 每件) is stricter than any sampling plan.
    if every:
        return True, why
    # Sample size: not smaller than PGQP1's.
    if p_every:
        fails("PGQP1要求100%检验，控制计划不是全检")
    elif p_size is not None:
        _every, size, per_cavity, _hours, _unit = _sampling(size_text)
        if not has_size_column or m.compact(size_text) in m._PLACEHOLDERS:
            fails("样本容量缺失")
        elif size is None:
            undecided(f"样本容量“{' '.join(size_text.split())}”读不出件数")
        elif size < p_size and per_cavity:
            undecided(f"样本容量“{' '.join(size_text.split())}”按腔计，腔数未知")
        elif size < p_size:
            fails(f"样本容量{size}小于PGQP1的{p_size}")
    # Frequency: no sparser than PGQP1's (interval not longer); a per-batch plan must reach every batch.
    if p_hours is not None or p_unit is not None:
        _every, _size, _cavity, hours, unit = _sampling(freq_text)
        if m.compact(freq_text) in m._PLACEHOLDERS:
            fails("频率缺失")
        elif p_hours is not None and hours is not None:
            if hours > p_hours:
                fails(f"频率每{hours:g}小时一次，稀于PGQP1的每{p_hours:g}小时一次")
        elif p_hours is None and unit is not None and unit == p_unit:
            pass
        else:
            undecided(f"频率“{' '.join(freq_text.split())}”与PGQP1的写法单位不同，检查程序不能比较疏密")
    return state, why


# ---- the clause methods -------------------------------------------------------------------


class _Revision3:
    """Revision 3 clause methods; `self._m` is the module of the UAES control plan reader."""

    _m = None

    # CP:1 -------------------------------------------------------------------------------
    @rule("全部工序生产资源")
    def rule_1(self, ctx):
        m = self._m
        rows = self.plan(ctx)
        if not self.plan_column(ctx, "CP生产资源"):
            raise m.unable("控制计划表头未能定位“机器、装置、夹具、工装”或其他生产资源列，检查程序不能确认各工序的生产资源，需人工复核。")
        operations = {}
        start = 0
        for index, row in enumerate(rows):
            if index == 0 or row.operation != rows[index - 1].operation:
                start = row.row
            if row.operation:
                operations.setdefault((row.operation, start), []).append(row)
        if not operations:
            raise m.unable("控制计划没有读出写有工序名称的业务行，不能按空范围通过。")
        lacking, manual = [], []
        for (name, _start), items in operations.items():
            main = items[0]
            # A resource in the operation's first row, or in a cell merged over its rows, is inherited by all its rows.
            value = _fold(self.spanned(main, "CP生产资源"))
            label = f"“{' '.join(name.split())}”（{self.at(main, 'CP生产资源')}）"
            if value in _NO_DEVICE_FILLS:
                if any(word in _fold(name) for word in _NO_DEVICE):
                    manual.append(label)
                    continue
                lacking.append(label + f"填“{self.spanned(main, 'CP生产资源').strip()}”，工序名称不是不依赖设备的手工/目视/自然冷却工序")
            elif value in m._PLACEHOLDERS:
                lacking.append(label)
        if lacking:
            if where := self.graphic_in_blank([items[0] for items in operations.values()], "CP生产资源"):
                raise m.unable(f"控制计划{where}生产资源格内有图形或无法对应原页位置，检查程序未能读取其内容，需人工复核。")
            return "NG", f"控制计划共{len(operations)}个工序，其中{len(lacking)}个工序主行的生产资源为空或不合规：{'、'.join(lacking[:12])}{'等' if len(lacking) > 12 else ''}。"
        note = f"其中{'、'.join(manual)}为不依赖设备的工序，填“/”或“N/A”属合规" if manual else ""
        return "PASS", f"控制计划全部{len(operations)}个工序的主行都写明了生产资源{('；' + note) if note else ''}。"

    # CP:2 -------------------------------------------------------------------------------
    def row_text(self, row):
        """Every written cell of a control plan row, comparable."""
        if row.sheet is None:
            return _fold(" ".join(row.cells.values()))
        _rows, width = self.extent(row.sheet)
        return " ".join(text for _column, text in sorted(self._cells(row.sheet, row.row, width)))

    @rule("生产追溯及先进先出实际措施")
    def rule_2(self, ctx):
        m = self._m
        rows = self.plan(ctx)
        if not self.plan_column(ctx, "CP规格公差"):
            if any("规格" in text or "规范" in text or "公差" in text for text in self.header_texts(ctx)):
                raise m.unable("控制计划表头有规格/公差字样，但检查程序未能定位“产品/过程规格/公差”列，需人工复核。")
            return "NG", "控制计划没有“产品/过程规格/公差”列，追溯标识无处写明。"

        def spec(row):
            return _fold(self.spanned(row, "CP规格公差"))

        def of(stage):
            return [row for row in rows if any(_stage(name) == stage for name in row.owners())]

        problems, unread = [], []
        for stage, _words in _STAGES:
            mine = of(stage)
            if not mine:
                problems.append(f"控制计划中没有识别出{stage}环节的工序")
            elif not any(word in spec(row) for row in mine for word in _CARRIERS):
                problems.append(f"{stage}环节（{self.at(mine[0], 'CP规格公差')}起）的“产品/过程规格/公差”列没有写明追溯标识载体（批次号／炉号／序列号／流转卡等）")
                unread += mine
        fifo_where = []
        for stage, label in (("出入库", "出入库/仓储/领用"), ("出货", "发运/出货")):
            mine = of(stage)
            if not mine:
                continue
            hit = next((row for row in mine if any(word in self.row_text(row) for word in _FIFO)), None)
            if hit is not None:
                fifo_where.append(f"{label}环节{self.at(hit, 'CP规格公差')}")
                continue
            limit = next((row for row in mine if any(word in self.row_text(row) for word in _NOT_FIFO)), None)
            if limit is not None:
                problems.append(f"{label}环节只写了库龄/库存天数等时限要求（{self.at(limit, 'CP规格公差')}），不算先进先出")
            else:
                problems.append(f"{label}环节（{self.at(mine[0], '工序名称')}起）的行没有写明先进先出或批次先后取用要求")
        if not of("出入库") and not of("出货"):
            problems.append("控制计划没有出入库、仓储、领用或发运环节的行写明先进先出")
        if problems:
            if drawn := self.graphic_in_blank(unread, "CP规格公差"):
                raise m.unable(f"控制计划{drawn}规格/公差格内有图形或无法对应原页位置，检查程序未能读取，需人工复核。")
            return "NG", "；".join(problems) + "。"
        return "PASS", f"控制计划进料、出入库、出货环节的“产品/过程规格/公差”列均写明追溯标识载体；{'、'.join(fifo_where)}写明先进先出。"

    # CP:5 -------------------------------------------------------------------------------
    @rule("跑道尺寸过程能力控制")
    def rule_5(self, ctx):
        status, reason = super().rule_5(ctx)
        if status == "UNABLE" and reason.startswith("未发现跑道尺寸"):
            detail = reason.split("：", 1)[1].removesuffix("需人工确认该零件的跑道尺寸。").rstrip("，,")
            return (
                "NA",
                f"不适用：逐行检索后未发现跑道尺寸行（{detail}），依据是该零件控制计划中没有定量尺寸类的跑道尺寸特性。提示：请核实，并由供应商在控制计划或提交说明中书面确认该零件确无跑道尺寸。",
            )
        return status, reason

    # CP:6 -------------------------------------------------------------------------------
    @rule("CP身份字段图纸身份及最后更改日期")
    def rule_6(self, ctx):
        m = self._m
        res = m.resources
        self.plan_sheets(ctx)
        pdf = str(self.audit_file.file_url).lower().endswith(".pdf")
        failures, unknown, absent = [], [], []
        part_values = [value for value, _place in self.plan_fields(ctx, res.PART_NUMBER)]
        for label, aliases in (
            ("控制计划编号", _PLAN_NUMBER),
            ("供应商名称", res.SUPPLIER_NAME),
            ("供应商代码", res.SUPPLIER_CODE),
            ("编制人", res.PREPARER),
            ("版本号", res.VERSION),
            ("受控日期/创建日期/修订日期", res.DATES),
            ("零件名称", res.PART_NAME),
            ("零件号", res.PART_NUMBER),
        ):
            found = self.plan_fields(ctx, aliases)
            if not found:
                (unknown if pdf else absent).append(label)
                continue
            if "日期" in label:
                continue
            written = [(value, place) for value, place in found if m.compact(value) not in m._PLACEHOLDERS]
            if label == "控制计划编号":
                # The plan number is its own key: a placeholder or the part number itself is no plan number.
                parts = {m.compact(value) for value in part_values if m.compact(value) not in m._PLACEHOLDERS}
                written = [(value, place) for value, place in written if m.compact(value) not in parts]
                if not written:
                    absent.append(f"控制计划编号（{self.named(found[0][1])}为“{found[0][0] or '空'}”，视为未填）")
                continue
            if not written:
                failures.append(f"{label}（{self.named(found[0][1])}）为“{found[0][0] or '空'}”")
        if absent:
            grade = "致命" if len(absent) >= 3 else "重要"
            failures.insert(0, f"表头缺少{'、'.join(absent)}字段（缺失{len(absent)}项，按条文为{grade}）")
        if unknown:
            unknown = [f"表头未能定位{'、'.join(unknown)}字段"]
        try:
            _drawing, missing = self.drawing(ctx)
            failures.append(missing)
        except m.GuardStop as stop:
            if stop.decision != m.Decision.UNABLE:
                raise
            unknown.append(stop.message.removesuffix("，需人工复核。").rstrip("。"))
        if failures:
            if len(failures) > 1:
                self.__dict__.pop("_need", None)
            note = f"；另{'；'.join(unknown)}，需人工复核" if unknown else ""
            return "NG", "控制计划" + "；".join(failures) + note + "。"
        # The last change date is compared with the drawing only once a same-part drawing is read.
        raise m.unable("；".join(unknown) + "，需人工复核。")

    # CP:7 / CP:8 counterparts ------------------------------------------------------------
    def sources_r3(self, ctx, *, plan_required):
        """(found, missing, open): same-part flow chart and PFMEA (required), and the purchased goods plan.

        `plan_required`: CP:7 requires the purchased goods quality plan file; CP:8 counts its absence at package level only.
        A PGQP1 table is read only when the plan has one; its absence is not a missing file.
        """
        m = self._m
        wanted = [(m._PFD, "过程流程图", m.flowchart_counterpart), (m._PFMEA, "PFMEA", m.pfmea_counterpart), (m._PGQP1, "采购件质量计划", m.pgqp1)]
        absent = [label for kind, label, _document in wanted if not self.batch_files(kind) and (plan_required or kind != m._PGQP1)]
        if absent:
            self.need("》《".join(absent))
        mine = self.part_number(ctx)
        found, missing, unknown, notes = {}, [f"同批次没有{'、'.join(absent)}独立文件"] if absent else [], [], []
        # The counterparts that are present are still compared, so a missing one does not hide the gaps of the others.
        for kind, label, document in wanted:
            if not self.batch_files(kind):
                if kind == m._PGQP1 and not plan_required:
                    notes.append("同批次没有采购件质量计划，PGQP1缺件只在包级计一次，本条不重复计")
                continue
            try:
                keep = self.__dict__.get("_need")
                item, why = self.counterpart(ctx, kind, label, document, mine)
                if keep is not None:
                    self.__dict__["_need"] = keep
            except m.GuardStop as stop:
                if stop.decision != m.Decision.UNABLE:
                    raise
                unknown.append(stop)
                continue
            if item is None:
                if kind == m._PGQP1 and not plan_required:
                    notes.append(why + "，PGQP1缺件只在包级计一次，本条不重复计")
                    self.__dict__.pop("_need", None)
                else:
                    missing.append(why)
            else:
                found[kind] = item
        if m._PGQP1 in found:
            with self.reading(ctx, found[m._PGQP1], m.pgqp1):
                if not m.Table(ctx).find_sheets("PGQP1", on_missing=m.Decision.IGNORE):
                    notes.append("同批次采购件质量计划中没有PGQP1表，PGQP1部分不在本条比对范围")
                    del found[m._PGQP1]
        if unknown and not missing:
            raise unknown[0]
        return found, missing, notes

    @rule("PFD PFMEA PGQP1全部工序覆盖")
    def rule_7(self, ctx):
        m = self._m
        found, missing, notes = self.sources_r3(ctx, plan_required=True)
        if missing and not found:
            return "NG", "；".join(missing) + "，请补充缺项文件后复审。"
        operations = self.operations(ctx)
        gaps = []

        def check(label, step):
            # A short name of the same operation matches either way ("高压清洗" / "清洗"); two different words do not.
            key = m._key(step)
            if step and not any(key in name or (len(name) >= 2 and name in key) for name in operations):
                gaps.append(f"{label}“{' '.join(step.split())}”")

        def steps(sheet, section, column):
            """Rows of `column` that are operations: those numbered left of it, when the original numbers its operations.

            A PFMEA lists functions and characteristics in the same column under each numbered operation; signature rows
            below a flow chart are no operation either.
            """
            rows = list(section.rows(column))
            name_col = section.column(column).header_col

            def numbered(row):
                return any(re.fullmatch(r"\d{1,4}(?:[-.]\d{1,3})?", m.compact(sheet.cell(row.row_index, col).value())) for col in range(1, name_col))

            if any(numbered(row) for row in rows):
                rows = [row for row in rows if numbered(row)]
            return [str(row.value(column) or "").strip() for row in rows]

        opened = []
        try:
            if m._PFD in found:
                with self.reading(ctx, found[m._PFD], m.flowchart_counterpart):
                    for sheet in self.counterpart_sheets(ctx, "U08流程图", "过程流程图"):
                        for step in steps(sheet, self.section(sheet, "U08流程区"), "U08过程描述"):
                            check("过程流程图", step)
            if m._PFMEA in found:
                with self.reading(ctx, found[m._PFMEA], m.pfmea_counterpart):
                    for sheet in self.counterpart_sheets(ctx, "U08PFMEA", "PFMEA"):
                        for step in steps(sheet, self.section(sheet, "U08PFMEA区"), "U08PFMEA工序"):
                            check("PFMEA", step)
        except m.GuardStop as stop:
            # An unreadable counterpart leaves the clause open unless a missing file already decides it.
            if stop.decision != m.Decision.UNABLE or not missing:
                raise
            opened.append(stop.message.removesuffix("，需人工复核。").rstrip("。"))
        if m._PGQP1 in found:
            with self.reading(ctx, found[m._PGQP1], m.pgqp1):
                for sheet in m.Table(ctx).find_sheets("PGQP1", on_missing=m.Decision.IGNORE):
                    for row in self.section(sheet, "U08关键工序区").rows("U08PGQP1工艺"):
                        check("PGQP1工艺", str(row.value("U08PGQP1工艺") or "").strip())
        note = f"（{'；'.join(notes)}）" if notes else ""
        if missing:
            listed = list(dict.fromkeys(gaps))
            extra = f"；另以下工序未在控制计划工序名称/操作描述列出现：{'、'.join(listed[:20])}{'等' if len(listed) > 20 else ''}" if listed else ""
            extra += f"；另{'；'.join(opened)}，需人工复核" if opened else ""
            return "NG", "；".join(missing) + "，请补充缺项文件后复审" + extra + note + "。"
        if gaps:
            listed = list(dict.fromkeys(gaps))
            return "NG", f"以下工序未在控制计划工序名称/操作描述列出现：{'、'.join(listed[:20])}{'等' if len(listed) > 20 else ''}{note}。"
        sources = "过程流程图、PFMEA" + ("、PGQP1" if m._PGQP1 in found else "")
        return "PASS", f"{sources}的全部工序都在控制计划工序名称/操作描述列出现{note}。"

    # pairing by name and tolerance (CP:8, CP:13) -------------------------------------------
    def pairs(self, rows, name, spec):
        """Control plan rows that carry the characteristic `name` with the tolerance `spec` in one row."""
        key, wanted = _name_key(name), _fold(spec)
        found = []
        for row in rows:
            names = [_name_key(row.cells.get(column, "")) for column in ("CP产品特性", "CP过程特性")]
            if not key or not any(item and (key in item or item in key) for item in names):
                continue
            cells = [_fold(row.cells.get(column, "")) for column in self._m._CHARACTERISTIC]
            if wanted and any(wanted in cell for cell in cells):
                found.append(row)
        return found

    def qualitative_pairs(self, rows, name, requirement):
        """Rows pairing a characteristic without a numeric tolerance: same name, no numeric tolerance, same written requirement."""
        key = _name_key(name)
        found = []
        for row in rows:
            names = [_name_key(row.cells.get(column, "")) for column in ("CP产品特性", "CP过程特性")]
            if not key or not any(item and (key in item or item in key) for item in names):
                continue
            if _spec_like(row.cells.get("CP规格公差", "")):
                continue
            if requirement and _fold(requirement) not in _fold(self.spanned(row, "CP规格公差")):
                continue
            found.append(row)
        return found

    @rule("PFD PFMEA PGQP1全部特殊特性覆盖")
    def rule_8(self, ctx):
        m = self._m
        found, missing, notes = self.sources_r3(ctx, plan_required=False)
        if missing:
            return "NG", "；".join(missing) + "，请补充缺项文件后复审。"
        rows = self.plan(ctx)
        gaps, differ, opened = [], [], []

        def pair(source, where, name, spec, mark):
            # A repeated page heading or a footer (编号 No., a date) read as a table row names no characteristic.
            folded = _fold(name)
            if not folded or not re.search(r"[a-z一-鿿]", folded) or folded.rstrip(".") in {"编号no", "no", "序号"}:
                return
            if _spec_like(spec):
                matched = self.pairs(rows, name, spec)
            else:
                matched = self.qualitative_pairs(rows, name, spec)
            if not matched:
                gaps.append(f"{source}{where}“{' '.join((name + ' ' + spec).split())}”")
                return
            if mark is None:
                return
            # A pair whose marks differ is not consistent: the control plan must mark it as the source does.
            if not any(_marks(row.cells.get("CP特殊特性")) == _marks(mark) for row in matched):
                differ.append(f"{source}{where}“{' '.join(name.split())}”标识“{mark}”，控制计划{self.at(matched[0], 'CP特殊特性')}标识“{matched[0].cells.get('CP特殊特性') or '空'}”")

        with self.reading(ctx, found[m._PFD], m.flowchart_counterpart):
            for sheet in self.counterpart_sheets(ctx, "U08流程图", "过程流程图"):
                section = self.section(sheet, "U08流程区")
                if ctx.optional_column(section.resolved, "U08流程特殊特性") is None:
                    opened.append(f"过程流程图“{sheet.title}”没有可读的特性符号列（特性标识可能是图形），检查程序未能确认其中被标识的特性")
                    continue
                for row in section.rows("U08过程描述", "U08流程特殊特性", optional=("U08流程产品特性", "U08流程过程特性")):
                    mark = str(row.value("U08流程特殊特性") or "").strip()
                    if m.compact(mark) in m._PLACEHOLDERS:
                        continue
                    for name in ("U08流程产品特性", "U08流程过程特性"):
                        value = str(row.value(name) or "").strip() if name in row.columns else ""
                        if value:
                            self.cite(sheet, row.row_index, row.columns[name].header_col)
                            pair("过程流程图", f"第{row.row_index}行", value, _tolerance(value), mark)
        with self.reading(ctx, found[m._PFMEA], m.pfmea_counterpart):
            for sheet in self.counterpart_sheets(ctx, "U08PFMEA", "PFMEA"):
                section = self.section(sheet, "U08PFMEA区")
                column = section.column("U08PFMEA工序")
                _height, width = self.extent(sheet)
                for row in section.rows("U08PFMEA分类"):
                    mark = str(row.value("U08PFMEA分类") or "").strip()
                    if m.compact(mark) in m._PLACEHOLDERS:
                        continue
                    name = str(sheet.cell(row.row_index, column.header_col).value() or "").strip()
                    # The tolerance is written next to the characteristic (776/A40: the cell right of it), else inside it.
                    right = str(sheet.cell(row.row_index, column.header_col + 1).value() or "").strip() if column.header_col + 1 <= width else ""
                    spec = right if _spec_like(right) else _tolerance(name)
                    self.cite(sheet, row.row_index, column.header_col)
                    pair("PFMEA", f"第{row.row_index}行", name, spec, mark)
        if m._PGQP1 in found:
            for name, spec, where in self.important(ctx, found[m._PGQP1]):
                pair("PGQP1", self.named(where), name, spec, None)
        note = f"（{'；'.join(notes)}）" if notes else ""
        if gaps or differ:
            parts = []
            if gaps:
                parts.append(f"以下特性按“特性名＋公差值”在控制计划产品、过程、规格/公差列中配对不上：{'、'.join(gaps[:15])}{'等' if len(gaps) > 15 else ''}")
            if differ:
                parts.append(f"以下特性配对成功但特殊特性标识不一致：{'、'.join(differ[:10])}{'等' if len(differ) > 10 else ''}")
            if opened:
                parts.append("另" + "；".join(opened) + "，需人工复核")
            return "NG", "；".join(parts) + note + "。"
        if opened:
            raise m.unable("；".join(opened) + f"{note}，需人工复核。")
        return "PASS", f"过程流程图、PFMEA{'、PGQP1' if m._PGQP1 in found else ''}的被标识特性都按“特性名＋公差值”在控制计划中配对成功且标识一致{note}。"

    # CP:9 -------------------------------------------------------------------------------
    @rule("PGQP1尺寸类特性出货检验覆盖")
    def rule_9(self, ctx):
        m = self._m
        plan, why = self.plan_only(ctx)
        if plan is None:
            return "NG", why + "，请补充后复审。"
        dimensions = [(name, spec, where) for name, spec, where in self.important(ctx, plan) if m._dimension(f"{name} {spec}")]
        if not dimensions:
            raise m.unable("PGQP1重要特性清单中没有读出尺寸类特性，不能按空范围通过，需人工复核。")
        rows = self.plan(ctx)

        def outgoing_(name):
            return any(word in m.compact(name) for word in ("出货检验", "outgoinginspection"))

        outgoing = [row for row in rows if outgoing_(row.operation)]
        if not outgoing:
            raise m.unable("控制计划中未找到出货检验工序，尚不能核对PGQP1尺寸是否在出货检验中，需人工复核。")
        between = [row for row in rows if any(outgoing_(name) for name in row.candidates)]
        absent, differ, open_ = [], [], []
        for name, spec, where in dimensions:
            # One row carries the item: its name and its specification are not gathered from different rows.
            if self.pairs(outgoing, name, spec):
                continue
            if self.pairs(between, name, spec):
                open_.append(f"“{name} {spec}”")
                continue
            named = [row for row in outgoing if any(_name_key(name) and _name_key(name) == _name_key(row.cells.get(column, "")) for column in ("CP产品特性", "CP过程特性"))]
            (differ if named else absent).append(f"{self.named(where)}“{name} {spec}”")
        if absent:
            extra = f"；另{len(differ)}项名称出现但同一行的规格写法与PGQP1不同：{'、'.join(differ[:5])}" if differ else ""
            listed = "、".join(absent[:15]) + ("等" if len(absent) > 15 else "")
            return "NG", f"PGQP1共{len(dimensions)}项尺寸类特性，其中{len(absent)}项未在控制计划出货检验工序（{self.at(outgoing[0], '工序名称')}起）的同一行出现：{listed}{extra}。"
        if differ or open_:
            text = []
            if differ:
                text.append(f"{len(differ)}项名称在出货检验工序出现但同一行规格写法与PGQP1不同：{'、'.join(differ[:8])}")
            if open_:
                text.append(f"{'、'.join(open_[:8])}只出现在出货检验与相邻工序之间、原件没有分隔线的行")
            raise m.unable("；".join(text) + "，需人工复核。")
        return "PASS", f"PGQP1全部{len(dimensions)}项尺寸类特性都在控制计划出货检验工序的同一行出现。"

    # CP:10 ------------------------------------------------------------------------------
    @rule("PGQP1产品审核型式试验覆盖")
    def rule_10(self, ctx):
        m = self._m
        plan, why = self.plan_only(ctx)
        if plan is None:
            return "NG", why + "，请补充后复审。"
        audit = self.product_audit(ctx, plan)
        if not audit:
            raise m.unable("PGQP1中未能定位产品审核要求（Product Audit Requirements）表，检查程序未能读取，需人工复核。")
        tests, excluded = [], []
        for item, content, where in audit:
            text = _fold(item + content)
            annual = any(word in text for word in _ANNUAL)
            if any(word in text for word in _NOT_TYPE_TEST):
                excluded.append(item or content)
            elif any(word in text for word in _TYPE_TEST) or annual:
                # A type test, the annual material test, or another annual confirmation not stated to be no type test.
                tests.append((item, content, where))
        if not tests:
            listed = "、".join(item or content for item, content, _where in audit)
            extra = f"；{'、'.join(excluded)}明确不是型式试验，不在本条范围" if excluded else ""
            return "NA", f"不适用：PGQP1产品审核要求（{listed}）中没有定义型式试验或年度确认要求{extra}。"
        text = self.characteristics(ctx)
        absent = []
        for item, content, where in tests:
            # The item is named in English and Chinese ("Full dimension 全尺寸"): either name in one cell is enough.
            names = [part for part in re.split(r"\s+", str(item).strip()) if _cjk(part)] or [item or content]
            if not any(m._written(name, text) for name in names) and not m._written(item or content, text):
                absent.append(f"{self.named(where)}“{' '.join((item or content).split())}”（{' '.join(content.split())}）")
        extra = f"；{'、'.join(excluded)}明确不是型式试验，不在本条范围" if excluded else ""
        if absent:
            return "NG", f"PGQP1产品审核要求中的型式试验/年度确认要求未在控制计划产品、过程、规格/公差列出现：{'、'.join(absent)}{extra}。"
        listed = "、".join(" ".join((item or content).split()) for item, content, _where in tests)
        return "PASS", f"PGQP1产品审核要求中的{len(tests)}项型式试验/年度确认要求（{listed}）都在控制计划中出现{extra}。"

    # CP:13 ------------------------------------------------------------------------------
    def sample_column(self, sheet):
        """The column of the control plan's sample size (SAMPLE SIZE / 样本容量 / 数量), or None."""
        m = self._m
        section = self.section(sheet, "控制计划区")
        header = section.resolved.header_row
        _rows, width = self.extent(sheet)
        heads = {}
        for cell in sheet.scan_non_empty(row_min=max(1, header - 3), row_max=header + 1, col_min=1, col_max=width):
            heads.setdefault(cell.col_index, []).append(m.compact(cell.value()))
        for column, texts in sorted(heads.items()):
            joined = "".join(texts)
            if any(word in joined for word in ("样本容量", "samplesize", "样本数", "抽样数量", "容量")) or ("sample" in joined and "size" in joined) or "数量" in texts:
                return column
        return None

    def pgqp1_sampling(self, ctx, plan):
        """(name, spec, place, sampling text) of each PGQP1 important characteristic: its process control cells."""
        m = self._m
        items = []
        with self.reading(ctx, plan, m.pgqp1):
            for sheet in m.Table(ctx).find_sheets("PGQP1", on_missing=m.Decision.IGNORE):
                section = self.section(sheet, "重要特性区")
                header = section.resolved.header_row
                _height, width = self.extent(sheet)
                every_col = other_col = None
                for cell in sheet.scan_non_empty(row_min=header, row_max=header + 2, col_min=1, col_max=width):
                    text = m.compact(cell.value())
                    if "100%" in text and every_col is None:
                        every_col = cell.col_index
                    elif text in {"其他", "other", "others", "其它"} and other_col is None:
                        other_col = cell.col_index
                if every_col is None and other_col is None:
                    raise m.unable(f"PGQP1“{sheet.title}”重要特性清单中未能定位过程控制（100%检验／其他）列，检查程序未能读取样本容量与频率，需人工复核。")
                for row in section.rows("特性项目", "特殊特性"):
                    name, spec = str(row.value("特性项目") or "").strip(), str(row.value("特殊特性") or "").strip()
                    if not (name and spec and any(char.isdigit() for char in spec)):
                        continue
                    every = every_col is not None and m.compact(sheet.cell(row.row_index, every_col).value()) not in m._PLACEHOLDERS
                    other = str(sheet.cell(row.row_index, other_col).value() or "").strip() if other_col is not None else ""
                    place = m.Place.of(f"第{row.row_index}行", sheet, row.row_index, row.columns["特性项目"].header_col)
                    items.append((name, spec, place, ("100%检验 " if every else "") + other))
        return items

    @rule("PGQP1特性样本容量与抽样频率")
    def rule_13(self, ctx):
        m = self._m
        if not self.batch_files(m._PGQP1):
            return "NA", "不适用：同批次未提交采购件质量计划（PGQP1），本条无比对对象。"
        item, why = self.counterpart(ctx, m._PGQP1, "采购件质量计划", m.pgqp1, self.part_number(ctx))
        self.__dict__.pop("_need", None)
        if item is None:
            return "NA", f"不适用：{why}，同批次没有本零件的PGQP1，本条无比对对象。"
        with self.reading(ctx, item, m.pgqp1):
            if not m.Table(ctx).find_sheets("PGQP1", on_missing=m.Decision.IGNORE):
                return "NA", "不适用：同批次采购件质量计划中没有PGQP1表，本条无比对对象。"
        specified = [(name, spec, where, text) for name, spec, where, text in self.pgqp1_sampling(ctx, item) if any(part is not None and part is not False for part in _sampling(text))]
        if not specified:
            return "NA", "不适用：PGQP1重要特性清单没有对任何特性规定样本容量或频率，本条无判定对象。"
        rows = self.plan(ctx)
        columns = {id(row.sheet): self.sample_column(row.sheet) for row in rows if row.sheet is not None}
        unpaired, failed, open_ = [], [], []
        for name, spec, where, text in specified:
            label = f"{self.named(where)}“{' '.join((name + ' ' + spec).split())}”（PGQP1：{' '.join(text.split())}）"
            matched = self.pairs(rows, name, spec)
            if not matched:
                unpaired.append(label)
                continue
            verdicts = []
            for row in matched:
                column = columns.get(id(row.sheet))
                size_text = str(row.sheet.cell(row.row, column).value() or "").strip() if column is not None else ""
                freq_text = self.spanned(row, "CP频率")
                verdicts.append((*_compare(text, size_text, freq_text, column is not None, m), self.at(row, "CP频率")))
            if any(state is True for state, _why, _where in verdicts):
                continue
            if all(state is False for state, _why, _where in verdicts):
                failed.append(label + "：" + "；".join(f"{where_row}{'、'.join(why)}" for _state, why, where_row in verdicts[:3]))
            else:
                open_.append(label + "：" + "；".join(f"{where_row}{'、'.join(why)}" for state, why, where_row in verdicts if state is None))
        if unpaired or failed:
            parts = []
            if unpaired:
                parts.append(f"{len(unpaired)}项按“特性名＋公差值”在控制计划中配对不上（按缺项判）：{'、'.join(unpaired[:10])}{'等' if len(unpaired) > 10 else ''}")
            if failed:
                parts.append(f"{len(failed)}项控制计划的样本容量或频率不及PGQP1：{'、'.join(failed[:8])}{'等' if len(failed) > 8 else ''}")
            if open_:
                parts.append(f"另{len(open_)}项检查程序不能比较，需人工复核：{'、'.join(open_[:3])}")
            return "NG", f"PGQP1规定样本容量或频率的特性共{len(specified)}项，" + "；".join(parts) + "。"
        if open_:
            raise m.unable(f"PGQP1规定样本容量或频率的特性共{len(specified)}项，其中{len(open_)}项检查程序不能比较：{'、'.join(open_[:5])}，需人工复核。")
        return "PASS", f"PGQP1规定样本容量或频率的{len(specified)}项特性，控制计划对应行的样本容量与频率都等于或严于PGQP1。"


# ---- the reader with the revision 3 methods ------------------------------------------------

_BUILT = {}


def _extend(base):
    """The UAES control plan reader `base` with the revision 3 clause methods and the 特性类型 column name."""
    if base not in _BUILT:
        module = sys.modules[base.__module__]
        document = module.resources.DOCUMENT
        columns = tuple(column.model_copy(update={"aliases": (*column.aliases, "特性类型")}) if column.canonical_name == "CP特殊特性" else column for column in document[2])
        resource = module.combine(
            (document[0], document[1], columns), module.pgqp1.DOCUMENT, module.pgqp1_counterpart.DOCUMENT, module.flowchart_counterpart.DOCUMENT, module.pfmea_counterpart.DOCUMENT
        )
        _BUILT[base] = type("UaesControlPlanRevision3", (_Revision3, base), {"_m": module, "EXTRA_RESOURCE": resource, "__module__": __name__})
    return _BUILT[base]


class Revision3Audit(AgentAudit):
    def _implementation(self):
        if self._failure is not None:
            raise self._failure
        agent = super()._implementation()
        if agent is None or isinstance(agent, _Revision3):
            return agent
        try:
            self._agent = _extend(type(agent))(self.audit_file, self._implementation_rule(), group_context=self.group_context)
        except BaseException as exc:
            self._failure = exc
            raise
        return self._agent


def create_audit(audit_file, rule, group_context=None):
    return Revision3Audit(audit_file, rule, group_context, resource=RESOURCE)
