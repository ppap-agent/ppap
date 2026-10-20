"""User-authorized Drive writes. Durable operation state belongs to the upload receipt."""

from __future__ import annotations

import re
from typing import TypedDict

from ppap_core.feishu_drive import OPEN_API
from ppap_core.files import MAX_UPLOAD_BYTES, original_filename


class UploadUnknown(Exception):
    pass


class UploadRejected(Exception):
    pass


class UploadPlan(TypedDict):
    upload_id: str
    block_size: int
    block_num: int


class PersonalDriveClient:
    def __init__(self, http, access_token: str):
        self.http = http
        self.headers = {"Authorization": "Bearer " + access_token}

    def _request(self, method: str, path: str, *, finishing: bool = False, **kwargs) -> dict:
        try:
            response = self.http.request(method, OPEN_API + path, headers=self.headers, timeout=60, **kwargs)
            payload = response.json()
        except Exception:
            raise UploadUnknown("feishu_upload_response_unknown") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("code"), int):
            raise UploadUnknown("feishu_upload_response_unknown")
        if response.status_code != 200 or payload["code"] != 0:
            # A finish may have published a file even when its response is lost
            # or inconclusive. Only explicit authorization/size/quota rejections
            # establish a no-file failure at this boundary.
            no_effect = {99991663, 99991668, 99991672, 99991676, 99991677, 1061043, 1061061, 1061073}
            if not finishing or payload["code"] in no_effect:
                raise UploadRejected("feishu_upload_rejected:" + str(payload["code"]))
            raise UploadUnknown("feishu_upload_finish_unknown")
        data = payload.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise UploadUnknown("feishu_upload_response_unknown")
        return data

    def root_folder(self) -> dict:
        data = self._request("GET", "/drive/explorer/v2/root_folder/meta")
        token = data.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
            raise UploadUnknown("feishu_root_folder_unknown")
        return {"token": token, "name": "我的文件夹", "type": "folder"}

    def prepare(self, filename: str, size: int, folder_token: str) -> UploadPlan:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", folder_token) or not 0 < size <= MAX_UPLOAD_BYTES:
            raise ValueError("invalid_drive_upload")
        data = self._request("POST", "/drive/v1/files/upload_prepare", json={"file_name": original_filename(filename), "parent_type": "explorer", "parent_node": folder_token, "size": size})
        upload_id, block_size, block_num = (data.get(key) for key in ("upload_id", "block_size", "block_num"))
        if (
            not isinstance(upload_id, str)
            or not upload_id
            or type(block_size) is not int
            or not 0 < block_size <= MAX_UPLOAD_BYTES
            or type(block_num) is not int
            or block_num != (size + block_size - 1) // block_size
        ):
            raise UploadUnknown("feishu_upload_plan_unknown")
        return {"upload_id": upload_id, "block_size": block_size, "block_num": block_num}

    def part(self, plan: UploadPlan, sequence: int, data: bytes) -> None:
        if not 0 <= sequence < plan["block_num"] or not 0 < len(data) <= plan["block_size"]:
            raise ValueError("invalid_drive_part")
        self._request(
            "POST", "/drive/v1/files/upload_part", data={"upload_id": plan["upload_id"], "seq": str(sequence), "size": str(len(data))}, files={"file": ("part", data, "application/octet-stream")}
        )

    def finish(self, plan: UploadPlan) -> str:
        data = self._request("POST", "/drive/v1/files/upload_finish", finishing=True, json={"upload_id": plan["upload_id"], "block_num": plan["block_num"]})
        token = data.get("file_token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
            raise UploadUnknown("feishu_upload_finish_unknown")
        return token
