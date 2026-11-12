"""Write common round findings back into the source Feishu doc. Not a second ledger."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import cast

from audit.audits import ERR, EXEC_COMPLETED, FINDING_STATUSES, NA, NG, PASS, TARGET, UNABLE
from audit.models import public_locator
from audit.rule_coverage import REASON_MANUAL_ONLY, REASON_UNSUPPORTED, unsupported_item_note

from product.feishu_drive import OPEN_API


class FeishuWritebackError(Exception):
    pass


def _report_error(response, stage: str) -> FeishuWritebackError:
    # Preserve only a public numeric code, never provider messages or credentials.
    status = int(getattr(response, "status_code", 0) or 0)
    code = _response_json(response).get("code")
    detail = str(code) if type(code) is int and code != 0 else "http_" + str(status)
    # Feishu can return missing app/user permission as HTTP 400, before creation.
    outcome = "rejected" if status in (401, 403, 422, 429) or code in (99991672, 99991679) else "unknown"
    return FeishuWritebackError(f"feishu_report_{stage}_{outcome}:{detail}")


def create_report_document(http, access_token: str, title: str) -> str:
    """One external attempt. Caller must persist its claim before this call."""
    try:
        response = http.post(
            OPEN_API + "/docx/v1/documents",
            headers={"Authorization": "Bearer " + access_token},
            json={"title": title[:200]},
            timeout=20,
        )
    except Exception as exc:
        raise FeishuWritebackError("feishu_report_create_unknown") from exc
    payload = _response_json(response)
    if not _ok(response) or payload.get("code") != 0:
        raise _report_error(response, "create")
    document_id = ((payload.get("data") or {}).get("document") or {}).get("document_id")
    if not isinstance(document_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
        raise FeishuWritebackError("feishu_report_create_unknown")
    return document_id


def append_report_blocks(http, access_token: str, document_id: str, blocks: list[dict], operation_id: str) -> None:
    """Write in ordered batches; never auto-retry an ambiguous append."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
        raise FeishuWritebackError("invalid_file_token")
    for offset in range(0, len(blocks), 50):
        client_token = str(uuid.uuid5(uuid.NAMESPACE_URL, operation_id + ":" + str(offset)))
        try:
            response = http.post(
                docx_children_url(document_id),
                params={"client_token": client_token},
                headers={"Authorization": "Bearer " + access_token},
                json={"index": -1, "children": blocks[offset : offset + 50]},
                timeout=20,
            )
        except Exception as exc:
            raise FeishuWritebackError("feishu_report_write_unknown") from exc
        if not _ok(response) or _response_json(response).get("code") != 0:
            raise _report_error(response, "write")


def docx_children_url(document_id: str) -> str:
    return OPEN_API + "/docx/v1/documents/" + document_id + "/blocks/" + document_id + "/children"


def drive_comments_url(file_token: str) -> str:
    return OPEN_API + "/drive/v1/files/" + file_token + "/comments?file_type=docx"


def round_return_url(web_url: str, round_id: str) -> str:
    base = (web_url or "").rstrip("/")
    return base + "/?round=" + round_id


def _finding_version(finding: dict) -> str:
    locator_value = finding.get("locator")
    locator = locator_value if isinstance(locator_value, dict) else {}
    return str(locator.get("file_version_id") or "")


def _group_findings(round_body: dict) -> list[tuple[str, list[dict]]]:
    findings = [row for row in (round_body.get("findings") or []) if isinstance(row, dict)]
    groups: list[tuple[str, list[dict]]] = []
    for item in round_body.get("inputs") or []:
        if not isinstance(item, dict) or item.get("role") != TARGET:
            continue
        version = str(item.get("file_version_id") or "")
        rows = [row for row in findings if _finding_version(row) == version]
        groups.append((str(item.get("original_filename") or "未命名资料"), rows))
    return groups


def _semantic(finding: dict) -> dict:
    locator = finding.get("locator")
    semantic = locator.get("semantic") if isinstance(locator, dict) else None
    return semantic if isinstance(semantic, dict) else {}


def _scope(finding: dict) -> tuple[int, int, int] | None:
    semantic = _semantic(finding)
    scope = semantic.get("scope")
    values = scope if isinstance(scope, dict) else semantic
    total = values.get("total")
    checked = values.get("checked")
    unknown = values.get("unknown")
    if type(total) is int and total >= 0 and type(checked) is int and checked >= 0 and type(unknown) is int and unknown >= 0 and checked + unknown <= total:
        return total, checked, unknown
    return None


def _scope_text(rows: list[dict]) -> str:
    scopes = [scope for row in rows if (scope := _scope(row)) is not None]
    if not scopes:
        return ""
    total, checked, unknown = (sum(scope[index] for scope in scopes) for index in range(3))
    qualification = "" if len(scopes) == len(rows) else f"（仅 {len(scopes)}/{len(rows)} 项有结构化范围）"
    return f"条款核验记录{qualification}（按条款累计）：共 {total} 条，已判断 {checked} 条，无法判断 {unknown} 条"


def _status_text(rows: list[dict]) -> str:
    statuses = {row.get("status") for row in rows}
    return "、".join(status for status in (NG, UNABLE, NA, PASS) if status in statuses)


def _semantic_source_valid(source: dict, inputs: dict[str, dict]) -> bool:
    version = source.get("file_version_id")
    if not isinstance(version, str):
        return False
    item = inputs.get(version)
    if item is None or source.get("filename") != item.get("original_filename") or source.get("role") != item.get("role"):
        return False
    if public_locator(source.get("locator")) is None or not isinstance(source.get("excerpt"), str):
        return False
    return True


def writeback_state(round_body: dict) -> str:
    if not isinstance(round_body, dict) or round_body.get("execution") != EXEC_COMPLETED:
        return "round_incomplete"
    if round_body.get("failure") is not None:
        return "round_has_errors"
    findings = round_body.get("findings") or []
    if not findings or any(not isinstance(row, dict) for row in findings):
        return "round_incomplete"
    statuses = {str(row.get("status") or "") for row in findings}
    if not statuses.issubset(FINDING_STATUSES):
        return "round_incomplete"
    if ERR in statuses:
        return "round_has_errors"
    inputs = round_body.get("inputs") or []
    if not inputs or any(not isinstance(item, dict) for item in inputs):
        return "round_incomplete"
    input_versions = {str(item.get("file_version_id") or "") for item in inputs}
    if "" in input_versions or any(_finding_version(row) not in input_versions for row in findings):
        return "round_incomplete"
    by_version = {str(item["file_version_id"]): item for item in inputs}
    core_round = round_body.get("executor") == "ppap-checker-core"
    for row in findings:
        semantic = _semantic(row)
        if not semantic:
            if core_round:
                return "round_incomplete"
            continue
        checker_execution = semantic.get("verification_scope") == "checker_execution"
        if core_round and not checker_execution:
            return "round_incomplete"
        # Checker assertion counts are not scanned-row coverage. Keep historical
        # semantic scope validation, without fabricating a scope for Core output.
        if not checker_execution or "scope" in semantic:
            if _scope(row) is None:
                return "round_incomplete"
        if checker_execution:
            performed = semantic.get("checks_performed")
            basis = semantic.get("empty_scope_basis", "")
            if (performed is not None and (type(performed) is not int or performed < 0)) or not isinstance(basis, str):
                return "round_incomplete"
            if row["status"] == PASS and not basis.strip() and (performed is None or performed == 0):
                return "round_incomplete"
        target_input = by_version[_finding_version(row)]
        rule = target_input.get("rule") or {}
        if semantic.get("rule_version_id") != target_input.get("rule_version_id"):
            return "round_incomplete"
        body = rule.get("body") if isinstance(rule, dict) else None
        clauses = body.get("rules") if isinstance(body, dict) else None
        if checker_execution and not isinstance(clauses, list):
            return "round_incomplete"
        if isinstance(clauses, list):
            matches = [item for item in clauses if isinstance(item, dict) and item.get("id") == semantic.get("clause_id")]
            if len(matches) != 1 or matches[0].get("rule_to_check") != semantic.get("rule_text"):
                return "round_incomplete"
        sources = semantic.get("sources")
        if not isinstance(sources, list) or any(not isinstance(source, dict) or not _semantic_source_valid(source, by_version) for source in sources):
            return "round_incomplete"
        if checker_execution and row["status"] == PASS and not sources and not semantic.get("empty_scope_basis", "").strip():
            return "round_incomplete"
    groups = _group_findings(round_body)
    if not groups or any(not rows for _, rows in groups):
        return "round_incomplete"
    return "ready"


def _audit_clock(round_body: dict, now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    raw = round_body.get("created_at")
    if isinstance(raw, datetime):
        stamp = raw
    elif isinstance(raw, str) and raw:
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc)
    return stamp.strftime("%Y 年 %m 月 %d 日 %H:%M")


def history_text(round_body: dict, return_url: str = "", *, now: datetime | None = None) -> str:
    state = writeback_state(round_body)
    if state != "ready":
        raise FeishuWritebackError(state)
    lines = ["审核时间：" + _audit_clock(round_body, now), ""]
    index = 1
    for name, rows in _group_findings(round_body):
        lines.append("**审核内容" + str(index) + "：" + name + "**")
        lines.append("")
        lines.append("审核结果：" + _status_text(rows))
        source: dict = next((item for item in round_body["inputs"] if rows and item["file_version_id"] == _finding_version(rows[0])), {})
        if source.get("checker_verification"):
            lines.append(source["checker_verification"]["notice"])
        scope = _scope_text(rows)
        if scope:
            lines.append(scope)
        unresolved = [row for row in rows if row.get("status") in {NG, UNABLE, NA}]
        if unresolved:
            lines.append("总结：")
            for row in unresolved:
                item = str(row.get("item") or "").strip()
                suggestions = str(row.get("suggestions") or "").strip()
                if row.get("status") == NG and suggestions:
                    lines.append((item or name) + " : " + suggestions)
                elif row.get("status") in {UNABLE, NA}:
                    explanation = suggestions or ("缺少足够依据" if row.get("status") == UNABLE else "本条款不适用")
                    lines.append((item or name) + "（" + str(row["status"]) + "）：" + explanation)
        lines.append("")
        index += 1
    url = (return_url or "").strip()
    if url:
        lines.append("回访：" + url)
        lines.append("")
    return "\n".join(lines)


def comment_text(round_body: dict) -> str:
    lines: list[str] = []
    for name, rows in _group_findings(round_body):
        for row in rows:
            if row.get("status") not in {NG, UNABLE, NA}:
                continue
            suggestions = str(row.get("suggestions") or "").strip()
            item = str(row.get("item") or "").strip()
            if row.get("status") == NG and not suggestions:
                continue
            if not lines:
                lines.append("总结：")
            if row.get("status") == NG:
                lines.append((item or name) + "：" + suggestions)
            else:
                explanation = suggestions or ("缺少足够依据" if row.get("status") == UNABLE else "本条款不适用")
                lines.append((item or name) + "（" + str(row["status"]) + "）：" + explanation)
    return "\n".join(lines) if lines else ""


def _text_run(content: str, **style: object) -> dict:
    run: dict[str, object] = {"content": content}
    if style:
        run["text_element_style"] = style
    return {"text_run": run}


def _heading2(content: str) -> dict:
    return {"block_type": 4, "heading2": {"elements": [_text_run(content)]}}


def _heading3(content: str) -> dict:
    return {"block_type": 5, "heading3": {"elements": [_text_run(content)]}}


def _text(content: str, *, background: bool = False) -> dict:
    style = {"background_color": 3} if background else {}
    block = {"block_type": 2, "text": {"elements": [_text_run(content, **style)]}}
    return block


def _bullet(content: str) -> dict:
    return {"block_type": 12, "bullet": {"elements": [_text_run(content)]}}


def _finding_title(row: dict, number: int) -> str:
    locator_value = row.get("locator")
    locator = locator_value if isinstance(locator_value, dict) else {}
    clause = _semantic(row).get("clause_id") or locator.get("rule_item_id") or number
    return str(clause) + " · " + str(row.get("classification") or "条款")


def _source_location(locator: dict) -> str:
    kind = locator["kind"]
    if kind == "xlsx_cell":
        return f"{locator['sheet']}!{locator['cell']}"
    if kind == "xlsx_range":
        return f"{locator['sheet']}!{locator['start']}:{locator['end']}"
    if kind == "pdf_page":
        return f"第 {locator['page']} 页"
    if kind == "pdf_block":
        return f"第 {locator['page']} 页 · 文本块 {locator['block']}"
    if kind == "pdf_cell":
        return f"第 {locator['page']} 页 · 表格行 {locator['row']} · 列 {locator['column']}"
    if kind == "docx_paragraph":
        return f"段落 {locator['paragraph']}"
    return f"表格 {locator['table']} · 行 {locator['row']} · 列 {locator['column']}"


def _published_clause(clauses: object, clause_id: object, text: object) -> bool:
    return bool(text) and isinstance(clauses, list) and any(isinstance(clause, dict) and clause.get("rule_to_check") == text and (not clause_id or clause.get("id") == clause_id) for clause in clauses)


def _semantic_source_text(source: dict) -> str:
    role = "待审" if source["role"] == TARGET else "参考"
    return f"来源：{source['filename']}（{role}；文件版本 {source['file_version_id']}）；位置：{_source_location(source['locator'])}；原文摘录：{source['excerpt']}"


def report_blocks(round_body: dict, return_url: str = "") -> list[dict]:
    """Complete report, unlike the brief summary written into a source document."""
    state = writeback_state(round_body)
    if state != "ready":
        raise FeishuWritebackError(state)
    blocks = [_heading2("PPAP 审核报告"), _text("审核时间（UTC）：" + _audit_clock(round_body)), _text("机器结论与人工复核分别记录，人工判断不覆盖原始检查结果。")]
    targets = {item["file_version_id"]: item for item in round_body["inputs"] if item.get("role") == TARGET}
    findings = round_body.get("findings") or []
    counts = {status: sum(row.get("status") == status for row in findings) for status in (PASS, NG, UNABLE, NA, ERR)}
    pending = sum(not row.get("review") for row in findings)
    blocks.extend(
        [
            _heading2("本轮摘要"),
            _text(f"待审资料 {len(targets)} 份；自动检查 {len(findings)} 项：符合 {counts[PASS]}，不符合 {counts[NG]}，无法判断 {counts[UNABLE]}，不适用 {counts[NA]}，技术错误 {counts[ERR]}。"),
            _text(f"未记录人工复核 {pending} 项。已记录不等于问题已解决，本报告不代表正式批准。"),
            _text("未自动检查的规则条目会单独列出，不计入机器通过。"),
        ]
    )
    overall_scope = _scope_text(findings)
    if overall_scope:
        blocks.append(_text(overall_scope))
    for _, rows in _group_findings(round_body):
        for number, row in enumerate(rows, 1):
            if row.get("status") != "PASS":
                blocks.append(_bullet(str(row.get("status")) + " · " + _finding_title(row, number) + (" · 已记录复核" if row.get("review") else " · 未记录复核")))
    coverage = round_body.get("coverage") or {}
    unsupported = [item for item in coverage.get("rule_items") or [] if isinstance(item, dict) and item.get("coverage") == "unsupported"]
    if unsupported:
        blocks.append(_text("未覆盖条款（不计入机器通过）："))
        for item in unsupported:
            title = str(item.get("id") or "?") + " · " + str(item.get("category") or "规则条款")
            reason = item.get("reason")
            detail = {REASON_MANUAL_ONLY: "无对应内置检查，需人工核对", REASON_UNSUPPORTED: "当前文档类型没有可执行检查器"}.get(cast(str, reason), str(reason or "未自动检查"))
            blocks.append(_bullet(title + "：" + detail))
            version = item.get("file_version_id")
            if version:
                blocks.append(_text("待审文件版本：" + str(version)))
            rule = (targets.get(version) or {}).get("rule") or {}
            rule_body = rule.get("body") if isinstance(rule, dict) else None
            clauses = rule_body.get("rules") if isinstance(rule_body, dict) else None
            if not _published_clause(clauses, item.get("id"), item.get("rule_to_check")) and item.get("rule_to_check"):
                blocks.append(_text("本轮有效条款：" + str(item["rule_to_check"])))
    unchecked = [note for note in coverage.get("unchecked") or [] if not any(str(note) == unsupported_item_note(item) for item in unsupported)]
    if unchecked:
        blocks.append(_text("未检查范围：" + "；".join(map(str, unchecked))))
    for name, rows in _group_findings(round_body):
        blocks.append(_heading2(name))
        source = targets[_finding_version(rows[0])]
        rule = source.get("rule") or {}
        blocks.append(_text("匹配规则：" + str(rule.get("title") or "未记录规则名称")))
        if source.get("checker_verification"):
            blocks.append(_text(source["checker_verification"]["notice"]))
        blocks.append(_text("待审文件版本：" + source["file_version_id"]))
        if source.get("rule_version_id"):
            blocks.append(_text("规则版本：" + source["rule_version_id"]))
        rule_body = rule.get("body") if isinstance(rule, dict) else None
        published_clauses = rule_body.get("rules") if isinstance(rule_body, dict) else None
        if isinstance(published_clauses, list):
            blocks.append(_text("本轮已发布规则有效正文（条款 ID 与正文；不是原件坐标）："))
            for clause in published_clauses:
                if isinstance(clause, dict):
                    blocks.append(_bullet(str(clause.get("id") or "?") + "：" + str(clause.get("rule_to_check") or "")))
        rule_origin = (rule.get("source_file_version_id") if isinstance(rule, dict) else None) or source.get("rule_source_file_version_id")
        if rule_origin:
            blocks.append(_text("规则原件来源版本（仅溯源，不表示修订条款位于原件）：" + str(rule_origin)))
        for number, row in enumerate(rows, 1):
            meta = [str(row.get("classification") or "").strip(), str(row.get("priority") or "").strip()]
            blocks.append(_heading3(str(row["status"]) + " · " + _finding_title(row, number)))
            if any(meta):
                blocks.append(_text("分类/优先级：" + " · ".join(part for part in meta if part)))
            if row.get("suggestions"):
                blocks.append(_text(str(row["suggestions"])))
            semantic = _semantic(row)
            if semantic:
                clause_id = str(semantic.get("clause_id") or "").strip()
                rule_text = str(semantic.get("rule_text") or "").strip()
                if (clause_id or rule_text) and not _published_clause(published_clauses, clause_id, rule_text):
                    blocks.append(_text("本轮有效条款：" + (clause_id + " · " if clause_id else "") + rule_text))
                semantic_origin = semantic.get("rule_source_file_version_id")
                if semantic_origin and semantic_origin != rule_origin:
                    blocks.append(_text("规则原件来源版本（仅溯源）：" + str(semantic_origin)))
                scope = _scope(row)
                if scope:
                    blocks.append(_text(f"本条核验记录：共 {scope[0]} 条，已判断 {scope[1]} 条，无法判断 {scope[2]} 条"))
                if semantic.get("diagnostic"):
                    blocks.append(_text("无法判断原因：" + str(semantic["diagnostic"])))
                sources = semantic.get("sources") or []
                if sources:
                    blocks.extend(_text(_semantic_source_text(item)) for item in sources)
                else:
                    blocks.append(_text("依据：未提供可定位的资料来源。"))
            else:
                locator = row.get("locator") or {}
                if row.get("item") and not _published_clause(published_clauses, locator.get("rule_item_id"), row["item"]):
                    blocks.append(_text("检查项正文：" + str(row["item"])))
                positions = [str(locator["locator"])] if locator.get("locator") else []
                for evidence_source in (locator.get("evidence") or {}).get("sources") or []:
                    position = str(evidence_source.get("locator") or "")
                    if position and position not in positions:
                        positions.append(position)
                blocks.append(_text("依据：" + "；".join(positions) if positions else "未提供精确位置，请核对原件。"))
            review = row.get("review") or {}
            if review.get("judgment"):
                blocks.append(_text("人工复核：" + str(review["judgment"])))
                for field, label in (("rationale", "理由"), ("next_step", "下一步")):
                    if review.get(field):
                        blocks.append(_text(label + "：" + str(review[field])))
    references = [str(item.get("original_filename") or "未命名资料") for item in round_body["inputs"] if item.get("role") != TARGET]
    if references:
        blocks.append(_text("参考资料（不单独审核）：" + "、".join(references)))
    if return_url:
        blocks.append(_text("查看审核与原件：" + return_url))
    return blocks


def structured_blocks(content: str) -> list[dict]:
    children: list[dict] = []
    lines = (content or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("审核时间："):
            children.append(_heading2(line))
            index += 1
            continue
        if line.startswith("**审核内容") and line.endswith("**"):
            children.append(_heading3(line[2:-2]))
            index += 1
            continue
        if line == "总结：":
            children.append(_text(line, background=True))
            index += 1
            while index < len(lines):
                item = lines[index].strip()
                if not item:
                    index += 1
                    break
                children.append(_bullet(item))
                index += 1
            continue
        children.append(_text(line))
        index += 1
    return children


def _response_json(response) -> dict:
    parser = getattr(response, "json", None)
    if callable(parser):
        try:
            payload = parser()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return payload
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        try:
            payload = json.loads(text)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _ok(response) -> bool:
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 300:
        return False
    body = _response_json(response)
    return body.get("code") in (0, None)


def insert_history(http, access_token: str, document_id: str, content: str) -> None:
    children = structured_blocks(content)
    if not children:
        raise FeishuWritebackError("feishu_writeback_failed")
    # Feishu accepts at most 50 children. Advance the insertion index so later
    # batches follow the earlier ones while the whole history precedes old text.
    # Any ambiguous batch stops the write; never replay an earlier batch.
    for offset in range(0, len(children), 50):
        try:
            response = http.post(
                docx_children_url(document_id),
                headers={
                    "Authorization": "Bearer " + access_token,
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={"index": offset, "children": children[offset : offset + 50]},
                timeout=20,
            )
        except Exception as exc:
            raise FeishuWritebackError("feishu_writeback_failed") from exc
        if not _ok(response):
            raise FeishuWritebackError("feishu_writeback_failed")


def add_file_comment(http, access_token: str, file_token: str, content: str) -> bool:
    body = {
        "reply_list": {
            "replies": [
                {
                    "content": {
                        "elements": [{"type": "text_run", "text_run": {"text": content}}],
                    }
                }
            ]
        }
    }
    try:
        response = http.post(
            drive_comments_url(file_token),
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json; charset=utf-8",
            },
            json=body,
            timeout=20,
        )
    except Exception:
        return False
    return _ok(response)


def write_round_history(
    http,
    access_token: str,
    file_token: str,
    round_body: dict,
    return_url: str,
) -> dict[str, object]:
    token = (file_token or "").strip()
    if not token or "/" in token or ".." in token:
        raise FeishuWritebackError("invalid_file_token")
    state = writeback_state(round_body)
    if state != "ready":
        raise FeishuWritebackError(state)
    text = history_text(round_body, return_url)
    insert_history(http, access_token, token, text)
    comment = comment_text(round_body)
    commented = bool(comment) and add_file_comment(http, access_token, token, comment)
    return {"inserted": True, "commented": commented, "file_token": token}
