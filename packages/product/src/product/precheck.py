"""Free data-standardization precheck of one audit task, read from the task as it stands.

It is independent of checker suites: it never runs or reads a checker and never
changes what is accepted for audit. It only states what the task already knows
for certain about each file (readable, attributable, one valid version, rule
coverage, identity fields), coded as in the PPAP-00 general outline (B/W/S codes).
Content-level results (``product.precheck_content``) are merged in when the caller read them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Mapping

from product.precheck_content import entry_key

OUTLINE_VERSION = "PPAP-00 v0.14"

CODES = {
    "B01": "文件无法解析",
    "B02": "多版本并存且无唯一有效件",
    "B03": "对象身份不可归属",
    "B04": "结构不可判",
    "B05": "关键字段结构上不存在",
    "B06": "取值冲突或派生值不自洽",
    "W01": "缺项",
    "W02": "字段为空或未识别",
    "W03": "写法不规范",
    "W04": "非主键不一致",
    "W05": "局部不可解析",
    "W06": "文件内部不自洽",
    "S02": "无规则覆盖",
}
CONTENT_ACTIONS = {
    "B": "按客户规则补齐缺失的列、字段或区块后重新上传。",
    "W01": "补齐缺少的区块或内容。",
    "W02": "补填该字段；确不适用时按规范写明。",
    "W03": "按规范统一写法或取值。",
    "W05": "修复无法显示的单元格或提供可读的原始文件。",
}
IDENTITY_GAPS = {
    "matching_customer_required": ("未识别出客户", "返回上传页选择客户后重新识别。", "提交方"),
    "matching_customer_conflict": ("文件内的客户标识与本次审核客户不一致", "核对审核客户；不同客户的文件分别建立任务。", "提交方"),
    "matching_document_type_required": ("未能确定文件类型", "核对文件是否为 PPAP 文件；必要时按客户模板重新整理后上传。", "材料侧"),
    "document_classification_failed": ("未能确定文件类型", "核对文件是否为 PPAP 文件；必要时按客户模板重新整理后上传。", "材料侧"),
}
COVERAGE_GAPS = {
    "matching_document_type_rules_missing": "没有此类文件的生效规则",
    "matching_customer_rules_missing": "没有适用于该客户和文件类型的生效规则",
    "no_available_rules": "暂无可用规则",
    "matching_rules_customer_unassigned": "规则的客户范围尚未确认",
    "ambiguous_available_rules": "有多份生效规则符合条件，未自动选择",
}
READ_GAPS = {
    "empty_document_text": "未读取到文件文字",
    "document_text_unavailable": "无法读取文件文字",
    "metadata_extraction_failed": "业务信息识别失败",
}
# Version tokens that differ between drafts of one document: V1.2, v3, 20261108, 23.12.18, (1), 副本, 最新.
_VERSION = re.compile(r"(?i)(?:[-_\s]*(?:v|rev|版本)\s*\d+(?:\.\d+)*)|(?:[-_\s]*\d{8})|(?:[-_\s]*\d{2}\.\d{1,2}\.\d{1,2})|(?:\s*\(\d+\))|(?:[-_\s]*(?:副本|最新|终版|final))")


def _stem(name: str) -> str:
    base = name.rsplit(".", 1)[0] if "." in name else name
    return re.sub(r"[-_\s]+", "-", _VERSION.sub("", base)).strip("-").lower()


def _finding(code: str, level: str, file: dict, problem: str, evidence: str, action: str, side: str, location: str = "") -> dict:
    name = file["file"]["original_filename"]
    return {
        "code": code,
        "title": CODES[code],
        "level": level,
        "file_version_id": file["file"]["id"],
        "filename": name,
        "location": f"{name} › {location}" if location else name,
        "evidence": evidence or "(空)",
        "problem": problem,
        "action": action,
        "responsibility": side,
        # Content-level findings come from the routed rule entry; file-level ones from the task itself.
        "scope": "content" if location else "file",
    }


def precheck_task(task: dict[str, Any], content: Mapping[str, list[dict]] | None = None) -> dict[str, Any]:
    """Return the precheck of a task: findings, statuses, per-file level and batch conclusion.

    ``content`` maps a file version id to its ``precheck_content`` results; their 不满足 items become findings
    and the full list is attached to the file row. Files whose (customer, type) has no 00-A entry get a
    content-level S02 status that does not change the file level.
    """
    files = [row for row in task.get("files", []) if row.get("selection") == "selected"]
    findings: list[dict] = []
    statuses: list[dict] = []
    pending: list[str] = []
    entries: dict[str, str | None] = {}
    for row in files:
        name, validation, match = row["file"]["original_filename"], row.get("validation") or {}, row.get("match") or {}
        reasons = "；".join(str(reason) for reason in validation.get("reasons") or [])
        if validation.get("state") == "invalid":
            findings.append(_finding("B01", "block", row, "文件无法读取或格式不受支持，不能进入审核。", reasons, "解除密码或修复文件后重新上传；只接受 Excel / PDF / Word / 图片格式。", "材料侧"))
            continue
        if validation.get("state") in {"pending", "checking", "error"} or match.get("state") in {None, "pending", "matching"}:
            pending.append(row["file"]["id"])
            statuses.append({"code": "未执行", "file_version_id": row["file"]["id"], "filename": name, "note": "文件检查或识别尚未完成，本次未预检。"})
            continue
        if row.get("reference_only") or match.get("state") == "reference":
            continue
        gaps = list(match.get("gaps") or [])
        identity = next((gap for gap in gaps if gap in IDENTITY_GAPS), None)
        if identity:
            problem, action, side = IDENTITY_GAPS[identity]
            findings.append(_finding("B03", "block", row, problem + "，无法确定归属。", problem, action, side))
            continue
        coverage = next((gap for gap in gaps if gap in COVERAGE_GAPS), None)
        if match.get("state") in {"unmatched", "failed"} or coverage:
            note = (COVERAGE_GAPS[coverage] if coverage else "未匹配到生效规则") + "；该文件不进入本次审核，不计费。"
            statuses.append({"code": "S02", "file_version_id": row["file"]["id"], "filename": name, "note": note})
            continue
        for gap in gaps:
            if gap in READ_GAPS:
                problem = READ_GAPS[gap] + "，正式审核可能对相关条款给出“无法判断”。"
                action = "如为扫描件，请提供可编辑的原始文件（Excel / Word）或文字版 PDF。"
                findings.append(_finding("W05", "warn", row, problem, READ_GAPS[gap], action, "解析侧"))
        metadata = match.get("metadata") or {}
        entries[row["file"]["id"]] = entry = entry_key(metadata.get("customer") or task.get("customer"), match.get("document_type"))
        if entry is None:
            note = "预检规则未覆盖该客户与文件类型（PPAP-00-A 路由表无此条目），内容级不预检；不影响正式审核。"
            statuses.append({"code": "S02", "scope": "content", "file_version_id": row["file"]["id"], "filename": name, "note": note})
        for item in (content or {}).get(row["file"]["id"], []) if entry else []:
            if item["result"] == "不满足" and item.get("code") in CODES:
                code, problem = item["code"], f"{item['criterion_id']} {item['criterion']}：{item['reason']}".rstrip("：")
                action = CONTENT_ACTIONS.get(code, CONTENT_ACTIONS["B"] if code.startswith("B") else "核对后修正。")
                findings.append(_finding(code, "block" if code.startswith("B") else "warn", row, problem, item["evidence"], action, "材料侧", item["location"]))
        for field, label in (("part_number", "零件号"), ("part", "零件名称")):
            if not (metadata.get(field) or "").strip():
                findings.append(_finding("W02", "warn", row, f"文件中未识别到{label}。", "(空)", f"在文件表头填写{label}，与图纸和其他 PPAP 文件保持一致。", "材料侧"))
    # Several drafts of one document in one batch leave no single valid version.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    replaced = {row.get("replacement_of_file_version_id") for row in files} - {None}
    for row in files:
        match = row.get("match") or {}
        if match.get("state") == "matched" and row["file"]["id"] not in replaced:
            groups[(match.get("document_type"), _stem(row["file"]["original_filename"]))].append(row)
    for rows in groups.values():
        if len(rows) > 1:
            names = "、".join(row["file"]["original_filename"] for row in rows)
            for row in rows:
                findings.append(_finding("B02", "block", row, "同一文件的多个版本同时提交，无法确定以哪一份为准。", names, "只保留最新有效版本，移除其余版本后再开始审核。", "提交方"))
    by_file: dict[str, str] = {}
    for item in findings:
        current = by_file.get(item["file_version_id"])
        by_file[item["file_version_id"]] = "block" if "block" in {current, item["level"]} else "warn"
    for status in statuses:
        if status.get("scope") == "content":
            continue
        by_file.setdefault(status["file_version_id"], "unmatched" if status["code"] == "S02" else "pending")
    blocks = [item for item in findings if item["level"] == "block"]
    warnings = [item for item in findings if item["level"] == "warn"]
    for index, item in enumerate(blocks, 1):
        item["number"] = f"B-{index:02d}"
    for index, item in enumerate(warnings, 1):
        item["number"] = f"W-{index:02d}"
    conclusion = "not_run" if not files or pending else "fail" if blocks else "pass"
    rows = [
        {
            "file_version_id": row["file"]["id"],
            "filename": row["file"]["original_filename"],
            "level": by_file.get(row["file"]["id"], "reference" if row.get("reference_only") or (row.get("match") or {}).get("state") == "reference" else "ok"),
            "document_type": (row.get("match") or {}).get("document_type"),
            "customer": ((row.get("match") or {}).get("metadata") or {}).get("customer"),
            "entry_key": entries.get(row["file"]["id"]),
            **({"criteria": (content or {})[row["file"]["id"]]} if entries.get(row["file"]["id"]) and row["file"]["id"] in (content or {}) else {}),
        }
        for row in files
    ]
    counts = Counter(row["level"] for row in rows)
    return {
        "outline_version": OUTLINE_VERSION,
        "conclusion": conclusion,
        "files": rows,
        "blocks": blocks,
        "warnings": warnings,
        "statuses": statuses,
        "counts": {level: counts.get(level, 0) for level in ("ok", "warn", "block", "unmatched", "reference", "pending")},
    }
