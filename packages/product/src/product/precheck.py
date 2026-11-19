"""Free data-standardization precheck of one audit task, read from the task as it stands.

It is independent of checker suites: it never runs or reads a checker and never
changes what is accepted for audit. It only states what the task already knows
for certain about each file (readable, attributable, one valid version, rule
coverage, identity fields, same-batch counterparts), coded as in the PPAP-00
general outline (B/W/S codes). Content-level results (``product.precheck_content``)
are merged in when the caller read them.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Callable, Mapping

from product.precheck_content import NO_TEXT, entry_key
from product.precheck_reading import IMAGE_SUFFIXES, read_document

OUTLINE_VERSION = "PPAP-00 v0.21"

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
# PPAP-00-A §4.1 同批关联图: (customer, target type) -> counterpart types (all `SUB`) whose part number pairs them.
# Only counterparts that recognition can type are listed; drawings, bubble charts, kappa records, deviation
# summaries and delivery checklists have no recognized type yet and are not inferred from a filename.
PEERS: dict[tuple[str, str], tuple[str, ...]] = {
    ("UAES", "T11"): ("T09", "T10", "E12"),
    ("UAES", "T10"): ("T12", "E12"),
    ("UAES", "T07"): ("E12", "T11"),
    ("UAES", "T08"): ("E12",),
    ("理想", "T10"): ("T09",),
    ("蔚来", "T11"): ("T09", "T12", "T10"),
    ("蔚来", "T10"): ("T09",),
    ("蔚来", "T09"): ("T12",),
}
PEER_NAMES = {"T09": "过程流程图", "T10": "PFMEA", "T11": "控制计划", "T12": "特殊特性清单", "E12": "采购件质量计划（PGQP1）"}
# A placeholder or a label is not a part number that can pair two files.
_NO_IDENTITY = {"", "/", "\\", "-", "--", "—", "——", "n/a", "na", "none", "null", "tbd", "xx", "xxx", "xxxx", "?", "无", "待填", "待定", "待确认", "未填写"}
_NO_IDENTITY |= {"零件号", "零件编号", "partnumber", "partno", "partno."}


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


def customer_types(customer: str | None) -> frozenset[str] | None:
    """M: the registered customer's type set; None when the customer is not registered or has no M configured."""
    from product.customers import resolve

    registered = resolve(customer)
    return frozenset(registered.type_set.types) if registered and registered.type_set and registered.type_set.types else None


def type_code(document_type: str | None, customer: str | None = None) -> str | None:
    """The PRD §1.5 dictionary code (T01, T01a…T17, E01…) of a recognized type for this customer; None when it has none.

    The same recognized type can be a different dictionary member per customer (蔚来's measurement report is T01a,
    理想's inspection report T01), so the customer's own entries decide.
    """
    text = (document_type or "").strip()
    if re.fullmatch(r"[TE]\d{2}a?", text):
        return text
    if not text:
        return None
    from audit.kinds import PpapDocumentType

    from product.customers import type_code as dictionary_code

    kind = PpapDocumentType.from_string(text)
    return dictionary_code(kind.get_type_name(), customer) if kind != PpapDocumentType.UNKNOWN else None


def _part(row: dict) -> str | None:
    """The recognized part number, compared literally after width and space folding; None when unreadable."""
    value = ((row.get("match") or {}).get("metadata") or {}).get("part_number")
    text = "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold().rstrip(":：")
    return None if text in _NO_IDENTITY else text


def named_by_user(row: dict) -> bool:
    """The user chose this file's rule by hand on P3 and it is the rule the file is matched to; recognition alone never is.

    A re-audit row inherits the previous Round's rule as its preferred rule; that rule is the first of its
    ``rule_options`` (原审核版本), so keeping it is no choice by hand — only another rule picked on P3 is.
    """
    preferred = row.get("preferred_rule_version_id")
    if not preferred or ((row.get("match") or {}).get("rule") or {}).get("id") != preferred:
        return False
    original = next(iter(row.get("rule_options") or []), None)
    return not (original and original.get("id") == preferred)


def image_files(files: list[dict], content: Mapping[str, list[dict]] | None, read: Callable[[dict], bytes] | None = None) -> list[dict]:
    """总纲 §5.2 「同批存在图像型文件」: picture files by their extension, and scans — files without a text layer,
    found by the content level or, for a PDF no rule entry read, by reading its text layer once. No OCR or model call."""
    found = []
    for row in files:
        name = row["file"]["original_filename"]
        suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        scanned = any(item.get("reason") == NO_TEXT for item in (content or {}).get(row["file"]["id"], []))
        if not scanned and suffix == ".pdf" and row["file"]["id"] not in (content or {}) and read is not None:
            try:
                document = read_document(read(row), name)
                scanned = not document.error and not document.has_text
            except Exception:  # noqa: BLE001 - an unreadable file is the file level's matter (B01/W05), not a scan
                scanned = False
        if suffix in IMAGE_SUFFIXES or scanned:
            found.append({"file_version_id": row["file"]["id"], "filename": name})
    return found


def _grids(content: bytes, filename: str) -> dict[str, list[list[str]]] | None:
    """Each worksheet's cell values by sheet name; None when the file is not a readable spreadsheet."""
    from product.precheck_reading import read_document

    doc = read_document(content, filename)
    return {grid.name: grid.rows for grid in doc.grids} if doc.kind == "xlsx" and not doc.error else None


def _cell_differences(first: dict[str, list[list[str]]], other: dict[str, list[list[str]]]) -> list[str]:
    from openpyxl.utils import get_column_letter

    found = []
    for sheet in dict.fromkeys([*first, *other]):
        if sheet not in first or sheet not in other:
            found.append(f"工作表「{sheet}」只在{'第 1 份' if sheet in first else '另一份'}中")
            continue
        a, b = first[sheet], other[sheet]
        for r in range(max(len(a), len(b))):
            left, right = a[r] if r < len(a) else [], b[r] if r < len(b) else []
            for c in range(max(len(left), len(right))):
                x, y = left[c] if c < len(left) else "", right[c] if c < len(right) else ""
                if x != y:
                    found.append(f"{sheet}!{get_column_letter(c + 1)}{r + 1}：{x or '(空)'} → {y or '(空)'}")
    return found


def _duplicate_evidence(rows: list[dict], read: Callable[[dict], bytes] | None) -> str:
    """附件 R-03: whether the copies agree only decides how the evidence reads, never the B02 itself."""
    count, name = len(rows), rows[0]["file"]["original_filename"]
    digests = [row["file"].get("sha256") for row in rows]
    if all(digests) and len(set(digests)) == 1:
        return f"{count} 份同名文件「{name}」逐字节相同"
    try:
        grids = [_grids(read(row), name) for row in rows] if read else None
    except Exception:  # noqa: BLE001 - an unreadable copy leaves the content uncompared, never guessed
        grids = None
    sheets = [grid for grid in grids or [] if grid is not None]
    if not grids or len(sheets) != len(grids):
        return f"{count} 份同名文件「{name}」字节不同，内容未逐格比对"
    differences = {index: _cell_differences(sheets[0], grid) for index, grid in enumerate(sheets[1:], 2)}
    if not any(differences.values()):
        return f"{count} 份同名文件「{name}」为另存／重存副本：各工作表单元格取值逐格相同"
    parts = [f"第 1 份与第 {index} 份共 {len(found)} 处不同：" + "；".join(found) for index, found in differences.items() if found]
    return f"{count} 份同名文件「{name}」为同一对象的{'两' if count == 2 else f' {count} '}个版本，" + "；".join(parts)


def _counterparts(targets: list[tuple[dict, str, str]], files: list[dict], codes: dict[str, str | None]) -> tuple[list[dict], list[dict]]:
    """S04 (+ its W01) for a counterpart absent from the batch; S07 for the one associated counterpart of another part.

    A counterpart that belongs to another submitted part is not associated with this file. An unreadable part
    number, an untyped file that could be the counterpart, or several candidates leave the comparison
    undecided: neither S04 nor S07.
    """
    statuses: list[dict] = []
    findings: list[dict] = []
    submitted = {part for row, _customer, _code in targets if (part := _part(row))}
    for row, customer, code in targets:
        name, mine = row["file"]["original_filename"], _part(row)
        others = [other for other in files if other["file"]["id"] != row["file"]["id"]]
        undetermined = any(codes[other["file"]["id"]] is None for other in others)
        for peer in PEERS.get((customer, code), ()):
            document = PEER_NAMES[peer]
            candidates = [other for other in others if codes[other["file"]["id"]] == peer]
            if mine and any(_part(other) == mine for other in candidates):
                continue
            # A counterpart of another submitted part pairs with that part's files, not with this one.
            associated = [other for other in candidates if _part(other) is None or _part(other) not in submitted - {mine}]
            if not associated and not undetermined:
                shown = row["match"]["metadata"].get("part_number") if mine else None
                part = f"（零件号 {shown}）" if shown else ""
                note = f"本批没有同零件的《{document}》{part}，与它的比对未完成。"
                missing = {"document": document, "document_type": peer, **({"part_number": shown} if shown else {})}
                statuses.append({"code": "S04", "scope": "content", "file_version_id": row["file"]["id"], "filename": name, "note": note, "missing": missing})
                findings.append(_finding("W01", "warn", row, f"缺少同零件的《{document}》{part}，跨文件比对未完成。", f"本批未见《{document}》", f"补交《{document}》{part}后重新预检。", "提交方"))
                continue
            if not mine or len(associated) != 1 or len(candidates) != 1 or _part(associated[0]) is None:
                continue
            peer_row = associated[0]
            found, value = peer_row["match"]["metadata"]["part_number"], row["match"]["metadata"]["part_number"]
            statuses.append(
                {
                    "code": "S07",
                    "scope": "content",
                    "file_version_id": row["file"]["id"],
                    "filename": name,
                    "note": f"比对源《{document}》零件号 {found} 与被审文件零件号 {value} 不一致，该项无法比对。",
                    "source_mismatch": {
                        "document": document,
                        "document_type": peer,
                        "identity": "零件号",
                        "value": value,
                        "found_value": found,
                        "found_file_version_id": peer_row["file"]["id"],
                        "found_filename": peer_row["file"]["original_filename"],
                    },
                }
            )
    return statuses, findings


# Criterion results the precheck is not meant to decide: out of scope, no adapted rule, handed to the formal audit.
UNDECIDED_BY_DESIGN = {"S01", "S02", "S03"}
# Status codes of a criterion that should have been decided and was not; S01 / S02 are not counted.
UNREVIEWED = {"S03", "S04", "S05", "S06", "S07"}


def _coverage(row: dict, entry: str, criteria: list[dict] | None) -> list[dict]:
    """S06 when the routed entry was not fully decided: the judged share of the criteria the precheck should decide.

    S06 is only 「该判而没判完」 (总纲 v0.21 §3.1): an original that could not be read is W05, not S06, so a
    file without content and criteria left open because the original was unreadable are not counted.
    """
    if criteria is None:
        return []
    else:
        due = [item for item in criteria if item["result"] not in UNDECIDED_BY_DESIGN and not item.get("unread")]
        open_ = [item for item in due if item["result"] not in {"满足", "不满足"}]
        total, judged = len(due), len(due) - len(open_)
        missed = "；".join(f"{item['criterion_id']}（{item.get('reason') or item['result']}）" for item in open_)
    if not total or judged == total:
        return []
    note = f"已判 {judged}/{total} 条判据，覆盖率 {judged * 100 // total}%；未判：{missed}。"
    return [{"code": "S06", "scope": "content", "file_version_id": row["file"]["id"], "filename": row["file"]["original_filename"], "note": note, "coverage": {"judged": judged, "total": total}}]


def unreviewed_count(statuses: list[dict], unread: int = 0) -> int:
    """未审条数: criteria that should have been decided and were not (S03/S04/S05/S07 one each, S06 its open criteria),
    plus ``unread`` criteria the precheck had no means to read (总纲 §3.1 硬约束 4 注「执行方能力不足」), which carry no S06."""
    return unread + sum(status["coverage"]["total"] - status["coverage"]["judged"] if status["code"] == "S06" else 1 for status in statuses if status["code"] in UNREVIEWED)


def precheck_task(task: dict[str, Any], content: Mapping[str, list[dict]] | None = None, read: Callable[[dict], bytes] | None = None) -> dict[str, Any]:
    """Return the precheck of a task: findings, statuses, per-file level and batch conclusion.

    ``content`` maps a file version id to its ``precheck_content`` results; their 不满足 items become findings
    and the full list is attached to the file row. Files whose (customer, type) has no 00-A entry get a
    content-level status that does not change the file level: S01 when the type is outside the customer's M,
    else S02.

    Routing: a type outside the customer's type set M is S01 (out of scope), also when a counterpart-only
    type is submitted on its own; only a type inside M without an adapted effective rule is S02. A missing
    same-batch counterpart is S04 with its W01; an associated counterpart of another part is S07, which
    carries the facts in ``source_mismatch`` and never blocks the file. A criterion handed to the formal audit
    is S03; a routed entry not fully decided is S06 with its ``coverage``. ``unreviewed`` counts what should
    have been decided and was not (S03–S07; S01/S02 never). S05 has no applicable entry in this period
    (00-A v1.13 §0 纪律 4: a missing key column is B05), so it is never stated. Criteria the original could not
    be read for (``unread``) are no S06 but count in ``unreviewed``.

    Several versions (附件 R-03, 用户裁定): two or more submitted files of one recognized part with the very same
    file name are each B02. ``read`` (a task file row -> its bytes) lets the evidence say whether they are
    byte-identical, saved copies with equal cell values, or two versions with their differing cells.

    ``image_files`` lists the batch's image-type files (总纲 §5.2 整批级 W01). It is no finding: it counts in no
    warning, changes no file level and not the conclusion; the report states it and asks for a text version.
    """
    files = [row for row in task.get("files", []) if row.get("selection") == "selected"]
    findings: list[dict] = []
    statuses: list[dict] = []
    pending: list[str] = []
    entries: dict[str, str | None] = {}
    targets: list[tuple[dict, str, str]] = []

    def customer_of(row: dict) -> str | None:
        return ((row.get("match") or {}).get("metadata") or {}).get("customer") or task.get("customer_preference")

    codes = {row["file"]["id"]: type_code((row.get("match") or {}).get("document_type"), customer_of(row)) for row in files}
    for row in files:
        name, validation, match = row["file"]["original_filename"], row.get("validation") or {}, row.get("match") or {}
        customer = customer_of(row)
        scope, code = customer_types(customer), codes[row["file"]["id"]]
        outside = scope is not None and code is not None and code not in scope
        reasons = "；".join(str(reason) for reason in validation.get("reasons") or [])
        if validation.get("state") == "invalid":
            findings.append(_finding("B01", "block", row, "文件无法读取或格式不受支持，不能进入审核。", reasons, "解除密码或修复文件后重新上传；只接受 Excel / PDF / Word / 图片格式。", "材料侧"))
            continue
        if validation.get("state") in {"pending", "checking", "error"} or match.get("state") in {None, "pending", "matching"}:
            pending.append(row["file"]["id"])
            statuses.append({"code": "未执行", "file_version_id": row["file"]["id"], "filename": name, "note": "文件检查或识别尚未完成，本次未预检。"})
            continue
        if row.get("reference_only"):
            continue
        if match.get("state") == "reference":
            # A counterpart-only type submitted on its own (such as PGQP1) is outside the audit scope.
            if outside:
                statuses.append({"code": "S01", "file_version_id": row["file"]["id"], "filename": name, "note": "该文件类型不在本客户的审核范围内，仅可作为其他文件的比对文件；不单独审核，不计费。"})
            continue
        gaps = list(match.get("gaps") or [])
        identity = next((gap for gap in gaps if gap in IDENTITY_GAPS), None)
        if identity:
            problem, action, side = IDENTITY_GAPS[identity]
            findings.append(_finding("B03", "block", row, problem + "，无法确定归属。", problem, action, side))
            continue
        coverage = next((gap for gap in gaps if gap in COVERAGE_GAPS), None)
        if match.get("state") in {"unmatched", "failed"} or coverage:
            if outside:
                statuses.append({"code": "S01", "file_version_id": row["file"]["id"], "filename": name, "note": "该文件类型不在本客户的审核范围内；该文件不进入本次审核，不计费。"})
                continue
            note = (COVERAGE_GAPS[coverage] if coverage else "未匹配到生效规则") + "；该文件不进入本次审核，不计费。"
            statuses.append({"code": "S02", "file_version_id": row["file"]["id"], "filename": name, "note": note})
            continue
        for gap in gaps:
            if gap in READ_GAPS:
                problem = READ_GAPS[gap] + "，正式审核可能对相关条款给出“无法判断”。"
                action = "如为扫描件，请提供可编辑的原始文件（Excel / Word）或文字版 PDF。"
                findings.append(_finding("W05", "warn", row, problem, READ_GAPS[gap], action, "解析侧"))
        metadata = match.get("metadata") or {}
        entries[row["file"]["id"]] = entry = entry_key(metadata.get("customer") or task.get("customer_preference"), match.get("document_type"))
        if customer and code:
            targets.append((row, customer, code))
        if entry is None and outside:
            note = "该文件类型不在本客户的预检范围内，内容级不预检；不影响正式审核。"
            statuses.append({"code": "S01", "scope": "content", "file_version_id": row["file"]["id"], "filename": name, "note": note})
        elif entry is None:
            note = "预检规则未覆盖该客户与文件类型（PPAP-00-A 路由表无此条目），内容级不预检；不影响正式审核。"
            statuses.append({"code": "S02", "scope": "content", "file_version_id": row["file"]["id"], "filename": name, "note": note})
        for item in (content or {}).get(row["file"]["id"], []) if entry else []:
            if item["result"] == "不满足" and item.get("code") in CODES:
                code, problem = item["code"], f"{item['criterion_id']} {item['criterion']}：{item['reason']}".rstrip("：")
                action = CONTENT_ACTIONS.get(code, CONTENT_ACTIONS["B"] if code.startswith("B") else "核对后修正。")
                findings.append(_finding(code, "block" if code.startswith("B") else "warn", row, problem, item["evidence"], action, "材料侧", item["location"]))
            elif item["result"] == "S03":
                note = f"{item['criterion_id']} {item['criterion']}：{item['reason']}".rstrip("：")
                statuses.append({"code": "S03", "scope": "content", "file_version_id": row["file"]["id"], "filename": name, "note": note})
        if entry and content is not None:
            statuses.extend(_coverage(row, entry, content.get(row["file"]["id"])))
        for field, label in (("part_number", "零件号"), ("part", "零件名称")):
            if not (metadata.get(field) or "").strip():
                findings.append(_finding("W02", "warn", row, f"文件中未识别到{label}。", "(空)", f"在文件表头填写{label}，与图纸和其他 PPAP 文件保持一致。", "材料侧"))
    if not pending:
        peer_statuses, peer_findings = _counterparts(targets, files, codes)
        statuses.extend(peer_statuses)
        findings.extend(peer_findings)
    # 附件 R-03: one part, the very same file name, two or more copies → every copy is B02; no copy is the original.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    replaced = {row.get("replacement_of_file_version_id") for row in files} - {None}
    for row in files:
        if (row.get("match") or {}).get("state") == "matched" and row["file"]["id"] not in replaced and (part := _part(row)):
            groups[(part, row["file"]["original_filename"])].append(row)
    for rows in groups.values():
        if len(rows) > 1:
            evidence = _duplicate_evidence(rows, read)
            problem = f"同一零件提交了 {len(rows)} 份同名文件，无法确定以哪一份为准。"
            for row in rows:
                findings.append(_finding("B02", "block", row, problem, evidence, "只保留一份有效文件，移除其余同名文件后再开始审核。", "提交方"))
    by_file: dict[str, str] = {}
    for item in findings:
        current = by_file.get(item["file_version_id"])
        by_file[item["file_version_id"]] = "block" if "block" in {current, item["level"]} else "warn"
    references = {row["file"]["id"] for row in files if (row.get("match") or {}).get("state") == "reference"}
    for status in statuses:
        if status.get("scope") == "content" or status["file_version_id"] in references:
            continue
        by_file.setdefault(status["file_version_id"], "unmatched" if status["code"] in {"S01", "S02"} else "pending")
    blocks = [item for item in findings if item["level"] == "block"]
    warnings = [item for item in findings if item["level"] == "warn"]
    for index, item in enumerate(blocks, 1):
        item["number"] = f"B-{index:02d}"
    for index, item in enumerate(warnings, 1):
        item["number"] = f"W-{index:02d}"
    unread = sum(1 for file_version_id, entry in entries.items() if entry for item in (content or {}).get(file_version_id, []) if item.get("unread") and item["result"] not in UNDECIDED_BY_DESIGN)
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
        "unreviewed": unreviewed_count(statuses, unread),
        "image_files": image_files(files, content, read),
        "counts": {level: counts.get(level, 0) for level in ("ok", "warn", "block", "unmatched", "reference", "pending")},
    }
