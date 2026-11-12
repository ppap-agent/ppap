"""Built-in customer document templates.

The files are read-only customer originals under `<template_root>/<company>/templates`.
This manifest only gives each one a clean display name and a document-type label;
the originals are never renamed or rewritten.
"""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from product.feishu_personal_upload import PersonalDriveClient, UploadRejected, UploadUnknown
from product.settings import Settings

ZIP_CHUNK = 64 * 1024
MAX_FOLDER_PAGES = 40


@dataclass(frozen=True)
class Template:
    id: str
    source: str
    name: str
    document_type: str
    # The PPAP type the template is for: a standard T code, a customer-only E code, or None while undecided.
    type_code: str | None = None

    @property
    def extension(self) -> str:
        return Path(self.source).suffix.lower()

    @property
    def filename(self) -> str:
        return self.name + self.extension


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    templates: tuple[Template, ...]


CUSTOMERS: tuple[Customer, ...] = (
    Customer(
        "li",
        "理想汽车",
        (
            Template("li-design-verification-plan", "APQP-零部件设计验证计划.xlsx", "零部件设计验证计划", "零部件设计验证计划和报告", "T02"),
            Template("li-material-supplier-list", "APQP-材料、产品及分供方清单.xlsx", "材料、产品及分供方清单", "材料产品及分供清单", "T04"),
            Template("li-gauge-acceptance", "APQP-检具认可报告.xlsx", "检具认可报告", "检具验收报告", "T05"),
            Template("li-mould-acceptance", "APQP-模具验收报告.xls", "模具验收报告", "模具验收报告", "T06"),
            Template("li-msa", "APQP-测量系统分析报告.xlsx", "测量系统分析报告", "测量系统分析评价", "T07"),
            Template("li-process-capability", "APQP-过程能力分析报告.xlsx", "过程能力分析报告", "过程能力研究", "T08"),
            Template("li-process-flow", "APQP-过程流程图.xlsx", "过程流程图", "过程流程图", "T09"),
            Template("li-pfmea", "APQP-潜在过程失效模式及后果分析（PFMEA）.xlsx", "潜在过程失效模式及后果分析（PFMEA）", "潜在过程失效模式及后果分析（PFMEA）", "T10"),
            Template("li-control-plan", "APQP-生产控制计划.xlsx", "生产控制计划", "控制计划", "T11"),
            Template("li-special-characteristics", "APQP-过程特殊特性清单.xlsx", "过程特殊特性清单", "过程特殊特性清单", "T12"),
            Template("li-ccsc", "APQP-产品过程CCSC特性管理清单.xlsx", "产品过程CCSC特性管理清单", "CC/SC 特性管理清单"),
            Template("li-equipment", "APQP-设备开发验收清单.xlsx", "设备开发验收清单", "设备清单", "T13"),
            Template("li-capacity", "APQP-产能评估表.xlsx", "产能评估表", "生产系统产能评估报告", "T14"),
            Template("li-issue-list", "APQP-项目问题清单.xlsx", "项目问题清单", "APQP项目问题清单", "T15"),
            Template("li-annual-test", "APQP-零部件年度试验要求.xlsx", "零部件年度试验要求", "零部件年度试验要求", "T16"),
            Template("li-psw", "APQP-零件提交保证书（PSW签署）.xlsx", "零件提交保证书（PSW签署）", "零件提交保证书", "T17"),
            Template("li-dimension-report", "APQP-尺寸检验报告.xlsx", "尺寸检验报告", "尺寸检验报告", "T01"),
        ),
    ),
    Customer(
        "nio",
        "蔚来汽车",
        (
            Template("nio-measurement-report", "1-2 尺寸测量报告.xlsm", "尺寸测量报告", "测量报告", "T01"),
            Template("nio-process-flow", "8 过程流程图.xlsx", "过程流程图", "过程流程图", "T09"),
            Template("nio-pfmea", "9 潜在过程失效模式及后果分析.xlsx", "潜在过程失效模式及后果分析", "潜在过程失效模式及后果分析（PFMEA）", "T10"),
            Template("nio-control-plan", "10 控制计划.xlsx", "控制计划", "控制计划", "T11"),
            Template("nio-special-characteristics", "11 特殊特性清单.xlsx", "特殊特性清单", "特殊特性清单", "T12"),
        ),
    ),
    Customer(
        "uaes",
        "联合汽车电子（UAES）",
        (
            Template("uaes-psw", "新-F-PU-206_零件提交保证书_PartSubmissionWarrant_V03 (1).xls", "零件提交保证书（F-PU-206）", "零件提交保证书（PSW）", "T17"),
            Template("uaes-pgqp", "F-PU-127_采购件质量计划_Purchased Goods Quality Plan PGQP1_V06 (1).xlsx", "采购件质量计划 PGQP1（F-PU-127）", "采购件质量计划", "E12"),
            Template("uaes-measure-comparison", "F-PU-166_测量比较报告_Measure Comparison Report_V02.xls (1).xls", "测量比较报告（F-PU-166）", "测量比较报告", "E02"),
            Template("uaes-sample-inspection", "F-PU-207_样品检验报告_Sample Inspection Report_V01-.xls (1).xls", "样品检验报告（F-PU-207）", "初始样品检验报告", "E01"),
            Template("uaes-sample-approval", "F01RC0YA8601 样品认可报告 (1).xls", "样品认可报告", "样品认可报告"),
            Template("uaes-material-supplier-list", "7. 材料及分供方清单模板 (1).xlsx", "材料及分供方清单", "材料产品及分供清单", "T04"),
            Template("uaes-traceability", "零件追溯检查单-模板 20250609.xlsx", "零件追溯检查单", "零件追溯说明", "E10"),
        ),
    ),
)

# Which customer's built-in rules a template customer is counted against.
PRESET_CUSTOMERS = {"li": "理想", "nio": "蔚来", "uaes": "UAES"}


def customer_type_codes(customer: Customer) -> list[str]:
    """The document types this customer's built-in rules cover (UAES adds its own E types); coverage counts against these."""
    from audit.presets import customer_presets

    return sorted({preset.source_type for preset in customer_presets() if preset.customer == PRESET_CUSTOMERS.get(customer.id)})


CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
}


def template_root(settings: Settings) -> Path:
    return Path(settings.template_root.strip() or "assets")


def source_path(root: Path, customer: Customer, template: Template) -> Path:
    # Only manifest names reach the filesystem; request input selects, never builds, a path.
    return root / customer.id / "templates" / template.source


def find_customer(customer_id: str) -> Customer | None:
    return next((customer for customer in CUSTOMERS if customer.id == customer_id), None)


def find_template(customer: Customer, template_id: str) -> Template | None:
    return next((template for template in customer.templates if template.id == template_id), None)


def available(root: Path, customer: Customer) -> list[tuple[Template, int]]:
    rows = []
    for template in customer.templates:
        path = source_path(root, customer, template)
        if path.is_file():
            rows.append((template, path.stat().st_size))
    return rows


def catalog(root: Path) -> dict:
    return {
        "customers": [
            {
                "id": customer.id,
                "name": customer.name,
                "templates": [
                    {
                        "id": template.id,
                        "name": template.name,
                        "document_type": template.document_type,
                        "extension": template.extension,
                        "filename": template.filename,
                        "byte_size": size,
                        "type_code": template.type_code,
                    }
                    for template, size in available(root, customer)
                ],
                "standard_type_codes": customer_type_codes(customer),
            }
            for customer in CUSTOMERS
        ],
    }


def content_type(template: Template) -> str:
    return CONTENT_TYPES.get(template.extension, "application/octet-stream")


def zip_chunks(root: Path, customer: Customer) -> Iterator[bytes]:
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
        for template, _size in available(root, customer):
            archive.write(source_path(root, customer, template), template.filename)
    spool.seek(0)

    def stream() -> Iterator[bytes]:
        with spool:
            while block := spool.read(ZIP_CHUNK):
                yield block

    return stream()


SaveState = Literal["saved", "exists", "failed", "unknown"]


class SaveResult(TypedDict):
    template_id: str
    filename: str
    state: SaveState
    file_token: str | None
    reason: str | None


def folder_names(list_folder: Callable[[str, str], dict], folder_token: str) -> set[str]:
    """Read back the destination before any write. Unreadable means no write."""
    names: set[str] = set()
    page = ""
    for _ in range(MAX_FOLDER_PAGES):
        listing = list_folder(folder_token, page)
        names.update(item["name"] for item in listing["files"])
        page = listing.get("next_page_token") or ""
        if not page:
            return names
    raise ValueError("folder_too_large")


def save_to_drive(root: Path, customer: Customer, templates: list[Template], folder_token: str, drive: PersonalDriveClient, list_folder: Callable[[str, str], dict]) -> list[SaveResult]:
    """Upload each template once, after reading the folder back.

    A same-name file already in the folder is never replaced or duplicated, so
    repeating a request after an unconfirmed result reconciles instead of
    writing again. An unconfirmed finish is reported, never retried here.
    """
    existing = folder_names(list_folder, folder_token)
    results: list[SaveResult] = []

    def report(template: Template, state: SaveState, reason: str | None, token: str | None = None) -> None:
        results.append({"template_id": template.id, "filename": template.filename, "state": state, "file_token": token, "reason": reason})

    for template in templates:
        if template.filename in existing:
            report(template, "exists", "目录中已有同名文件，未覆盖，也未重复保存。")
            continue
        try:
            body = source_path(root, customer, template).read_bytes()
        except OSError:
            report(template, "failed", "模板暂时无法读取，未保存。")
            continue
        try:
            plan = drive.prepare(template.filename, len(body), folder_token)
            for seq in range(plan["block_num"]):
                drive.part(plan, seq, body[seq * plan["block_size"] : (seq + 1) * plan["block_size"]])
        except (UploadRejected, UploadUnknown, ValueError):
            # Nothing is published before finish.
            report(template, "failed", "飞书未接收此文件，目录中没有新增文件。请核对目录权限和剩余空间后重试。")
            continue
        try:
            token = drive.finish(plan)
        except UploadRejected:
            report(template, "failed", "飞书拒绝保存此文件，目录中没有新增文件。请核对目录权限和剩余空间后重试。")
            continue
        except UploadUnknown:
            report(template, "unknown", "飞书尚未确认是否已保存。请先在飞书目录中查看；再次保存时会先核对同名文件，不会重复保存。")
            continue
        existing.add(template.filename)
        report(template, "saved", None, token)
    return results
