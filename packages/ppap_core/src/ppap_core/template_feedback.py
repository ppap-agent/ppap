"""Feishu template-feedback effects. Unconfirmed writes are never replayed."""

import httpx

BASE = "https://open.feishu.cn/open-apis"


class FeedbackError(Exception):
    pass


def _call(http, method, path, token, **kwargs) -> dict:
    try:
        response = http.request(method, BASE + path, headers={"Authorization": "Bearer " + token}, timeout=30, **kwargs)
        payload = response.json()
        if not 200 <= response.status_code < 300 or not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(payload.get("data"), dict):
            raise FeedbackError("feedback_provider_unconfirmed")
        return payload["data"]
    except (httpx.HTTPError, ValueError) as error:
        raise FeedbackError("feedback_provider_unconfirmed") from error


def _record_path(base_token: str, table_id: str) -> str:
    if not all(value and value.isascii() and value.replace("_", "").isalnum() for value in (base_token, table_id)):
        raise FeedbackError("feedback_destination_invalid")
    return f"/bitable/v1/apps/{base_token}/tables/{table_id}/records"


def _record_id(record, operation: str, tokens: list[str]) -> str:
    if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
        raise FeedbackError("feedback_record_unconfirmed")
    fields = record["fields"]
    request_number = fields.get("请求编号")
    if isinstance(request_number, list):
        if any(not isinstance(part, dict) or not isinstance(part.get("text"), str) for part in request_number):
            raise FeedbackError("feedback_record_unconfirmed")
        request_number = "".join(part["text"] for part in request_number)
    attachments = fields.get("文件附件")
    actual = [item.get("file_token") for item in attachments if isinstance(item, dict)] if isinstance(attachments, list) else []
    record_id = record.get("record_id")
    if (
        request_number != operation
        or not tokens
        or len(actual) != len(tokens)
        or not isinstance(attachments, list)
        or len(attachments) != len(tokens)
        or not all(isinstance(token, str) and token for token in actual)
        or set(actual) != set(tokens)
        or not isinstance(record_id, str)
        or not record_id.strip()
    ):
        raise FeedbackError("feedback_record_unconfirmed")
    return record_id


def create_feedback_record(http, token: str, base_token: str, table_id: str, operation: str, fields: dict) -> str:
    data = _call(http, "POST", _record_path(base_token, table_id), token, params={"client_token": operation}, json={"fields": fields})
    return _record_id(data.get("record"), operation, [item["file_token"] for item in fields["文件附件"]])


def find_feedback_record(http, token: str, base_token: str, table_id: str, operation: str, tokens: list[str]) -> str:
    data = _call(
        http,
        "POST",
        _record_path(base_token, table_id) + "/search",
        token,
        params={"page_size": 2},
        json={"field_names": ["请求编号", "文件附件"], "filter": {"conjunction": "and", "conditions": [{"field_name": "请求编号", "operator": "is", "value": [operation]}]}},
    )
    items = data.get("items")
    if data.get("has_more") is not False or not isinstance(items, list) or len(items) != 1:
        # Zero results cannot exclude a late create. Multiple results need an operator.
        raise FeedbackError("feedback_record_unconfirmed")
    return _record_id(items[0], operation, tokens)


def upload_feedback_attachment(http, token: str, base_token: str, filename: str, body: bytes) -> str:
    if not body or len(body) > 200 * 1024 * 1024:
        raise FeedbackError("feedback_attachment_size")
    _record_path(base_token, "validation")
    data = _call(http, "POST", "/drive/v1/medias/upload_prepare", token, json={"file_name": filename, "parent_type": "bitable_file", "parent_node": base_token, "size": len(body)})
    upload_id, block_size, block_num = data.get("upload_id"), data.get("block_size"), data.get("block_num")
    if (
        not isinstance(upload_id, str)
        or not upload_id
        or type(block_size) is not int
        or not 0 < block_size <= 8 * 1024 * 1024
        or type(block_num) is not int
        or block_num > 1024
        or block_num != (len(body) + block_size - 1) // block_size
    ):
        raise FeedbackError("feedback_upload_unconfirmed")
    for seq in range(block_num):
        part = body[seq * block_size : (seq + 1) * block_size]
        _call(http, "POST", "/drive/v1/medias/upload_part", token, data={"upload_id": upload_id, "seq": str(seq), "size": str(len(part))}, files={"file": (filename, part, "application/octet-stream")})
    data = _call(http, "POST", "/drive/v1/medias/upload_finish", token, json={"upload_id": upload_id, "block_num": block_num})
    file_token = data.get("file_token")
    if not isinstance(file_token, str) or not file_token.strip():
        raise FeedbackError("feedback_upload_unconfirmed")
    return file_token
