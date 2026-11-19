"""Render an already accepted, frozen per-file machine result as a PDF: a cover and ten chapters."""

from datetime import datetime
from html import escape
from io import BytesIO
from zoneinfo import ZoneInfo

import pymupdf

from documents.report_layout import Chapter, Table, chapter_title, dash, render_report, text

STATUS = {"PASS": "符合", "NG": "不符合", "UNABLE": "无法判断", "ERR": "技术错误", "NA": "不适用"}
CONCLUSION = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "尚有未确定项"}
KIND = "PPAP 审核报告"
# Cover: recognized fields first (frozen recognition of the accepted task), the check date, then fields the reader completes.
COVER_FIELDS = (("customer", "客户全称"), ("project", "项目"), ("model", "车型"), ("part_number", "零件号"), ("part", "零件名称"))
READER_FIELDS = ("供应商名称", "PPAP 版本", "SQE 工程师")
LEVELS = ("A", "B", "C")
# The same four counts as the summary line, never a risk grade.
STAT_COLORS = (("符合", "pass_count", "#2ea121"), ("不符合", "ng_count", "#d83931"), ("无法判断", "unknown_count", "#de7802"), ("不适用", "na_count", "#8f959e"))
DETAIL_HEAD = ("序号", "审核项", "结果", "严重程度", "问题性质", "判定依据", "说明", "位置")
DETAIL_NOWRAP = (0, 2, 3, 4)


def _text(value) -> str:
    return escape(str(value if value is not None else ""))


def _location(locator: dict) -> str:
    kind = locator.get("kind")
    if kind == "xlsx_cell":
        return f"{locator['sheet']}!{locator['cell']}"
    if kind == "xlsx_range":
        return f"{locator['sheet']}!{locator['start']}:{locator['end']}"
    if kind == "pdf_block":
        return f"第 {locator['page']} 页，第 {locator['block']} 块"
    if kind == "pdf_cell":
        if set(locator) != {"kind", "page", "row", "column"} or any(type(locator.get(field)) is not int or locator[field] < 1 for field in ("page", "row", "column")):
            raise ValueError("report_locator_unavailable")
        return f"第 {locator['page']} 页 · 表格行 {locator['row']} · 列 {locator['column']}"
    if kind == "docx_paragraph":
        return f"第 {locator['paragraph']} 段"
    if kind == "docx_cell":
        return f"表 {locator['table']}，行 {locator['row']}，列 {locator['column']}"
    raise ValueError("report_locator_unavailable")


def _first_location(source: dict, check: dict) -> str:
    for evidence in check["evidence"]:
        if evidence.get("locator"):
            return f"{source['originals'][evidence['file_version_id']]} · {_location(evidence['locator'])}"
    return "未提供具体位置"


def _locations(source: dict, check: dict) -> str:
    places = []
    for evidence in check["evidence"]:
        locator = evidence.get("locator")
        place = _location(locator) if locator else evidence.get("location_gap") or "未提供具体位置。"
        places.append(f"{source['originals'][evidence['file_version_id']]} · {place}")
    return "；".join(places) or "未提供具体位置"


def _evidence(source: dict, check: dict) -> str:
    if not check["evidence"]:
        return "未提供原件定位与摘录。"
    parts = []
    for evidence in check["evidence"]:
        locator = evidence.get("locator")
        place = _location(locator) if locator else evidence.get("location_gap") or "未提供具体位置。"
        excerpt = evidence.get("excerpt") or ("该位置内容为空。" if locator else "未提供摘录。")
        parts.append(f"{text(source['originals'][evidence['file_version_id']])} · {text(place)}<br/>{text(excerpt)}")
    return "<br/>".join(parts)


def _semantic(check: dict) -> dict:
    semantic = ((check["finding"] or {}).get("locator") or {}).get("semantic")
    return semantic if isinstance(semantic, dict) else {}


def _supply(check: dict) -> dict | None:
    supply = _semantic(check).get("supply")
    return supply if isinstance(supply, dict) and supply.get("kind") in {"missing", "source_mismatch"} else None


def _level(check: dict) -> str | None:
    """Final level of an NG; None when neither the rule nor the checker gave one."""
    level = _semantic(check).get("severity")
    return level if check["finding"]["status"] == "NG" and level in LEVELS else None


def _rating(audit: dict) -> str:
    """An NG without a level is not shown to be minor, so it cannot pass with conditions."""
    failed = [check for check in audit["checks"] if check["finding"]["status"] == "NG"]
    if audit["conclusion"] == "PASS" and not failed:
        return "通过"
    undecided = any(check["finding"]["status"] == "UNABLE" for check in audit["checks"])
    if audit["conclusion"] == "NG" and failed and not undecided and all(_level(check) == "C" for check in failed):
        return "有条件通过"
    return "不通过" if failed else CONCLUSION.get(audit["conclusion"], audit["conclusion"])


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
    status = check["finding"]["status"]
    label = text(STATUS.get(status, status))
    return f"<span class='strong'>{label}</span>" if status != "PASS" else label


def _item(check: dict) -> str:
    rule = check["rule_item"]
    return f"{text(rule['id'])} · {text(rule['category'])}"


def _explanation(check: dict) -> str:
    finding = check["finding"]
    lines = [finding.get("suggestions") or finding.get("classification")]
    lines.append(_grading_note(check))
    if scope := check.get("scope"):
        lines.append(f"检查范围：已查 {scope['checked']}/{scope['total']}，未知 {scope['unknown']}。")
    lines.append(check.get("scope_notice"))
    return "<br/>".join(text(line) for line in lines if line) or "—"


def _detail_row(source: dict, number: int, check: dict) -> tuple[str, ...]:
    # 问题性质 has no frozen value yet; it is shown empty rather than guessed.
    return (str(number), _item(check), _status(check), dash(_level(check)), "—", text(check["rule_item"]["rule_to_check"]), _explanation(check), text(_locations(source, check)))


def _cover(source: dict, checked_on: str, customer_name) -> str:
    # The frozen recognition stays as recorded; only its customer value is shown by the registered full name.
    recognition = dict(source.get("recognition") or {})
    if recognition.get("customer"):
        recognition["customer"] = customer_name(recognition["customer"])
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


def _summary(source: dict) -> list:
    audit = source["audit"]
    counts = audit["counts"]
    blocks = [
        f"<p>审核结论：<b>{_text(CONCLUSION.get(audit['conclusion'], audit['conclusion']))}</b>　总评：<b>{_rating(audit)}</b></p>"
        f"<p>共 {len(audit['checks'])} 项：符合 {counts['pass_count']} 项，不符合 {counts['ng_count']} 项，"
        f"无法判断 {counts.get('unknown_count', 0)} 项，不适用 {counts.get('na_count', 0)} 项。</p>",
        "<table><tr>"
        + "".join(f"<td style='text-align:center'>{label}<br/><b style='color:{color}; font-size:16pt'>{counts.get(key, 0)}</b></td>" for label, key, color in STAT_COLORS)
        + "</tr></table>",
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
    counts = audit["counts"]
    blocks: list = [
        Table(
            "表 3-1 检查结果分布",
            ("检查项", "符合", "不符合", "无法判断", "不适用"),
            [(str(len(audit["checks"])), str(counts["pass_count"]), str(counts["ng_count"]), str(counts.get("unknown_count", 0)), str(counts.get("na_count", 0)))],
        )
    ]
    failed = [check for check in audit["checks"] if check["finding"]["status"] == "NG"]
    if failed:
        levels = [_level(check) for check in failed]
        blocks.append(Table("表 3-2 不符合项严重程度", ("A 类", "B 类", "C 类", "未定级"), [tuple(str(levels.count(level)) for level in (*LEVELS, None))]))
        blocks.append("<p class='meta'>未定级的不符合项按「不通过」计入总评。</p>")
    supplies = [supply for check in audit["checks"] if check["finding"]["status"] == "NG" and (supply := _supply(check))]
    blocks.append(f"<p>需补交或换交的关联文件：{len(supplies)} 份。</p>" if supplies else "<p>本次没有需补交或换交的关联文件。</p>")
    return blocks


def _problems(source: dict, levels: set) -> list:
    rows = [_detail_row(source, number, check) for number, check in enumerate(source["audit"]["checks"], 1) if check["finding"]["status"] == "NG" and _level(check) in levels]
    named = "A · B 类" if "A" in levels else "C 类"
    blocks: list = [Table(f"表 {'4' if 'A' in levels else '5'}-1 {named}问题", DETAIL_HEAD, rows, empty=f"本次无{named}问题。", nowrap=DETAIL_NOWRAP)]
    if None in levels and any(check["finding"]["status"] == "NG" and _level(check) is None for check in source["audit"]["checks"]):
        blocks.append("<p class='meta'>严重程度为「—」的不符合项尚未定级，列入本章，按「不通过」计入总评。</p>")
    return blocks


def _register(source: dict) -> list:
    rows = [
        (str(number), _item(check), _status(check), _explanation(check), text(_locations(source, check)))
        for number, check in enumerate(source["audit"]["checks"], 1)
        if check["finding"]["status"] not in {"PASS", "NG"}
    ]
    return [
        Table("表 6-1 无法判断、技术错误与不适用项", ("序号", "审核项", "状态", "说明", "位置"), rows, empty="本次没有无法判断、技术错误或不适用的检查项。", nowrap=(0, 2)),
        "<p class='meta'>「无法判断」和「技术错误」均未计为符合，须对照原件人工核对。</p>",
    ]


def _remediation(source: dict) -> list:
    """Only frozen checker findings: no rewritten advice, no model call."""
    failed = [(number, check) for number, check in enumerate(source["audit"]["checks"], 1) if check["finding"]["status"] == "NG"]
    if not failed:
        return ["<p>本次未发现问题项，无需整改。</p>"]
    graded = any(_level(check) for _number, check in failed)
    order = "A → B → C → 未定级、再按检查项顺序" if graded else "检查项顺序"
    parts = [f"<p>以下按{order}列出不符合项；同一位置的多项合并为一条。</p>"]
    marks = "①②③④⑤⑥⑦⑧⑨⑩"
    index = 0
    for level in (*LEVELS, None) if graded else (None,):
        groups: dict[str, list] = {}
        for number, check in failed:
            if not graded or _level(check) == level:
                groups.setdefault(_first_location(source, check), []).append((number, check))
        if graded:
            named = level + " 类" if level else "未定级"
            parts.append(f"<h2>{named}</h2>" + ("" if groups else f"<p>本次无{named}问题。</p>"))
        for location, items in groups.items():
            index += 1
            lines = [f"<h3>{index}. 位置：{text(location)}</h3>"]
            for position, (number, check) in enumerate(items):
                mark = marks[position] if len(items) > 1 and position < len(marks) else ""
                rule = check["rule_item"]
                lines.append(f"<p>{mark}【{text(rule['category'])}】{text(check['finding'].get('suggestions'))}</p>")
                lines.append(f"<p class='meta'>依据：第 {number} 项 · 检查项 {text(rule['id'])}</p>")
            parts.append("".join(lines))
    supplies = [supply for _number, check in failed if (supply := _supply(check))]
    if supplies:
        lines = ["<h3>需补交或换交文件清单</h3>"]
        for supply in supplies:
            document, part = supply["document"], supply.get("part_number")
            if supply["kind"] == "missing":
                line = f"补交：零件号 {part} 的《{document}》" if part else f"补交：《{document}》"
            else:
                line = f"换交：《{document}》（零件号 {part}）" if part else f"换交：《{document}》"
            lines.append(f"<p>{text(line)}</p>")
        parts.append("".join(lines))
    return parts


def _limits(source: dict) -> list:
    lines = [
        "本报告记录所选文件版本和冻结要求的机器审核结果，不表示正式量产批准。",
        "「无法判断」「技术错误」与「不适用」项均未计为符合；其原因与位置见第六章，须对照原件核对。",
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
        "<p>下表列出本规则的全部检查项及其结果，含符合、不适用、无法判断与不符合项。</p>",
        Table("表 9-1 检查项全量结果", ("序号", "审核项", "结果", "严重程度", "判定依据", "说明", "来源证据"), rows, nowrap=(0, 2, 3)),
    ]


def _shanghai(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai"))


def render_audit_pdf(source: dict, customer_name=lambda value: value) -> bytes:
    """No model calls, current document reads, human reviews or display edits.

    ``customer_name`` maps the recognized customer to its registered full name (``product.customers.full_name``).
    """
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
    return render_report(cover=_cover(source, checked_on, customer_name), chapters=chapters, name=source["filename"] + "-审核报告", kind=KIND, date=checked_on)


def render_fact_confirmation_pdf(source: dict) -> bytes:
    """Separate human-confirmation report; the original machine PDF is immutable."""
    results = {result["item_key"]: result for result in source["results"]}
    parts = [
        '<html lang="zh"><body><h1>PPAP 原件事实人工确认报告</h1>',
        f"<p>审核轮次：{_text(source['round_id'])}；回答修订：{source['revision']}</p>",
        "<p>机器结果与人工确认分别记录。回答仅用于本轮所选文件版本，不修改规则，不调用模型，不自动作为训练样本。</p>",
    ]
    for finding in source["machine_findings"]:
        locator = finding.get("locator") or {}
        parts.extend(
            [
                f"<h2>{_text(locator.get('filename') or locator.get('file_version_id'))} · {_text(locator.get('rule_item_id') or finding['item_key'])}</h2>",
                f"<p>原机器判定：{_text(finding['status'])} · {_text(finding['suggestions'])}</p>",
            ]
        )
        if finding["item_key"] not in results:
            continue
        result = results[finding["item_key"]]
        parts.append(f"<p><b>人工确认：{_text(result['status'])}</b> · {_text(result['suggestions'])}</p>")
        for answer in result["sources"]:
            value = "看不清" if answer["value"] is None else "是" if answer["value"] is True else "否" if answer["value"] is False else answer["value"]
            position = answer["locator"]
            location = f"第 {position['page']} 页" if position.get("kind") == "pdf_page" else _location(position)
            parts.append(f"<p>依据：文件版本 {_text(answer['file_version_id'])} · {_text(location)} · {_text(answer['field'])} · 回答：{_text(value)}</p>")
            parts.append(f"<p>回答人：{_text(answer['answered_by'])}；时间：{_text(answer['answered_at'])}</p>")
    parts.append("</body></html>")
    html = "".join(parts)
    if len(html) > 4_000_000:
        raise ValueError("report_content_too_large")
    story = pymupdf.Story(html=html, user_css="body {font-family:sans-serif; font-size:10pt; line-height:1.6} h1 {font-size:18pt} h2 {font-size:12pt} p {white-space:pre-wrap}")
    output = BytesIO()
    with pymupdf.DocumentWriter(output) as writer:

        def page_rect(number, filled):
            if number >= 500:
                raise ValueError("report_page_limit")
            return pymupdf.Rect(0, 0, 595, 842), pymupdf.Rect(42, 42, 553, 786), None

        story.write(writer, page_rect)
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document:
        document.set_metadata({"title": "PPAP 原件事实人工确认报告", "subject": f"round={source['round_id']};fact_revision={source['revision']}"})
        document.subset_fonts()
        return document.tobytes(garbage=4, deflate=True)
