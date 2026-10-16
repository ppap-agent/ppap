"""Render an already accepted, frozen per-file machine result as a PDF."""

from html import escape
from io import BytesIO

import pymupdf


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
        f"<p>审核结论：<b>{_text(audit['conclusion'])}</b></p>",
        f"<p>满足 {audit['counts']['pass_count']} 项；不符合 {audit['counts']['ng_count']} 项。</p>",
        f"<p class='meta'>生成时间：{_text(source['generated_at'])}</p>",
    ]
    for check in audit["checks"]:
        rule, finding = check["rule_item"], check["finding"]
        parts.extend(
            [
                f"<hr><h2>{_text(rule['id'])} · {_text(rule['category'])} - {_text(finding['status'])}</h2>",
                f"<h3>审核要求</h3><p>{_text(rule['rule_to_check'])}</p>",
                f"<h3>判定说明</h3><p>{_text(finding.get('suggestions') or finding.get('classification'))}</p>",
                f"<p class='meta'>检查范围：已查 {check['scope']['checked']}/{check['scope']['total']}，未知 {check['scope']['unknown']}。</p>",
                "<h3>来源证据</h3>",
            ]
        )
        for evidence in check["evidence"]:
            name = source["originals"][evidence["file_version_id"]]
            parts.append(f"<p>{_text(name)} · {_text(_location(evidence['locator']))}</p><p>{_text(evidence['excerpt'] or '该位置内容为空。')}</p>")
    summary = audit.get("summary")
    if summary:
        parts.append(f"<hr><h2>审核总结</h2><p>{_text(summary['text'])}</p>")
        for title, field in (("核心整改建议", "corrective_actions"), ("AI 延伸建议（不是客户要求）", "extended_suggestions")):
            if summary[field]:
                parts.append(f"<h3>{title}</h3>" + "".join(f"<p>{_text(item)}</p>" for item in summary[field]))
    parts.append("<hr><p class='meta'>本报告记录所选文件版本和冻结要求的机器审核结果，不表示正式量产批准。</p></body></html>")
    html = "".join(parts)
    if len(html) > 4_000_000:
        raise ValueError("report_content_too_large")
    css = "body {font-family:sans-serif; font-size:10.5pt; line-height:1.6} h1 {font-size:20pt} h2 {font-size:13pt} "
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
