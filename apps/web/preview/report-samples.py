"""Build explicitly marked PDF fixtures, not a production report renderer.

Run with a Python runtime containing reportlab. The same JSON samples drive the
online fixture and these fixed PDFs. No real files or APIs.
"""

import argparse
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

parser = argparse.ArgumentParser()
parser.add_argument("--font", required=True, help="Path to a locally licensed CJK TrueType font; subset is embedded in the PDF")
args = parser.parse_args()
pdfmetrics.registerFont(TTFont("SampleCJK", args.font))
body = ParagraphStyle("body", fontName="SampleCJK", fontSize=11, leading=18, spaceAfter=10, wordWrap="CJK")
heading = ParagraphStyle("heading", parent=body, fontSize=16, leading=24, spaceBefore=12)
note = ParagraphStyle("note", parent=body, fontSize=9, textColor=colors.HexColor("#52525b"))


def build(document: dict, generated_at: str):
    source = document["name"]
    target = Path(__file__).parent / "reports" / f"{document['asset']}.pdf"
    target.parent.mkdir(exist_ok=True)
    story = [Paragraph("交互预览 - 示例报告，不是真实审核结果", note), Paragraph(f"{source}-审核报告", heading)]
    story.extend(
        Paragraph(text, body)
        for text in [f"文件：{source}", f"冻结规则：{document['rule']}", f"原生成时间：{generated_at.replace('T', ' ')}", "示例客户 / 示例车型 / P-204", f"文件审核结论：{document['conclusion']}"]
    )
    story += [HRFlowable(width="100%", color=colors.HexColor("#d4d4d8")), Spacer(1, 12), Paragraph("逐项审核与来源证据", heading)]
    for check in document["checks"]:
        story += [
            Paragraph(f"{check['id']} {check['category']} - {check['status']}", heading),
            Paragraph(check["requirement"], body),
            Paragraph(check["observation"], body),
            Paragraph(f"来源：{check['source']} / 审核示例 / {check['cell']}", note),
        ]
    story += [Paragraph("审核总结", heading), Paragraph(document["summary"], body)]
    for suggestion in document["corrective_actions"]:
        story += [Paragraph(f"核心整改建议：{suggestion}", body)]
    story += [Spacer(1, 12), Paragraph("本文件仅为 UI 测试样例，不是审核或量产批准凭证。", note)]
    SimpleDocTemplate(str(target), pagesize=A4, rightMargin=42, leftMargin=42, topMargin=36, bottomMargin=36, title=f"{source}-审核报告", author="PPAP UI fixture").build(story)


samples = json.loads(Path(__file__).with_suffix(".json").read_text())
for document in samples["documents"]:
    build(document, samples["generated_at"])
