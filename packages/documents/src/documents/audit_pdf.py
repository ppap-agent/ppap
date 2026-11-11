"""Render an already accepted, frozen per-file machine result as a PDF."""

from datetime import datetime
from html import escape
from io import BytesIO
from zoneinfo import ZoneInfo

import pymupdf

STATUS = {"PASS": "符合", "NG": "不符合", "UNABLE": "无法判断", "ERR": "技术错误", "NA": "不适用"}
CONCLUSION = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "尚有未确定项"}


def _text(value) -> str:
    return escape(str(value if value is not None else ""))


def _verification_notice(verification: dict) -> str:
    # Escape the frozen notice first. Keep only an exact suite_revision_id from
    # breaking inside the token; the line may still break before or after it.
    # A missing or nonmatching id is not injected and is not matched in markup.
    raw = verification["notice"]
    notice = str(raw if raw is not None else "")
    revision_id = verification.get("suite_revision_id")
    if type(revision_id) is not str or revision_id == "" or revision_id not in notice:
        return escape(notice)
    token = '<span style="white-space:nowrap">' + escape(revision_id) + "</span>"
    return token.join(escape(part) for part in notice.split(revision_id))


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


# Cover: recognized fields first, then fields the reader completes after download.
COVER_FIELDS = (("customer", "客户名称"), ("project", "项目名称"), ("model", "车型"), ("part_number", "零件号"), ("part", "零件名称"))
READER_FIELDS = ("供应商名称", "PPAP 版本", "SQE 工程师")
# A long table repeats its header only by starting a new chunk; rows are short.
TABLE_ROWS = 20
LEVELS = ("A", "B", "C")
PAGE = pymupdf.Rect(0, 0, 595, 842)
# A4 portrait, single column: 20 mm top/bottom and 18 mm left/right margins.
BODY = pymupdf.Rect(51, 57, 544, 785)
# MuPDF maps sans-serif to Nimbus Sans, which shapes ff/fi/fl into compatibility
# ligatures (U+FB00 and neighbors); its CJK font does the same and CSS cannot turn
# that off. Monospace keeps those letters as ASCII, so a copied or extracted frozen
# identity stays the original string. Chinese text uses MuPDF's embedded CJK fallback.
CSS = (
    "body {font-family:monospace; font-size:10.5pt; line-height:1.6} h1 {font-size:20pt} h2 {font-size:14pt} "
    "h3 {font-size:11pt} p {white-space:pre-wrap} .meta {font-size:9pt; color:#555} "
    "table {border-collapse:collapse; width:100%} th, td {border:0.5pt solid #888; padding:2pt 4pt; font-size:9pt; line-height:1.4; text-align:left; vertical-align:top} "
    "th {background-color:#eee} .ng {font-weight:bold; background-color:#ddd}"
)


def _first_location(source: dict, check: dict) -> str:
    for evidence in check["evidence"]:
        if evidence.get("locator"):
            return f"{source['originals'][evidence['file_version_id']]} · {_location(evidence['locator'])}"
    return "未提供具体位置"


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
    if audit["conclusion"] == "NG" and failed and all(_level(check) == "C" for check in failed):
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


def _cover(source: dict, audited_on: str) -> str:
    audit, recognition = source["audit"], source.get("recognition") or {}
    counts = audit["counts"]
    rows = [(label, recognition.get(key) or "—") for key, label in COVER_FIELDS]
    rows += [("审核规则", audit["rule"]["title"]), ("检查日期", audited_on)]
    rows += [(label, "") for label in READER_FIELDS]
    table = "".join(f"<tr><th>{_text(label)}</th><td>{_text(value)}</td></tr>" for label, value in rows)
    return "".join(
        [
            "<h1>PPAP 审核报告</h1>",
            f"<h2>{_text(source['filename'])}</h2>",
            f"<p>审核结论：<b>{_text(CONCLUSION.get(audit['conclusion'], audit['conclusion']))}</b>　总评：<b>{_rating(audit)}</b></p>",
            f"<p>共 {len(audit['checks'])} 项：符合 {counts['pass_count']} 项，不符合 {counts['ng_count']} 项，不适用 {counts.get('na_count', 0)} 项。</p>",
            *([f"<p><b>{_verification_notice(audit['checker_verification'])}</b></p>"] if audit.get("checker_verification") else []),
            f"<table>{table}</table>",
            "<p class='meta'>供应商名称、PPAP 版本与 SQE 工程师请下载后自行补充。</p>",
        ]
    )


def _overview(source: dict) -> str:
    audit = source["audit"]
    counts = audit["counts"]
    supplies = [supply for check in audit["checks"] if (supply := _supply(check))]
    parts = [
        "<h1>1 检查概览</h1>",
        f"<p>审核结论：<b>{_text(CONCLUSION.get(audit['conclusion'], audit['conclusion']))}</b>　总评：<b>{_rating(audit)}</b></p>",
        "<table><tr><th>检查项</th><th>符合</th><th>不符合</th><th>不适用</th></tr>",
        f"<tr><td>{len(audit['checks'])}</td><td>{counts['pass_count']}</td><td>{counts['ng_count']}</td><td>{counts.get('na_count', 0)}</td></tr></table>",
    ]
    failed = [check for check in audit["checks"] if check["finding"]["status"] == "NG"]
    if failed:
        levels = [_level(check) for check in failed]
        cells = "".join(f"<td>{levels.count(level)}</td>" for level in (*LEVELS, None))
        parts.append(f"<table><tr><th>A 类</th><th>B 类</th><th>C 类</th><th>未定级</th></tr><tr>{cells}</tr></table>")
        parts.append("<p class='meta'>未定级的不符合项按「不通过」计入总评。</p>")
    scopes = [check["scope"] for check in audit["checks"] if check.get("scope")]
    if scopes:
        parts.append(f"<p>检查范围：已查 {sum(scope['checked'] for scope in scopes)}/{sum(scope['total'] for scope in scopes)}，未知 {sum(scope['unknown'] for scope in scopes)}。</p>")
    if any(check.get("finding") and not check.get("evidence_verified") for check in audit["checks"]):
        parts.append("<p class='meta'>部分判定的证据未经验证，请核对原件中的证据和检查范围。</p>")
    parts.append(f"<p>需补交或换交的关联文件：{len(supplies)} 份。</p>" if supplies else "<p>本次没有需补交或换交的关联文件。</p>")
    return "".join(parts)


def _detail_tables(source: dict) -> list[str]:
    checks = source["audit"]["checks"]
    rows = [
        f"<tr class='{'ng' if check['finding']['status'] == 'NG' else ''}'><td>{number}</td><td>{_text(check['rule_item']['id'])} · {_text(check['rule_item']['category'])}</td>"
        f"<td>{_text(STATUS.get(check['finding']['status'], check['finding']['status']))}</td>"
        f"<td>{(_level(check) or '未定级') if check['finding']['status'] == 'NG' else '—'}</td><td>{_text(_first_location(source, check))}</td></tr>"
        for number, check in enumerate(checks, 1)
    ]
    head = "<tr><th>序号</th><th>审核项</th><th>结果</th><th>严重程度</th><th>位置</th></tr>"
    chunks = [rows[index : index + TABLE_ROWS] for index in range(0, len(rows), TABLE_ROWS)] or [[]]
    return [("<h1>2 问题详情</h1><h3>检查结果一览</h3>" if index == 0 else "<h3>检查结果一览（续表）</h3>") + f"<table>{head}{''.join(chunk)}</table>" for index, chunk in enumerate(chunks)]


def _details(source: dict) -> str:
    parts = ["<h3>逐项判定依据</h3>"]
    for number, check in enumerate(source["audit"]["checks"], 1):
        rule, finding = check["rule_item"], check["finding"]
        parts.extend(
            [
                f"<hr><h3>{number}. {_text(rule['id'])} · {_text(rule['category'])} - {_text(STATUS.get(finding['status'], finding['status']))}</h3>",
                f"<p><b>审核规则</b>　{_text(rule['rule_to_check'])}</p>",
                f"<p><b>判定说明</b>　{_text(finding.get('suggestions') or finding.get('classification'))}</p>",
            ]
        )
        if note := _grading_note(check):
            parts.append(f"<p><b>说明</b>　{_text(note)}</p>")
        scope = check.get("scope")
        if scope:
            parts.append(f"<p class='meta'>检查范围：已查 {scope['checked']}/{scope['total']}，未知 {scope['unknown']}。</p>")
        if check.get("scope_notice"):
            parts.append(f"<p class='meta'>{_text(check['scope_notice'])}</p>")
        parts.append("<p><b>来源证据</b></p>")
        if not check["evidence"]:
            parts.append("<p class='meta'>未提供原件定位与摘录。</p>")
        for evidence in check["evidence"]:
            name = source["originals"][evidence["file_version_id"]]
            locator = evidence.get("locator")
            location = _location(locator) if locator else evidence.get("location_gap") or "未提供具体位置。"
            excerpt = evidence.get("excerpt") or ("该位置内容为空。" if locator else "未提供摘录。")
            parts.append(f"<p>{_text(name)} · {_text(location)}</p><p>{_text(excerpt)}</p>")
    return "".join(parts)


def _remediation(source: dict) -> str:
    """Only frozen checker findings: no rewritten advice, no model call."""
    failed = [(number, check) for number, check in enumerate(source["audit"]["checks"], 1) if check["finding"]["status"] == "NG"]
    if not failed:
        return "<h1>3 整改建议</h1><p>本次未发现问题项，无需整改。</p>"
    graded = any(_level(check) for _number, check in failed)
    order = "A → B → C → 未定级、再按检查项顺序" if graded else "检查项顺序"
    parts = ["<h1>3 整改建议</h1>", f"<p>以下按{order}列出不符合项；同一位置的多项合并为一条。</p>"]
    marks = "①②③④⑤⑥⑦⑧⑨⑩"
    index = 0
    for level in (*LEVELS, None) if graded else (None,):
        groups: dict[str, list] = {}
        for number, check in failed:
            if not graded or _level(check) == level:
                groups.setdefault(_first_location(source, check), []).append((number, check))
        if graded:
            parts.append(f"<h2>{level + ' 类' if level else '未定级'}</h2>" + ("" if groups else f"<p>本次无{level + ' 类' if level else '未定级'}问题。</p>"))
        for location, items in groups.items():
            index += 1
            parts.append(f"<h3>{index}. 位置：{_text(location)}</h3>")
            for position, (number, check) in enumerate(items):
                mark = marks[position] if len(items) > 1 and position < len(marks) else ""
                rule = check["rule_item"]
                parts.append(f"<p>{mark}【{_text(rule['category'])}】{_text(check['finding'].get('suggestions'))}</p>")
                parts.append(f"<p class='meta'>依据：第 {number} 项 · 检查项 {_text(rule['id'])}</p>")
    supplies = [supply for _number, check in failed if (supply := _supply(check)) and supply["kind"] == "missing"]
    if supplies:
        parts.append("<h3>需补交文件清单</h3>")
        for supply in supplies:
            part = f"（零件号 {supply['part_number']}）" if supply.get("part_number") else ""
            parts.append(f"<p>补交：{_text(supply['document'])}{_text(part)}</p>")
    return "".join(parts)


def _appendix(source: dict) -> str:
    audit = source["audit"]
    files = "".join(f"<tr><td>{_text(name)}</td><td>{'本报告被审文件' if version == audit['file_version_id'] else '同轮文件'}</td></tr>" for version, name in source["originals"].items())
    return "".join(
        [
            "<h1>4 附录</h1><h3>受检文件集</h3>",
            f"<table><tr><th>文件</th><th>角色</th></tr>{files}</table>",
            f"<h3>审核规则</h3><p>{_text(audit['rule']['title'])}</p>",
            *([f"<h3>检查版本</h3><p>{_verification_notice(audit['checker_verification'])}</p>"] if audit.get("checker_verification") else []),
            f"<p class='meta'>生成时间：{_text(source['generated_at'])}</p>",
            "<p class='meta'>本报告记录所选文件版本和冻结要求的机器审核结果，不表示正式量产批准。</p>",
        ]
    )


def _audited_on(source: dict) -> str:
    moment = datetime.fromisoformat(source.get("audited_at") or source["generated_at"])
    return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def render_audit_pdf(source: dict) -> bytes:
    """No model calls, current document reads, human reviews or display edits."""
    audit = source["audit"]
    audited_on = _audited_on(source)
    # Each section starts a new page. MuPDF loops on CSS page breaks, so every
    # section is a separate story.
    sections = [_cover(source, audited_on), _overview(source), *_detail_tables(source), _details(source), _remediation(source), _appendix(source)]
    if sum(len(section) for section in sections) > 4_000_000:
        raise ValueError("report_content_too_large")
    output = BytesIO()
    pages = 0
    with pymupdf.DocumentWriter(output) as writer:
        for section in sections:
            story = pymupdf.Story(html=f'<html lang="zh"><body>{section}</body></html>', user_css=CSS)
            more = 1
            while more:
                if pages >= 500:
                    raise ValueError("report_page_limit")
                device = writer.begin_page(PAGE)
                more, _filled = story.place(BODY)
                story.draw(device)
                writer.end_page()
                pages += 1
    title = source["filename"] + "-审核报告"
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document:
        document.set_metadata(
            {
                "title": title,
                "subject": f"round={audit['round_id']};file_version={audit['file_version_id']};result_attempt={audit['result_attempt']};rule_version={audit['rule']['id']}",
            }
        )
        # The cover has no header or footer; page numbers start on the first body page.
        # Same fonts as the body, so the header keeps ASCII identities unshaped.
        margin = "body {font-family:monospace; font-size:8pt; color:#666} p {margin:0}"
        header = _text(title if len(title) <= 50 else title[:49] + "…")
        for index, page in enumerate(document):
            if index == 0:
                continue
            page.insert_htmlbox(pymupdf.Rect(51, 30, 400, 50), f"<p>{header}</p>", css=margin)
            page.insert_htmlbox(pymupdf.Rect(400, 30, 544, 50), "<p style='text-align:right'>PPAP 审核报告</p>", css=margin)
            page.draw_line((51, 48), (544, 48), color=(0.6, 0.6, 0.6), width=0.5)
            page.insert_htmlbox(pymupdf.Rect(51, 800, 300, 820), f"<p>{audited_on}</p>", css=margin)
            page.insert_htmlbox(pymupdf.Rect(300, 800, 544, 820), f"<p style='text-align:right'>第 {index} / {len(document) - 1} 页</p>", css=margin)
        document.subset_fonts()
        return document.tobytes(garbage=4, deflate=True)


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
