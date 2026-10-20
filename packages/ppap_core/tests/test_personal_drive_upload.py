import json

import httpx
import pytest
from ppap_core.feishu_personal_upload import PersonalDriveClient, UploadUnknown


def test_personal_directory_and_multipart_use_the_users_identity_and_original_bytes():
    calls = []

    def provider(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-user-token"
        path = request.url.path
        if path.endswith("root_folder/meta"):
            return httpx.Response(200, json={"code": 0, "data": {"token": "personal-root"}})
        if path.endswith("upload_prepare"):
            assert json.loads(request.content) == {"file_name": "审核.xlsx", "parent_type": "explorer", "parent_node": "chosen-folder", "size": 5}
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": "upload-one", "block_size": 4, "block_num": 2}})
        if path.endswith("upload_part"):
            assert b'filename="part"' in request.content
            assert b"abcd" in request.content or b"\r\ne\r\n" in request.content
            return httpx.Response(200, json={"code": 0, "data": None})
        assert path.endswith("upload_finish")
        assert json.loads(request.content) == {"upload_id": "upload-one", "block_num": 2}
        return httpx.Response(200, json={"code": 0, "data": {"file_token": "new-cloud-file"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        client = PersonalDriveClient(http, "test-user-token")
        assert client.root_folder() == {"token": "personal-root", "name": "我的文件夹", "type": "folder"}
        plan = client.prepare("审核.xlsx", 5, "chosen-folder")
        client.part(plan, 0, b"abcd")
        client.part(plan, 1, b"e")
        assert client.finish(plan) == "new-cloud-file"
    assert len(calls) == 5


def test_a_lost_finish_response_is_not_automatically_retried():
    calls = []

    def provider(request):
        calls.append(request)
        raise httpx.ReadTimeout("provider response unavailable")

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(UploadUnknown):
            PersonalDriveClient(http, "test-user-token").finish({"upload_id": "upload-one", "block_size": 4, "block_num": 1})
    assert len(calls) == 1
