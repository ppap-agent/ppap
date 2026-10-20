import json
import uuid

import httpx
import pytest
from ppap_core.template_feedback import FeedbackError, FeedbackRejected, create_feedback_record, find_feedback_record, upload_feedback_attachment


@pytest.mark.parametrize("status,code", [(400, 99991672), (403, 99991679), (403, 1254302), (403, 1254304), (200, 1254302)])
def test_documented_permission_denial_proves_record_creation_was_rejected(status, code):
    fields = {"请求编号": "operation", "文件附件": [{"file_token": "box_original"}]}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"code": code, "msg": "private provider details"}))) as http:
        with pytest.raises(FeedbackRejected, match="^feedback_permission_denied$"):
            create_feedback_record(http, "test-token", "base_test", "tbl_test", "operation", fields)


def test_lost_record_reply_is_read_back_without_creating_another_record():
    operation = str(uuid.uuid4())
    fields = {"请求编号": operation, "文件附件": [{"file_token": "box_original"}]}
    writes = []

    def provider(request):
        if request.url.path.endswith("/records"):
            writes.append(json.loads(request.content))
            assert request.url.params["client_token"] == operation
            raise httpx.ReadTimeout("receipt lost", request=request)
        assert request.url.path.endswith("/records/search")
        assert json.loads(request.content)["filter"]["conditions"] == [{"field_name": "请求编号", "operator": "is", "value": [operation]}]
        return httpx.Response(200, json={"code": 0, "data": {"items": [{"record_id": "rec_received", "fields": fields}], "has_more": False}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(FeedbackError):
            create_feedback_record(http, "test-token", "base_test", "tbl_test", operation, fields)
        assert find_feedback_record(http, "test-token", "base_test", "tbl_test", operation, ["box_original"]) == "rec_received"
    assert len(writes) == 1


@pytest.mark.parametrize(
    "items,has_more", [([], False), ([{"record_id": "rec_wrong", "fields": {"请求编号": "other", "文件附件": []}}], False), ([{"record_id": "a"}, {"record_id": "b"}], False), ([], True)]
)
def test_absence_mismatch_and_ambiguous_record_do_not_prove_feedback_received(items, has_more):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": has_more}}))) as http:
        with pytest.raises(FeedbackError):
            find_feedback_record(http, "test-token", "base_test", "tbl_test", "original", ["box_original"])


def test_attachment_upload_uses_the_configured_base_and_complete_multipart_sequence():
    parts = []

    def provider(request):
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "upload_prepare":
            assert json.loads(request.content) == {"file_name": "模板.zip", "parent_type": "bitable_file", "parent_node": "base_test", "size": 6}
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": "upload-a", "block_size": 3, "block_num": 2}})
        if path == "upload_part":
            parts.append(request.content)
            return httpx.Response(200, json={"code": 0, "data": {}})
        assert path == "upload_finish" and json.loads(request.content) == {"upload_id": "upload-a", "block_num": 2}
        return httpx.Response(200, json={"code": 0, "data": {"file_token": "box_uploaded"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        assert upload_feedback_attachment(http, "test-token", "base_test", "模板.zip", b"abcdef") == "box_uploaded"
    assert len(parts) == 2 and b"abc" in parts[0] and b"def" in parts[1]
