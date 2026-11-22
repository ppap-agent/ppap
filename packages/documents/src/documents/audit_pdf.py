"""Render an already accepted, frozen per-file machine result as a PDF: a cover and ten chapters."""

import re
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from documents.location import location_label
from documents.report_layout import Chapter, Table, chapter_title, dash, render_report, text

# PRD §2.11 八: PASS / NG are written as they are; a clause without a verdict is 待审核 (never PASS).
STATUS = {"PASS": "PASS", "NG": "NG", "UNABLE": "待审核", "uncovered": "待审核", "ERR": "技术错误", "NA": "不适用"}
PENDING = frozenset({"UNABLE", "uncovered"})
CONCLUSION = {"PASS": "PASS", "NG": "NG", "UNKNOWN": "待审核"}
KIND = "PPAP 审核报告"
# Cover: recognized fields first (frozen recognition of the accepted task), the check date, then fields the reader completes.
COVER_FIELDS = (("customer", "客户"), ("project", "项目"), ("model", "车型"), ("part_number", "零件号"), ("part", "零件名称"))
READER_FIELDS = ("供应商名称", "PPAP 版本", "SQE 工程师")
LEVELS = ("A", "B", "C")
# PRD §2.11 五: level -> remediation priority, suggested deadline and what fixing it protects.
PRIORITY = {"A": "P1", "B": "P2", "C": "P3", None: "未定级"}
PRIORITY_TITLE = {"A": "P1 立即整改（A 类）", "B": "P2 限期整改（B 类）", "C": "P3 建议整改（C 类）", None: "未定级的不符合项"}
DEADLINE = {"A": "3 个工作日", "B": "7 个工作日", "C": "15 个工作日", None: "—"}
BENEFIT = {
    "A": "消除影响产品安全、法规符合性或客户特殊要求的缺陷，避免 PPAP 被拒收和量产后的安全与召回风险。",
    "B": "消除影响产品质量与过程稳定性的缺陷，降低量产中的不良与过程波动风险。",
    "C": "使文件格式与标识符合客户要求，避免提交被退回和重复审核。",
    None: "消除本审核项的不符合，使本文件该项满足客户要求。",
}
# The four result values of chapter one, never a risk grade.
STAT_COLORS = (("PASS", "#2ea121"), ("NG", "#d83931"), ("待审核", "#de7802"), ("不适用", "#8f959e"))
DETAIL_HEAD = ("序号", "审核项", "结果", "严重程度", "问题性质", "判定依据", "说明", "位置")
DETAIL_NOWRAP = (0, 2, 3, 4)
# PRD §2.11 六（4）: empty phrases and references never reach a remediation point.
EMPTY_PHRASES = re.compile(r"[，,；;]?\s*(?:请核实|请确认|建议完善|需补充相关内容|同上|见前文)[^。；;]*[。；;]?")
# Precheck warning codes -> the remediation action they call for (改值 / 增补 / 删除 / 重做映射).
WARNING_ACTION = {"W01": "增补", "W02": "增补", "W03": "改值", "W04": "改值", "W05": "增补", "W06": "改值"}


def _text(value) -> str:
    return escape(str(value if value is not None else ""))


def _first_location(source: dict, check: dict) -> str:
    for evidence in check["evidence"]:
        if evidence.get("locator"):
            return f"{source['originals'][evidence['file_version_id']]} · {location_label(evidence['locator'])}"
    return source["filename"]


def _locations(source: dict, check: dict) -> str:
    places = []
    for evidence in check["evidence"]:
        locator = evidence.get("locator")
        place = location_label(locator) if locator else evidence.get("location_gap") or "未提供具体位置。"
        places.append(f"{source['originals'][evidence['file_version_id']]} · {place}")
    return "；".join(places) or "未提供具体位置"


def _evidence(source: dict, check: dict) -> str:
    if not check["evidence"]:
        return "未提供原件定位与摘录。"
    parts = []
    for evidence in check["evidence"]:
        locator = evidence.get("locator")
        place = location_label(locator) if locator else evidence.get("location_gap") or "未提供具体位置。"
        excerpt = evidence.get("excerpt") or ("该位置内容为空。" if locator else "未提供摘录。")
        parts.append(f"{text(source['originals'][evidence['file_version_id']])} · {text(place)}<br/>{text(excerpt)}")
    return "<br/>".join(parts)


def _code(check: dict) -> str:
    """The clause's result: the frozen machine status, or 未覆盖 when the checker gave none."""
    return check["finding"]["status"] if check.get("finding") else "uncovered"


def _semantic(check: dict) -> dict:
    semantic = ((check.get("finding") or {}).get("locator") or {}).get("semantic")
    return semantic if isinstance(semantic, dict) else {}


def _supply(check: dict) -> dict | None:
    supply = _semantic(check).get("supply")
    return supply if isinstance(supply, dict) and supply.get("kind") in {"missing", "source_mismatch"} else None


def _level(check: dict) -> str | None:
    """Final level of an NG; None when neither the rule nor the checker gave one."""
    level = _semantic(check).get("severity")
    return level if _code(check) == "NG" and level in LEVELS else None


def _failed(audit: dict) -> list[tuple[int, dict]]:
    return [(number, check) for number, check in enumerate(audit["checks"], 1) if _code(check) == "NG"]


def _pending(audit: dict) -> int:
    return sum(_code(check) in PENDING for check in audit["checks"])


def _warnings(source: dict) -> list[dict]:
    """The audited file's own precheck warnings; in the audit report they are C-class problems (PRD §2.11 四)."""
    return list(source.get("precheck_warnings") or [])


def _results(audit: dict) -> dict[str, int]:
    codes = [_code(check) for check in audit["checks"]]
    return {"PASS": codes.count("PASS"), "NG": codes.count("NG"), "待审核": sum(code in PENDING for code in codes), "不适用": codes.count("NA")}


def _rating(source: dict) -> str:
    """PRD §2.11 四 with the owner's ruling: an A / B or an unleveled NG fails; otherwise any C (warnings included)
    passes with conditions; no problem passes. 待审核 clauses are named beside it, never counted as PASS."""
    audit = source["audit"]
    failed = [check for _number, check in _failed(audit)]
    if not failed and audit["conclusion"] not in {"PASS", "NG"}:
        return CONCLUSION.get(audit["conclusion"], audit["conclusion"])
    if any(_level(check) != "C" for check in failed):
        return "不通过"
    return "有条件通过" if failed or _warnings(source) else "通过"


def _pending_note(source: dict) -> str:
    count = _pending(source["audit"])
    return f"另 {count} 项待审核" if count and source["audit"]["conclusion"] in {"PASS", "NG"} else ""


def _grading_note(check: dict) -> str | None:
    level = _level(check)
    if level is None:
        return None
    override = _semantic(check).get("severity_override")
    if isinstance(override, dict) and override.get("level") == level:
        prefix = f"规则默认 {override['default']} → " if override.get("default") else ""
        return f"{prefix}判定 {level} ｜ 理由：{override.get('reason')}"
    return f"严重程度 {level}（规则默认）"


def _status(check: dict) -> str:
    code = _code(check)
    label = text(STATUS.get(code, code))
    return f"<span class='strong'>{label}</span>" if code != "PASS" else label


def _item(check: dict) -> str:
    rule = check["rule_item"]
    return f"{text(rule['id'])} · {text(rule['category'])}"


def _explanation(check: dict) -> str:
    finding = check.get("finding") or {}
    lines = [finding.get("suggestions") or finding.get("classification")]
    if not finding and not check.get("scope_notice"):
        lines.append("本条要求没有取得自动审核结果，待人工审核。")
    lines.append(_grading_note(check))
    if scope := check.get("scope"):
        lines.append(f"检查范围：已查 {scope['checked']}/{scope['total']}，未知 {scope['unknown']}。")
    lines.append(check.get("scope_notice"))
    return "<br/>".join(text(line) for line in lines if line) or "—"


def _detail_row(source: dict, number: int, check: dict) -> tuple[str, ...]:
    # 问题性质 has no frozen value yet; it is shown empty rather than guessed.
    return (str(number), _item(check), _status(check), dash(_level(check)), "—", text(check["rule_item"]["rule_to_check"]), _explanation(check), text(_locations(source, check)))


def _warning_row(number: str, warning: dict) -> tuple[str, ...]:
    code, title = warning["code"], warning["title"]
    explanation = f"{text(warning['problem'])}<br/>证据：{text(warning.get('evidence') or '(空)')}"
    return (str(number), text(f"预检 {code} · {title}"), f"<span class='strong'>{text(code)}</span>", "C", "—", text(f"预检 {code} {title}"), explanation, text(warning["location"]))


def _cover(source: dict, checked_on: str) -> str:
    # The frozen recognition as the file wrote it; its customer is the file's, never the chosen rule set.
    recognition = source.get("recognition") or {}
    rows = [(label, dash(recognition.get(key))) for key, label in COVER_FIELDS]
    rows += [("检查日期", text(checked_on))]
    rows += [(label, "") for label in READER_FIELDS]
    table = "".join(f"<tr><th>{escape(label)}</th><td>{value}</td></tr>" for label, value in rows)
    return "".join(
        [
            "<h1>PPAP 审核报告</h1>",
            f"<p class='kind'>{text(source['filename'])}</p>",
            f"<table>{table}</table>",
            "<p class='meta'>供应商名称、PPAP 版本与 SQE 工程师请下载后自行补充。</p>",
        ]
    )


def _problem_levels(source: dict) -> list[str | None]:
    """The level of every problem: each NG clause, then each precheck warning (C)."""
    return [_level(check) for _number, check in _failed(source["audit"])] + ["C"] * len(_warnings(source))


def _summary(source: dict) -> list:
    audit = source["audit"]
    results = _results(audit)
    levels = _problem_levels(source)
    note = _pending_note(source)
    rating = _rating(source)
    conclusion = CONCLUSION.get(audit["conclusion"], audit["conclusion"])
    warnings = len(_warnings(source))
    unleveled = levels.count(None)
    stats = "　".join(f"{level} 类 {levels.count(level)} 项" for level in LEVELS) + (f"　未定级 {unleveled} 项" if unleveled else "")
    if not levels:
        judgement = "本文件未发现问题项" + (f"；另 {results['待审核']} 项待审核，见第六章。" if note else "。")
    else:
        judgement = f"本文件发现 {len(levels)} 个问题，总评「{rating}」" + (f"；另 {results['待审核']} 项待审核，见第六章。" if note else "。")
    blocks = [
        f"<p>总评：<b>{_text(rating)}</b>{'　' + _text(note) if note else ''}　文件结论：<b>{_text(conclusion)}</b></p>"
        f"<p>问题统计：{stats}；问题总数 {len(levels)} 项" + (f"（C 类含预检警告 {warnings} 项）" if warnings else "") + "。</p>"
        f"<p>结果计数（共 {len(audit['checks'])} 项审核项）：" + "，".join(f"{label} {results[label]} 项" for label, _color in STAT_COLORS) + "。</p>",
        "<table><tr>" + "".join(f"<td style='text-align:center'>{label}<br/><b style='color:{color}; font-size:16pt'>{results[label]}</b></td>" for label, color in STAT_COLORS) + "</tr></table>",
        f"<p>{_text(judgement)}</p>",
        "<p class='meta'>「待审核」项不计入 A / B / C 与问题总数，也不计为 PASS。</p>",
    ]
    scopes = [check["scope"] for check in audit["checks"] if check.get("scope")]
    if scopes:
        blocks.append(f"<p>检查范围：已查 {sum(scope['checked'] for scope in scopes)}/{sum(scope['total'] for scope in scopes)}，未知 {sum(scope['unknown'] for scope in scopes)}。</p>")
    if any(check.get("finding") and not check.get("evidence_verified") for check in audit["checks"]):
        blocks.append("<p class='meta'>部分判定的证据未经验证，请核对原件中的证据和检查范围。</p>")
    return blocks


def _scope(source: dict) -> list:
    audit = source["audit"]
    files = [(text(name), "本报告被审文件" if version == audit["file_version_id"] else "同轮文件") for version, name in source["originals"].items()]
    return [
        f"<p>被审文件：{text(source['filename'])}</p><p>审核规则：{text(audit['rule']['title'])}</p>",
        Table("表 2-1 受检文件集", ("序号", "文件", "角色"), [(str(number), name, role) for number, (name, role) in enumerate(files, 1)], nowrap=(0, 2)),
    ]


def _overview(source: dict) -> list:
    audit = source["audit"]
    results = _results(audit)
    blocks: list = [
        Table(
            "表 3-1 检查结果分布",
            ("审核项", "PASS", "NG", "待审核", "不适用"),
            [(str(len(audit["checks"])), *(str(results[label]) for label, _color in STAT_COLORS))],
        )
    ]
    levels = _problem_levels(source)
    if levels:
        blocks.append(Table("表 3-2 问题严重程度", ("A 类", "B 类", "C 类", "未定级"), [tuple(str(levels.count(level)) for level in (*LEVELS, None))]))
        if _warnings(source):
            blocks.append(f"<p class='meta'>C 类含本文件的预检警告 {len(_warnings(source))} 项。</p>")
        if None in levels:
            blocks.append("<p class='meta'>未定级的不符合项按「不通过」计入总评。</p>")
    supplies = [supply for _number, check in _failed(audit) if (supply := _supply(check))]
    blocks.append(f"<p>需补交或换交的关联文件：{len(supplies)} 份。</p>" if supplies else "<p>本次没有需补交或换交的关联文件。</p>")
    return blocks


def _problems(source: dict, levels: set) -> list:
    rows = [_detail_row(source, number, check) for number, check in _failed(source["audit"]) if _level(check) in levels]
    if "C" in levels:
        # A warning keeps its precheck number (W-01 …), apart from the clause numbers.
        rows += [_warning_row(warning.get("number") or f"W-{index:02d}", warning) for index, warning in enumerate(_warnings(source), 1)]
    named = "A · B 类" if "A" in levels else "C 类"
    blocks: list = [Table(f"表 {'4' if 'A' in levels else '5'}-1 {named}问题", DETAIL_HEAD, rows, empty=f"本次无{named}问题。", nowrap=DETAIL_NOWRAP)]
    if None in levels and any(_level(check) is None for _number, check in _failed(source["audit"])):
        blocks.append("<p class='meta'>严重程度为「—」的不符合项尚未定级，列入本章，按「不通过」计入总评。</p>")
    if "C" in levels and _warnings(source):
        blocks.append("<p class='meta'>结果列为 W01～W06 的是本文件在预检中的警告项，按 C 类计入。</p>")
    return blocks


def _supply_line(supply: dict) -> str:
    document, part = supply["document"], supply.get("part_number")
    if supply["kind"] == "missing":
        return f"补交：零件号 {part} 的《{document}》" if part else f"补交：《{document}》"
    return f"换交：《{document}》（零件号 {part}）" if part else f"换交：《{document}》"


def _register(source: dict) -> list:
    audit = source["audit"]
    rows = [(str(number), _item(check), _status(check), _explanation(check), text(_locations(source, check))) for number, check in enumerate(audit["checks"], 1) if _code(check) not in {"PASS", "NG"}]
    blocks: list = [
        Table("表 6-1 待审核与不适用项", ("序号", "审核项", "状态", "说明", "位置"), rows, empty="本次没有待审核或不适用的审核项。", nowrap=(0, 2)),
        "<p class='meta'>「待审核」项未计为 PASS，不计入 A / B / C 与问题总数，须对照原件人工审核。</p>",
    ]
    supplies = [supply for _number, check in _failed(audit) if (supply := _supply(check))]
    lines = [f"<p>{text(_supply_line(supply))}</p>" for supply in supplies] or ["<p>本次没有需补交或换交的关联文件。</p>"]
    blocks.append("<h3>需补交文件清单</h3>" + "".join(lines))
    return blocks


def _plain(value: str) -> str:
    """A frozen checker message without the empty phrases a remediation point may not carry."""
    cleaned = EMPTY_PHRASES.sub("", value or "").strip()
    return cleaned + ("" if not cleaned or cleaned[-1] in "。；;.!?！？" else "。")


def _ng_action(source: dict, check: dict) -> str:
    """The action for one NG: 增补 a file named by the evidence, or 改值 at its location to meet the clause.
    A correct value the evidence does not give is never invented."""
    if supply := _supply(check):
        document, part = supply["document"], supply.get("part_number")
        if supply["kind"] == "missing":
            return f"增补零件号 {part} 的《{document}》" if part else f"增补《{document}》"
        if part:
            return f"增补零件号 {part} 的《{document}》，删除本批零件号不一致的《{document}》"
        return f"增补与被审文件零件号一致的《{document}》，删除本批零件号不一致的《{document}》"
    rule = check["rule_item"]
    current = _plain((check.get("finding") or {}).get("suggestions") or "")
    # One executable sentence (PRD §2.11 六): the 审核项 names the requirement; its full clause text stays in 依据 and chapter 九.
    return f"改值：使「{_first_location(source, check)}」满足审核项「{rule.get('category') or rule['id']}」" + (f"（现状：{current.rstrip('。')}）" if current else "")


def _warning_action(warning: dict) -> str:
    code, problem, filename = warning["code"], warning["problem"], warning.get("filename") or ""
    if warning.get("scope") == "file":
        if code == "W02" and (found := re.search(r"未识别到(.+?)。", problem)):
            return f"增补：在《{filename}》表头填写{found.group(1)}"
        if code == "W01" and (found := re.search(r"缺少(同零件的《.+?》(?:（零件号 .+?）)?)", problem)):
            return f"增补{found.group(1)}"
        if code == "W05":
            return f"增补《{filename}》的可读原件（可编辑的 Excel / Word 或文字版 PDF）"
    return f"{WARNING_ACTION.get(code, '改值')}：{warning['location']}——{_plain(problem).rstrip('。')}"


def _remediation_items(source: dict) -> list[dict]:
    """Every problem as a remediation point, ordered by priority and then by the clause order of the file."""
    items = []
    for number, check in _failed(source["audit"]):
        rule = check["rule_item"]
        items.append(
            {
                "level": _level(check),
                "location": _first_location(source, check),
                "action": _ng_action(source, check),
                "basis": f"第 {number} 项 · 审核项 {rule['id']}（{rule['category']}）",
                "self_check": f"第 {number} 项 · 审核项 {rule['id']}（{rule['category']}）已满足",
            }
        )
    for warning in _warnings(source):
        items.append(
            {
                "level": "C",
                "location": warning["location"],
                "action": _warning_action(warning),
                "basis": f"预检 {warning['code']} {warning['title']}",
                "self_check": f"预检 {warning['code']}「{warning['title']}」不再出现",
            }
        )
    order = (*LEVELS, None)
    return sorted(items, key=lambda item: order.index(item["level"]))


def _remediation(source: dict) -> list:
    """PRD §2.11 六: 速览 first, then P1 → P2 → P3 with the five elements, then the self-check list.
    Only frozen results and the file's precheck warnings: no rewritten advice, no model call."""
    items = _remediation_items(source)
    if not items:
        return ["<p>本次未发现问题项，无需整改。</p>"]
    marks = "①②③④⑤⑥⑦⑧⑨⑩"
    # Points at one location within one priority merge into one, numbered ①②③.
    merged: dict[tuple[str | None, str], list[dict]] = {}
    for item in items:
        merged.setdefault((item["level"], item["location"]), []).append(item)
    groups = [(level, location, entries) for (level, location), entries in merged.items()]

    def points(entries: list[dict]) -> list[str]:
        return [f"{marks[index] if len(entries) > 1 and index < len(marks) else ''}{entry['action']}" for index, entry in enumerate(entries)]

    # One row per point, so a long merged location never makes a row taller than a page.
    overview = [(PRIORITY[level], text(point), text(location), DEADLINE[level]) for level, location, entries in groups for point in points(entries)]
    parts: list = [Table("表 7-1 整改要点速览", ("优先级", "整改要点", "位置", "建议截止日"), overview, nowrap=(0, 3))]
    index = 0
    for level in (*LEVELS, None):
        selected = [group for group in groups if group[0] == level]
        if level is None and not selected:
            continue
        if not selected:
            parts.append(f"<h2>{PRIORITY_TITLE[level]}</h2><p>本次无 {PRIORITY[level]} 项。</p>")
            continue
        parts.append(f"<h2>{PRIORITY_TITLE[level]}</h2>")
        for _level, location, entries in selected:
            index += 1
            lines = [f"<h3>整改要点 {index}</h3>"]
            lines += [f"<p>整改要点：{text(point)}</p>" for point in points(entries)]
            lines.append(f"<p>位置：{text(location)}</p>")
            lines += [f"<p class='meta'>依据：{text(entry['basis'])}</p>" for entry in entries]
            lines.append(f"<p>建议截止日：{DEADLINE[level]}</p>")
            lines.append(f"<p>整改益处：{BENEFIT[level]}</p>")
            parts.append("".join(lines))
    checks = [f"<p>□ {number}. {text(location)}：" + "；".join(text(entry["self_check"]) for entry in entries) + "</p>" for number, (_level, location, entries) in enumerate(groups, 1)]
    parts.append("<h2>修订后自查要点</h2>" + "".join(checks))
    return parts


def _limits(source: dict) -> list:
    lines = [
        "本报告记录所选文件版本和冻结要求的机器审核结果，不表示正式量产批准。",
        "「待审核」与「不适用」项均未计为 PASS，不计入 A / B / C 与问题总数；其原因与位置见第六章，须对照原件核对。",
        "C 类问题含本文件在预检中的警告项；预检不判定内容是否合规。",
        "表中「严重程度」「问题性质」显示「—」的，表示本次没有该项判定数据，不表示没有问题。",
        "供应商名称、PPAP 版本与 SQE 工程师由报告使用方下载后补充，本报告不判定其内容。",
    ]
    return ["".join(f"<p>· {line}</p>" for line in lines), f"<p class='meta'>生成时间：{_text(_shanghai(source['generated_at']).strftime('%Y-%m-%d %H:%M'))}</p>"]


def _appendix(source: dict) -> list:
    rows = [
        (str(number), _item(check), _status(check), dash(_level(check)), text(check["rule_item"]["rule_to_check"]), _explanation(check), _evidence(source, check))
        for number, check in enumerate(source["audit"]["checks"], 1)
    ]
    return [
        "<p>下表列出本规则的全部审核项及其结果，含 PASS、不适用、待审核与 NG 项。</p>",
        Table("表 9-1 检查项全量结果", ("序号", "审核项", "结果", "严重程度", "判定依据", "说明", "来源证据"), rows, nowrap=(0, 2, 3)),
    ]


def _shanghai(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai"))


def render_audit_pdf(source: dict) -> bytes:
    """No model calls, current document reads, human reviews or display edits."""
    checked_on = _shanghai(source.get("audited_at") or source["generated_at"]).strftime("%Y-%m-%d")
    chapters = [
        Chapter(chapter_title(1, "结论摘要"), _summary(source)),
        Chapter(chapter_title(2, "提交范围与文件集"), _scope(source)),
        Chapter(chapter_title(3, "问题总览"), _overview(source)),
        Chapter(chapter_title(4, "A · B 类问题明细"), _problems(source, {"A", "B", None})),
        Chapter(chapter_title(5, "C 类问题明细"), _problems(source, {"C"})),
        Chapter(chapter_title(6, "状态登记"), _register(source)),
        Chapter(chapter_title(7, "整改建议汇总"), _remediation(source)),
        Chapter(chapter_title(8, "结论作用域与局限性声明"), _limits(source)),
        Chapter(chapter_title(9, "附录 · 检查项全量结果"), _appendix(source)),
        Chapter(chapter_title(10, "附录 · 分则附加节"), ["<p>本章无附加节。</p>"]),
    ]
    return render_report(cover=_cover(source, checked_on), chapters=chapters, name=source["filename"] + "-审核报告", kind=KIND, date=checked_on)
