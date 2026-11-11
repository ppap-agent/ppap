"""UAES U13 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from pathlib import Path

from audit.checker_lib.source import SourceAudit
from resources import RESOURCE


class UaesPhotoAgent(SourceAudit):
    SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".pdf"})

    def rule_1(self):
        from documents.photo_inputs import validate_photo_input

        path = Path(self.audit_file.file_url)
        result = validate_photo_input(path.name, path.read_bytes())
        message = "需人工确认样品照片是否符合要求；可读取图片不代表照片内容正确。"
        if result["state"] == "valid":
            self.witness({"kind": "file_version"}, f"已安全解码{result['image_count']}幅图像。")
            return "PASS", message
        return ("ERR" if result["state"] == "error" else "UNABLE"), "；".join(result["reasons"]) + "；" + message


def create_audit(audit_file, rule, group_context=None):
    return UaesPhotoAgent(audit_file, rule, group_context, resource=RESOURCE)
