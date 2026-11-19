"""Render a task's free data-standardization precheck: a cover and the ten chapters of the PPAP-00 outline."""

from html import escape

from documents.report_layout import Chapter, Table, chapter_title, dash, render_report, text

KIND = "PPAP 预检报告"
LEVEL = {"ok": "合规", "warn": "警告", "block": "阻断", "unmatched": "未匹配", "reference": "比对资料", "pending": "未执行"}
CONCLUSION = {"pass": "通过", "fail": "不通过", "not_run": "未执行"}
COVER_FIELDS = (("customer", "客户全称"), ("project", "项目"), ("model", "车型"), ("part_number", "零件号"), ("part", "零件名称"))
READER_FIELDS = ("供应商名称", "PPAP 版本", "SQE 工程师")
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
# Outline §11.10 chapter six: every status category is listed, with 本批无 when nothing falls into it;
# a status code outside this list is still registered, under its own code.
STATUS_CATEGORIES = (
    ("范围外", "S01"),
    ("规则不适配", "S02"),
    ("移交", "S03"),
    ("对端未到", "S04"),
    ("受控模板缺结构", "S05"),
    ("未完成全量统计", "S06"),
    ("比对源错配", "S07"),
    ("未执行", "未执行"),
)
# What an empty category reads; S05 has no controlled template on register to apply to.
EMPTY = {"S05": "本批无（当前无在册受控模板）"}
UNREVIEWED_NOTE = "S03、S04、S05、S06、S07 为该判而没判成的判定项，计入「未审条数」（S06 按未判条数计）；S01、S02 本就不该判，不计。"


def _rows(rows: list[tuple]) -> list[tuple[str, ...]]:
    return [tuple(text(cell) for cell in row) for row in rows]


def _card(item: dict) -> str:
    """Outline §11.6: a heading 「编号 ｜ 问题」 and the ten fields of §11.4; a field without data reads —."""
    fields = (
        ("位置", item["location"]),
        ("证据", item["evidence"]),
        ("判定依据", None),
        ("原因码", f"{item['code']} {item['title']}"),
        ("影响", None),
        ("整改建议", item["action"]),
        ("责任侧", item["responsibility"]),
        ("置信度", None),
        ("关联", None),
        ("出处", None),
    )
    return f"<h3>{text(item['number'])} ｜ {text(item['problem'])}</h3><div class='card'>" + "".join(f"<p><b>{label}：</b>{dash(value)}</p>" for label, value in fields) + "</div>"


def _cover(source: dict) -> str:
    recognition = source.get("recognition") or {}
    rows = [(label, dash(recognition.get(key))) for key, label in COVER_FIELDS]
    rows += [("检查日期", text(source["generated_at"][:10]))]
    rows += [(label, "") for label in READER_FIELDS]
    table = "".join(f"<tr><th>{escape(label)}</th><td>{value}</td></tr>" for label, value in rows)
    return (
        "<h1>PPAP 数据标准化预检报告</h1>" + (f"<p class='kind'>审核任务 {text(source['task_number'])}</p>" if source.get("task_number") else "<p class='kind'>　</p>") + f"<table>{table}</table>"
        "<p class='meta'>供应商名称、PPAP 版本与 SQE 工程师请下载后自行补充。</p>"
    )


def render_precheck_pdf(source: dict) -> bytes:
    """`source` holds the user-visible task_number (may be empty), generated_at, the recognized cover fields and the `precheck_task` result."""
    result = source["precheck"]
    blocks, warnings, statuses = result["blocks"], result["warnings"], result["statuses"]
    findings = blocks + warnings
    by_code: dict[str, list[str]] = {}
    for item in findings:
        by_code.setdefault(item["code"], []).append(item["number"])
    sides = sorted({item["responsibility"] for item in findings})
    conclusion = CONCLUSION[result["conclusion"]]
    worst = blocks[0]["problem"] if blocks else warnings[0]["problem"] if warnings else "未发现阻断或警告。"
    summary = [
        f"<p>结论：{conclusion}　能否进入正式审核：{'否' if result['conclusion'] == 'fail' else '是' if result['conclusion'] == 'pass' else '待识别完成'}</p>"
        f"<p>阻断 {len(blocks)} 项　警告 {len(warnings)} 项　未审 {result['unreviewed']} 条</p>"
        f"<p>原因码分布：{text('、'.join(f'{code}×{len(numbers)}' for code, numbers in sorted(by_code.items())) or '无')}</p>"
        f"<p>一句话判断：{text(worst)}</p><p>{SCOPE_NOTE}</p>",
        f"<p class='meta'>总纲版本：{text(result['outline_version'])}　生成时间：{text(source['generated_at'])}</p>",
    ]
    scope = [
        Table(
            "表 2-1 文件集",
            ("序号", "文件", "客户", "类型", "文件级结论"),
            _rows([(index, row["filename"], row["customer"] or "—", row["document_type"] or "—", LEVEL[row["level"]]) for index, row in enumerate(result["files"], 1)]),
            nowrap=(0, 4),
        ),
        "<p>范围判定：未声明提交范围，按部分提交推定。</p>",
    ]
    overview = [
        Table(
            "表 3-1 级别 × 责任侧",
            ("责任侧", "阻断", "警告", "合计"),
            _rows(
                [
                    (
                        side,
                        sum(1 for item in blocks if item["responsibility"] == side),
                        sum(1 for item in warnings if item["responsibility"] == side),
                        sum(1 for item in findings if item["responsibility"] == side),
                    )
                    for side in sides
                ]
            ),
        ),
        Table("表 3-2 原因码分布", ("原因码", "条数", "涉及问题号"), _rows([(code, len(numbers), "、".join(numbers)) for code, numbers in sorted(by_code.items())])),
    ]
    register_rows = []
    known = {code for _category, code in STATUS_CATEGORIES}
    others = tuple((code, code) for code in dict.fromkeys(row["code"] for row in statuses) if code not in known)
    for category, code in STATUS_CATEGORIES + others:
        rows = [row for row in statuses if row["code"] == code]
        register_rows += [(category, code, row["filename"], row["note"]) for row in rows] or [(category, code, "—", EMPTY.get(code, "本批无"))]
    remediation: list = [
        Table(f"7.{index} {side}", ("文件", "应改为", "关联问题号"), _rows([(item["filename"], item["action"], item["number"]) for item in findings if item["responsibility"] == side]))
        for index, side in enumerate(sides, 1)
    ]
    routes = "、".join(f"{row['filename']} → {row['entry_key']}" for row in result["files"] if row.get("entry_key"))
    chapters = [
        Chapter(chapter_title(1, "结论摘要"), summary),
        Chapter(chapter_title(2, "提交范围与文件集"), scope),
        Chapter(chapter_title(3, "问题总览"), overview),
        Chapter(chapter_title(4, "阻断项明细"), [_card(item) for item in blocks] or ["<p>无阻断项。</p>"]),
        Chapter(chapter_title(5, "警告项明细"), [f"<p>{WARN_NOTE}</p>", *([_card(item) for item in warnings] or ["<p>无警告项。</p>"])]),
        Chapter(
            chapter_title(6, "状态登记"), [f"<p>未审 {result['unreviewed']} 条。{UNREVIEWED_NOTE}</p>", Table("表 6-1 状态登记", ("类别", "状态", "条目", "说明"), _rows(register_rows), nowrap=(1,))]
        ),
        Chapter(chapter_title(7, "整改建议汇总"), remediation or ["<p>无需整改。</p>"]),
        Chapter(chapter_title(8, "结论作用域与局限性声明"), ["".join(f"<p>· {line}</p>" for line in LIMITS)]),
        Chapter(
            chapter_title(9, "附录 · 检查项全量结果"),
            [
                Table("表 9-1 文件级检查", ("文件", "可解析", "身份归属", "版本唯一", "规则覆盖", "识别字段"), _rows([_row_result(row, findings, statuses) for row in result["files"]])),
                *(block for row in result["files"] for block in _criteria(row)),
                "<p>结论为不通过时，材料侧问题须完成线下整改后重新提交，重新以无状态方式执行；本预检不包含整改后复检流程。</p>",
            ],
        ),
        Chapter(chapter_title(10, "附录 · 分则附加节"), [f"<p>路由结果：{text(routes or '无')}。</p>"]),
    ]
    return render_report(cover=_cover(source), chapters=chapters, name=f"{source['task_number']}-预检报告" if source.get("task_number") else "预检报告", kind=KIND, date=source["generated_at"][:10])


def _criteria(row: dict) -> list:
    """Every criterion of the routed rule entry, so what was not checked is visible, not counted as met."""
    if not row.get("entry_key"):
        return []
    criteria = row.get("criteria")
    if criteria is None:
        return [f"<p><b>{text(row['filename'])}</b>（{text(row['entry_key'])}）：原件未能读取，内容级未执行。</p>"]
    done = sum(1 for item in criteria if item["result"] != "未执行")
    rows = [(item["criterion_id"], item["criterion"], item["result"], item.get("code") or "", item.get("location") or "", item.get("reason") or item.get("evidence") or "") for item in criteria]
    caption = f"{row['filename']}（{row['entry_key']}，{len(criteria)} 项，已判定 {done} 项）"
    return [Table(caption, ("判定项", "内容", "结果", "原因码", "位置", "说明"), _rows(rows), nowrap=(0, 2, 3))]


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
    for code in ("S01", "S02"):
        if code in codes:
            return (row["filename"], "满足", "满足", "未执行", code, "未执行")
    return (row["filename"], "满足", "满足", "不满足（B02）" if "B02" in codes else "满足", "满足", "警告（W02）" if "W02" in codes else "满足")
