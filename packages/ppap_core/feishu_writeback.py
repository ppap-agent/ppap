"""Write common round findings back into the source Feishu doc. Not a second ledger."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from ppap_core.audits import ERR, EXEC_COMPLETED, FINDING_STATUSES, NG, TARGET
from ppap_core.feishu_drive import OPEN_API


class FeishuWritebackError(Exception):
    pass


def _report_error(response, stage: str) -> FeishuWritebackError:
    # Preserve only a public numeric code, never provider messages or credentials.
    status = int(getattr(response, 'status_code', 0) or 0)
    code = _response_json(response).get('code')
    detail = str(code) if type(code) is int and code != 0 else 'http_' + str(status)
    # Feishu can return missing app/user permission as HTTP 400, before creation.
    outcome = 'rejected' if status in (401, 403, 422, 429) or code in (99991672, 99991679) else 'unknown'
    return FeishuWritebackError(f'feishu_report_{stage}_{outcome}:{detail}')


def create_report_document(http, access_token: str, title: str) -> str:
    """One external attempt. Caller must persist its claim before this call."""
    try:
        response = http.post(
            OPEN_API + "/docx/v1/documents",
            headers={"Authorization": "Bearer " + access_token},
            json={"title": title[:200]}, timeout=20,
        )
    except Exception as exc:
        raise FeishuWritebackError("feishu_report_create_unknown") from exc
    payload = _response_json(response)
    if not _ok(response) or payload.get("code") != 0:
        raise _report_error(response, 'create')
    document_id = ((payload.get("data") or {}).get("document") or {}).get("document_id")
    if not isinstance(document_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
        raise FeishuWritebackError("feishu_report_create_unknown")
    return document_id


def append_report_blocks(http, access_token: str, document_id: str,
                         blocks: list[dict], operation_id: str) -> None:
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
                json={"index": -1, "children": blocks[offset:offset + 50]}, timeout=20,
            )
        except Exception as exc:
            raise FeishuWritebackError("feishu_report_write_unknown") from exc
        if not _ok(response) or _response_json(response).get("code") != 0:
            raise _report_error(response, 'write')


def docx_children_url(document_id: str) -> str:
    return OPEN_API + "/docx/v1/documents/" + document_id + "/blocks/" + document_id + "/children"


def drive_comments_url(file_token: str) -> str:
    return OPEN_API + "/drive/v1/files/" + file_token + "/comments?file_type=docx"


def round_return_url(web_url: str, round_id: str) -> str:
    base = (web_url or "").rstrip("/")
    return base + "/?round=" + round_id


def _finding_version(finding: dict) -> str:
    locator = finding.get("locator") if isinstance(finding.get("locator"), dict) else {}
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


def writeback_state(round_body: dict) -> str:
    if not isinstance(round_body, dict) or round_body.get("execution") != EXEC_COMPLETED:
        return "round_incomplete"
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
        ng_rows = [row for row in rows if row.get("status") == NG]
        if ng_rows:
            lines.append("审核结果：NG")
            lines.append("总结：")
            for row in ng_rows:
                item = str(row.get("item") or "").strip()
                suggestions = str(row.get("suggestions") or "").strip()
                if suggestions:
                    lines.append((item or name) + " : " + suggestions)
        else:
            lines.append("审核结果：PASS")
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
            if row.get("status") != NG:
                continue
            suggestions = str(row.get("suggestions") or "").strip()
            item = str(row.get("item") or "").strip()
            if not suggestions:
                continue
            if not lines:
                lines.append("总结：")
            lines.append((item or name) + "：" + suggestions)
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


def report_blocks(round_body: dict, return_url: str = "") -> list[dict]:
    """Complete report, unlike the brief summary written into a source document."""
    state = writeback_state(round_body)
    if state != "ready":
        raise FeishuWritebackError(state)
    blocks = [_heading2("PPAP 审核报告"), _text("审核时间（UTC）：" + _audit_clock(round_body)),
              _text("机器结论与人工复核分别记录，人工判断不覆盖原始检查结果。")]
    targets = {item['file_version_id']: item for item in round_body['inputs'] if item.get('role') == TARGET}
    findings = round_body.get('findings') or []
    counts = {status: sum(row.get('status') == status for row in findings) for status in ('PASS', 'NG', 'ERR')}
    pending = sum(not row.get('review') for row in findings)
    blocks.extend([
        _heading2('本轮摘要'),
        _text(f"待审资料 {len(targets)} 份；自动检查 {len(findings)} 项：通过 {counts['PASS']}，不符合 {counts['NG']}，检查异常 {counts['ERR']}。"),
        _text(f'未记录人工复核 {pending} 项。已记录不等于问题已解决，本报告不代表正式批准。'),
        _text('未自动检查的规则条目会单独列出，不计入机器通过。'),
    ])
    for row in findings:
        if row.get('status') != 'PASS':
            blocks.append(_bullet(str(row.get('item') or '检查项') + (' · 已记录复核' if row.get('review') else ' · 未记录复核')))
    unchecked = (round_body.get('coverage') or {}).get('unchecked') or []
    if unchecked:
        blocks.append(_text('未检查范围：' + '；'.join(map(str, unchecked))))
    for name, rows in _group_findings(round_body):
        blocks.append(_heading2(name))
        source = targets[_finding_version(rows[0])]
        rule = source.get('rule') or {}
        blocks.append(_text('匹配规则：' + str(rule.get('title') or '未记录规则名称')))
        blocks.append(_text('待审文件版本：' + source['file_version_id']))
        if source.get('rule_version_id'):
            blocks.append(_text('规则版本：' + source['rule_version_id']))
        for row in rows:
            meta = [str(row.get("classification") or "").strip(), str(row.get("priority") or "").strip()]
            blocks.append(_heading3(str(row["status"]) + " · " + str(row.get("item") or "检查项")))
            if any(meta):
                blocks.append(_text("分类/优先级：" + " · ".join(part for part in meta if part)))
            if row.get("suggestions"):
                blocks.append(_text(str(row["suggestions"])))
            locator = row.get("locator") or {}
            positions = [str(locator["locator"])] if locator.get("locator") else []
            for source in (locator.get("evidence") or {}).get("sources") or []:
                position = str(source.get("locator") or "")
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
    try:
        response = http.post(
            docx_children_url(document_id),
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"index": 0, "children": children},
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
