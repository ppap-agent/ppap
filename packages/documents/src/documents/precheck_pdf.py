"""Render a task's free data-standardization precheck in the ten-chapter template of the PPAP-00 outline."""

from html import escape
from io import BytesIO

import pymupdf

CSS = (
    "body {font-family:sans-serif; font-size:10pt; line-height:1.6} h1 {font-size:18pt} h2 {font-size:13pt; margin-top:14pt} "
    "p {white-space:pre-wrap} .meta {font-size:9pt; color:#555} .card {border:0.5pt solid #bbb; padding:4pt 8pt; margin:6pt 0} "
    "table {border-collapse:collapse; width:100%} th, td {border:0.5pt solid #888; padding:2pt 4pt; font-size:9pt; text-align:left; vertical-align:top} th {background-color:#eee}"
)
LEVEL = {"ok": "合规", "warn": "警告", "block": "阻断", "unmatched": "未匹配", "reference": "比对资料", "pending": "未执行"}
CONCLUSION = {"pass": "通过", "fail": "不通过", "not_run": "未执行"}
SCOPE_NOTE = "本预检只判定「本批能否被可靠审核」，不判定「文件是否合规」，不执行修复与复检。「通过」只表示「能审」，不表示「审过了没问题」。"
WARN_NOTE = "下列警告不拦截放行，但 100% 随本报告移交正式审核。正式审核不得因「预检已通过」而跳过下列任何一条指向的字段。警告是移交清单，不是免检证明。"
LIMITS = (
    "本预检不判定文件是否符合客户要求、IATF 16949 或 AIAG PPAP 标准。",
    "本预检无法区分「真缺项」与「本批次未提交」，缺项类结论只能表述为「本批次未识别到」。",
    "未声明提交范围时按部分提交推定，相关缺失类判定不构成缺件阻断。",
    "警告项不拦截放行，但已全部列入第五章并随本报告移交正式审核。",
    "正式审核不得因「预检已通过」而跳过警告项指向的字段。警告是移交清单，不是免检证明。",
    "本预检不执行修复、补全、格式转换与整改后复检。",
    "内容级按 PPAP-00-A 路由到的分则逐条列报：本期未实现或需要图像、跨文件比对的判定项标「未执行」并写明原因，不按满足计。",
)


def _t(value) -> str:
    return escape(str(value if value is not None else ""))


def _table(head: tuple[str, ...], rows: list[tuple]) -> str:
    if not rows:
        return "<p>无。</p>"
    return "<table><tr>" + "".join(f"<th>{_t(cell)}</th>" for cell in head) + "</tr>" + "".join("<tr>" + "".join(f"<td>{_t(cell)}</td>" for cell in row) + "</tr>" for row in rows) + "</table>"


def _card(item: dict) -> str:
    fields = (
        ("问题号", item["number"]),
        ("级别", "阻断" if item["level"] == "block" else "警告"),
        ("原因码", f"{item['code']} {item['title']}"),
        ("文件", item["filename"]),
        ("位置", item["location"]),
        ("证据", item["evidence"]),
        ("问题", item["problem"]),
        ("整改动作", item["action"]),
        ("责任侧", item["responsibility"]),
    )
    return '<div class="card">' + "".join(f"<p><b>{label}：</b>{_t(value)}</p>" for label, value in fields) + "</div>"


def render_precheck_pdf(source: dict) -> bytes:
    """`source` holds task_number, generated_at and the `precheck_task` result."""
    result = source["precheck"]
    blocks, warnings, statuses = result["blocks"], result["warnings"], result["statuses"]
    findings = blocks + warnings
    by_code: dict[str, list[str]] = {}
    for item in findings:
        by_code.setdefault(item["code"], []).append(item["number"])
    sides = sorted({item["responsibility"] for item in findings})
    conclusion = CONCLUSION[result["conclusion"]]
    worst = blocks[0]["problem"] if blocks else warnings[0]["problem"] if warnings else "未发现阻断或警告。"
    parts = [
        f"<h1>PPAP 数据标准化预检报告</h1><p class='meta'>审核任务：{_t(source['task_number'])}　生成时间：{_t(source['generated_at'])}　总纲版本：{_t(result['outline_version'])}</p>",
        "<h2>一、结论摘要</h2>",
        f"<p>结论：{conclusion}　能否进入正式审核：{'否' if result['conclusion'] == 'fail' else '是' if result['conclusion'] == 'pass' else '待识别完成'}</p>",
        f"<p>阻断 {len(blocks)} 项　警告 {len(warnings)} 项　状态项 {len(statuses)} 项</p>",
        f"<p>原因码分布：{_t('、'.join(f'{code}×{len(numbers)}' for code, numbers in sorted(by_code.items())) or '无')}</p>",
        f"<p>一句话判断：{_t(worst)}</p><p>{SCOPE_NOTE}</p>",
        "<h2>二、提交范围与文件集</h2>",
        _table(
            ("序号", "文件", "客户", "类型", "文件级结论"),
            [(index, row["filename"], row["customer"] or "—", row["document_type"] or "—", LEVEL[row["level"]]) for index, row in enumerate(result["files"], 1)],
        ),
        "<p>范围判定：未声明提交范围，按部分提交推定。</p>",
        "<h2>三、问题总览</h2>",
        _table(
            ("责任侧", "阻断", "警告", "合计"),
            [
                (
                    side,
                    sum(1 for item in blocks if item["responsibility"] == side),
                    sum(1 for item in warnings if item["responsibility"] == side),
                    sum(1 for item in findings if item["responsibility"] == side),
                )
                for side in sides
            ],
        ),
        _table(("原因码", "条数", "涉及问题号"), [(code, len(numbers), "、".join(numbers)) for code, numbers in sorted(by_code.items())]),
        "<h2>四、阻断项明细</h2>",
        "".join(_card(item) for item in blocks) or "<p>无阻断项。</p>",
        f"<h2>五、警告项明细（不影响结论，须全部移交正式审核）</h2><p>{WARN_NOTE}</p>",
        "".join(_card(item) for item in warnings) or "<p>无警告项。</p>",
        "<h2>六、状态登记</h2>",
        _table(("文件", "状态", "说明"), [(row["filename"], row["code"], row["note"]) for row in statuses]),
        "<h2>七、整改建议汇总</h2>",
    ]
    for side in sides:
        rows = [(item["filename"], item["action"], item["number"]) for item in findings if item["responsibility"] == side]
        parts += [f"<p><b>{_t(side)}</b></p>", _table(("文件", "应改为", "关联问题号"), rows)]
    if not sides:
        parts.append("<p>无需整改。</p>")
    parts += [
        "<h2>八、结论作用域与局限性声明</h2>",
        "".join(f"<p>· {line}</p>" for line in LIMITS),
        "<h2>九、附录·检查项全量结果</h2>",
        _table(("文件", "可解析", "身份归属", "版本唯一", "规则覆盖", "识别字段"), [_row_result(row, findings, statuses) for row in result["files"]]),
        "".join(_criteria(row) for row in result["files"]),
        "<p>结论为不通过时，材料侧问题须完成线下整改后重新提交，重新以无状态方式执行；本预检不包含整改后复检流程。</p>",
        "<h2>十、附录·分则附加节</h2><p>路由结果：" + _t("、".join(f"{row['filename']} → {row['entry_key']}" for row in result["files"] if row.get("entry_key")) or "无") + "。</p>",
    ]
    story = pymupdf.Story(html='<html lang="zh"><body>' + "".join(parts) + "</body></html>", user_css=CSS)
    output = BytesIO()
    with pymupdf.DocumentWriter(output) as writer:

        def page_rect(number, filled):
            if number >= 200:
                raise ValueError("report_page_limit")
            return pymupdf.Rect(0, 0, 595, 842), pymupdf.Rect(42, 42, 553, 786), None

        story.write(writer, page_rect)
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document:
        document.set_metadata({"title": "PPAP 数据标准化预检报告", "subject": f"task={source['task_number']}"})
        document.subset_fonts()
        return document.tobytes(garbage=4, deflate=True)


def _criteria(row: dict) -> str:
    """Every criterion of the routed rule entry, so what was not checked is visible, not counted as met."""
    if not row.get("entry_key"):
        return ""
    criteria = row.get("criteria")
    if criteria is None:
        return f"<p><b>{_t(row['filename'])}</b>（{_t(row['entry_key'])}）：原件未能读取，内容级未执行。</p>"
    done = sum(1 for item in criteria if item["result"] != "未执行")
    head = f"<p><b>{_t(row['filename'])}</b>（{_t(row['entry_key'])}，{len(criteria)} 项，已判定 {done} 项）</p>"
    rows = [(item["criterion_id"], item["criterion"], item["result"], item.get("code") or "", item.get("location") or "", item.get("reason") or item.get("evidence") or "") for item in criteria]
    return head + _table(("判定项", "内容", "结果", "原因码", "位置", "说明"), rows)


def _row_result(row: dict, findings: list[dict], statuses: list[dict]) -> tuple:
    """One row per file; a check after a stopping failure is not executed (outline §6)."""
    file_level = [item for item in statuses if item.get("scope") != "content"]
    codes = {item["code"] for item in findings + file_level if item["file_version_id"] == row["file_version_id"]}
    if "未执行" in codes:
        return (row["filename"], *["未执行"] * 5)
    if "B01" in codes:
        return (row["filename"], "不满足（B01）", *["未执行"] * 4)
    if "B03" in codes:
        return (row["filename"], "满足", "不满足（B03）", *["未执行"] * 3)
    if "S02" in codes:
        return (row["filename"], "满足", "满足", "未执行", "S02", "未执行")
    return (row["filename"], "满足", "满足", "不满足（B02）" if "B02" in codes else "满足", "满足", "警告（W02）" if "W02" in codes else "满足")
