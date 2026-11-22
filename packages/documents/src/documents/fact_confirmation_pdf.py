"""Human-confirmation PDF of original facts, kept apart from the immutable machine report.

What it shows is what the user knows: file names, places in the file, the question asked and who answered.
Round, file-version and user identifiers stay out of the text and the document properties.
"""

from io import BytesIO

import pymupdf

from documents.location import location_label
from documents.report_layout import text

TITLE = "PPAP 原件事实人工确认报告"
KIND = "PPAP 人工确认报告"
VALUES = {None: "看不清", True: "是", False: "否"}


def _value(value) -> str:
    return VALUES[value] if value is None or isinstance(value, bool) else str(value)


def render_fact_confirmation_pdf(source: dict) -> bytes:
    """``source`` is ``export_fact_answers``: ``originals`` names each file version, ``answerers`` each answering member."""
    originals, answerers = source["originals"], source["answerers"]
    results = {result["item_key"]: result for result in source["results"]}
    prompts = {(answer["file_version_id"], answer["field"]): answer["prompt"] for answer in source["answers"]}
    parts = [
        f'<html lang="zh"><body><h1>{TITLE}</h1>',
        f"<p>回答修订：{source['revision']}</p>",
        "<p>机器结果与人工确认分别记录。回答仅用于本轮所选文件版本，不修改规则，不调用模型，不自动作为训练样本。</p>",
    ]
    for finding in source["machine_findings"]:
        locator = finding.get("locator") or {}
        filename = originals.get(locator.get("file_version_id")) or "—"
        parts.extend(
            [
                f"<h2>{text(filename)} · {text(locator.get('rule_item_id') or '—')}</h2>",
                f"<p>原机器判定：{text(finding['status'])} · {text(finding['suggestions'])}</p>",
            ]
        )
        if finding["item_key"] not in results:
            continue
        result = results[finding["item_key"]]
        parts.append(f"<p><b>人工确认：{text(result['status'])}</b> · {text(result['suggestions'])}</p>")
        for answer in result["sources"]:
            location = location_label(answer["locator"])
            question = prompts.get((answer["file_version_id"], answer["field"])) or "—"
            parts.append(f"<p>依据：{text(originals.get(answer['file_version_id']) or '—')} · {text(location)} · {text(question)} · 回答：{text(_value(answer['value']))}</p>")
            parts.append(f"<p>回答人：{text(answerers.get(answer['answered_by']) or '—')}；时间：{text(answer['answered_at'])}</p>")
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
        # Properties carry the report name and type only, as the audit report's do.
        document.set_metadata({"title": TITLE, "subject": KIND, "author": "", "keywords": "", "creator": "", "producer": "", "creationDate": "", "modDate": "", "trapped": ""})
        document.del_xml_metadata()
        document.subset_fonts()
        return document.tobytes(garbage=4, deflate=True)
