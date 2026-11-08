"""Render an already accepted, frozen per-file machine result as a PDF."""

from html import escape
from io import BytesIO

import pymupdf

STATUS = {"PASS": "符合", "NG": "不符合", "UNABLE": "无法判断", "ERR": "技术错误", "NA": "不适用"}
CONCLUSION = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "尚有未确定项"}


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


def render_audit_pdf(source: dict) -> bytes:
    """No model calls, current document reads, human reviews or display edits."""
    audit = source["audit"]
    parts = [
        '<html lang="zh"><body><h1>PPAP 审核报告</h1>',
        f"<h2>{_text(source['filename'])}</h2>",
        f"<p>审核规则：{_text(audit['rule']['title'])}</p>",
        f"<p>审核结论：<b>{_text(CONCLUSION.get(audit['conclusion'], audit['conclusion']))}</b></p>",
        *([f"<p><b>{_text(audit['checker_verification']['notice'])}</b></p>"] if audit.get("checker_verification") else []),
        f"<p>共 {len(audit['checks'])} 项：符合 {audit['counts']['pass_count']} 项，不符合 {audit['counts']['ng_count']} 项，不适用 {audit['counts'].get('na_count', 0)} 项。</p>",
        *(["<p class='meta'>部分判定的证据未经验证，请核对原件中的证据和检查范围。</p>"] if any(check.get("finding") and not check.get("evidence_verified") for check in audit["checks"]) else []),
        f"<p class='meta'>生成时间：{_text(source['generated_at'])}</p>",
    ]
    for check in audit["checks"]:
        rule, finding = check["rule_item"], check["finding"]
        parts.extend(
            [
                f"<hr><h2>{_text(rule['id'])} · {_text(rule['category'])} - {_text(STATUS.get(finding['status'], finding['status']))}</h2>",
                f"<h3>审核规则</h3><p>{_text(rule['rule_to_check'])}</p>",
                f"<h3>判定说明</h3><p>{_text(finding.get('suggestions') or finding.get('classification'))}</p>",
            ]
        )
        scope = check.get("scope")
        if scope:
            parts.append(f"<p class='meta'>检查范围：已查 {scope['checked']}/{scope['total']}，未知 {scope['unknown']}。</p>")
        if check.get("scope_notice"):
            parts.append(f"<p class='meta'>{_text(check['scope_notice'])}</p>")
        parts.append("<h3>来源证据</h3>")
        if not check["evidence"]:
            parts.append("<p class='meta'>未提供原件定位与摘录。</p>")
        for evidence in check["evidence"]:
            name = source["originals"][evidence["file_version_id"]]
            locator = evidence.get("locator")
            location = _location(locator) if locator else evidence.get("location_gap") or "未提供具体位置。"
            excerpt = evidence.get("excerpt") or ("该位置内容为空。" if locator else "未提供摘录。")
            parts.append(f"<p>{_text(name)} · {_text(location)}</p><p>{_text(excerpt)}</p>")
    parts.append("<hr><p class='meta'>本报告记录所选文件版本和冻结要求的机器审核结果，不表示正式量产批准。</p></body></html>")
    html = "".join(parts)
    if len(html) > 4_000_000:
        raise ValueError("report_content_too_large")
    # MuPDF maps sans-serif to Nimbus Sans, which shapes ff/fi/fl into compatibility
    # ligatures (U+FB00 and neighbors). Monospace keeps those letters as ASCII, so a
    # copied or extracted frozen identity stays the original string.
    # Known tradeoff: the whole audit-report body uses monospace, so Latin width,
    # wrapping, and pagination can change. This is not an identity-only style.
    # render_fact_confirmation_pdf stays on sans-serif and is outside this fix.
    css = "body {font-family:monospace; font-size:10.5pt; line-height:1.6} h1 {font-size:20pt} h2 {font-size:13pt} "
    css += "h3 {font-size:11pt} p {white-space:pre-wrap} .meta {font-size:9pt; color:#555}"
    story = pymupdf.Story(html=html, user_css=css)
    output = BytesIO()
    with pymupdf.DocumentWriter(output) as writer:

        def page_rect(number, filled):
            if number >= 500:
                raise ValueError("report_page_limit")
            return pymupdf.Rect(0, 0, 595, 842), pymupdf.Rect(42, 42, 553, 786), None

        story.write(writer, page_rect)
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document:
        document.set_metadata(
            {
                "title": source["filename"] + "-审核报告",
                "subject": f"round={audit['round_id']};file_version={audit['file_version_id']};result_attempt={audit['result_attempt']};rule_version={audit['rule']['id']}",
            }
        )
        for index, page in enumerate(document):
            page.insert_text((275, 814), f"{index + 1} / {len(document)}", fontsize=9, color=(0.4, 0.4, 0.4))
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
