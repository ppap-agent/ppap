"""UAES U13 candidate: production sample photo, UAES v1.0 revision 3 (PHOTO:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES photo reader as the carrier for every format a submitted photo may come in; at run time
`Audit` extends it with the revision 3 clause method below. The verdict reads only the file's leading bytes:
no model is involved.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from resources import RESOURCE

# The image formats the clause lists, by their leading bytes.
SIGNATURES = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
)
# Formats recognised only to name them in the basis; none of them passes.
OTHERS = (
    (b"%PDF", "PDF文档"),
    (b"PK\x03\x04", "Office文档（xlsx/docx等压缩容器）"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "Office文档（xls/doc等旧版容器）"),
    (b"GIF8", "GIF图像"),
)
MEDIA = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".emf", ".wmf", ".webp")


def kind_of(head: bytes) -> tuple[str | None, str]:
    """(the listed image format, or None; a name for the basis)."""
    for magic, name in SIGNATURES:
        if head.startswith(magic):
            return name, name
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return None, "WebP图像"
    for magic, name in OTHERS:
        if head.startswith(magic):
            return None, name
    return None, "无法识别的文件类型"


def embedded_images(path: Path, head: bytes) -> int | None:
    """How many image objects a document container holds; None when not counted (legacy Office containers)."""
    try:
        if head.startswith(b"%PDF"):
            import fitz

            with fitz.open(path) as document:
                return len({image[0] for page in document for image in page.get_images(full=True)})
        if head.startswith(b"PK\x03\x04"):
            with zipfile.ZipFile(path) as archive:
                return sum(1 for name in archive.namelist() if "/media/" in name and name.lower().endswith(MEDIA))
    except Exception:
        return None
    return None


class PhotoClauses:
    """PHOTO:1 over the declared UAES photo reader (`READER` is its module)."""

    READER: Any = None

    @rule("生产件样品照片按文件头判定图片格式")
    def photo_1(self):
        path = Path(self.audit_file.file_url)
        data = path.read_bytes()
        name = path.name
        if len(data) < 4:
            return "NG", f"文件{name}为{len(data)}字节，文件头不足4字节，无法判型，属文件对象不可获取，请补交原始图片文件。"
        listed, found = kind_of(data[:16])
        self.witness({"kind": "file_version", "bytes": "0-15"}, f"文件头：{data[:8].hex(' ')}（{found}）")
        if listed is not None:
            return "PASS", f"文件{name}的文件头为{listed}格式，属条文所列图片格式（jpg／jpeg／png／bmp／tif／tiff）。"
        if found in {"WebP图像", "GIF图像", "无法识别的文件类型"}:
            return "NG", f"文件{name}的文件头为{found}，不属条文所列图片格式（jpg／jpeg／png／bmp／tif／tiff），扩展名不作为依据。"
        count = embedded_images(path, data[:16])
        inside = f"该容器内含{count}个图像对象，" if count else "" if count == 0 else "容器内的图像对象未统计，"
        return "NG", f"文件{name}为{found}，载体不符：非图片容器不能代替图片文件，{inside}建议供应商改交原始图片文件（整改指向是改交载体，不是重拍照片）。"


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesPhotoAgent": PhotoClauses}

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (self.CLAUSES[base.__name__], base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)
