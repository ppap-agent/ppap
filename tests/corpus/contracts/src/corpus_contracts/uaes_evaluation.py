"""UAES 评测集（V3.33 产品资料包金标）的只读载入。

工作簿是此批交付指定的金标，与 ``uaes-oracle.csv`` 是两套评价来源，互不覆盖。
标准答案列原样返回，不做归一化。存储地址按本资产包版本声明的前缀映射到
``assets/uaes/samples`` 下已有的同内容原件，不把工作簿路径当作磁盘位置。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from openpyxl import load_workbook

from corpus_contracts.oracle import CORPUS, REPO_ROOT

WORKBOOK = CORPUS / "UAES 评测集.xlsx"
# 固定可信版本：V3.33 产品资料包（2026-11-18）卷二 03_评测集。
WORKBOOK_SHA256 = "73ce28d3f031c8a803c470497bcf2a8e42aa039381e702a11fa054c598362a0e"
SAMPLES = REPO_ROOT / "assets/uaes/samples"
# 工作簿写旧目录 02_受审文件；资料包实际目录为 05_受审文件，零件目录以下结构相同。
SOURCE_PREFIX = "../02_受审文件/"
DEFECT_DIR = "缺陷样本(-test-)"
# 资料包里与仓库已有原件内容相同、但仓库未另存的文件名（A40 的两份 CPK 内容相同），按内容摘要钉住。
RENAMED = {
    "F01RD0YA40_机加工壳体/07 过程能力研究/A40 CPK-机加工.xlsx": (
        "F01RD0YA40_机加工壳体/测试文件/07 过程能力研究/A40-机加工 (2).xlsx",
        "5a043fb788d7d7fd4893de4dda98448437c5d7a43abd503baad586e121f8fcd9",
    ),
}

_KEYS = ("序号", "文件名称", "文件类型", "文件格式", "存储地址")
PRECHECK = ("预检统计表", (*_KEYS,), "标准答案", ("预检结果", "阻断数", "警告数", "问题", "整改动作", "项目", "客户", "车型", "零件号", "零件名称"))
AUDIT = ("审核明细", (*_KEYS, "规则编号", "规则详情（查什么 / 查哪里）"), "标准答案", ("审核结果", "问题等级", "审核依据", "整改建议"))


@dataclass(frozen=True)
class EvaluationRow:
    sheet: str
    excel_row: int
    keys: dict[str, Any]
    answers: dict[str, Any]
    source: Path

    @property
    def is_defect(self) -> bool:
        return DEFECT_DIR in self.source.relative_to(SAMPLES).parts


def resolve_source(address: str) -> Path:
    """工作簿存储地址 → 仓库原件；只接受声明前缀下的零件目录相对路径。"""
    if not isinstance(address, str) or not address.startswith(SOURCE_PREFIX) or "\\" in address:
        raise ValueError(f"存储地址不在评测集声明的前缀下：{address!r}")
    relative = PurePosixPath(address.removeprefix(SOURCE_PREFIX))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts) or len(relative.parts) < 3:
        raise ValueError(f"存储地址不是零件目录下的文件：{address!r}")
    part, *rest = relative.parts
    if str(relative) in RENAMED:
        target, digest = RENAMED[str(relative)]
        path = SAMPLES / target
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"改名原件内容与资料包不同：{address!r}")
    elif rest[0] == DEFECT_DIR:
        path = SAMPLES.joinpath(part, *rest)
    else:
        path = SAMPLES.joinpath(part, "测试文件", *rest)
    if not path.resolve().is_relative_to(SAMPLES.resolve()) or not path.is_file():
        raise ValueError(f"存储地址在仓库样本中找不到：{address!r}")
    return path


def _sheet(workbook: Any, spec: tuple[str, tuple[str, ...], str, tuple[str, ...]]) -> list[EvaluationRow]:
    name, keys, group, answers = spec
    sheet = workbook[name]
    top = [cell.value for cell in sheet[1]]
    sub = [cell.value for cell in sheet[2]]
    expected_top = [*keys, group, *[None] * (len(answers) - 1)]
    expected_sub = [*[None] * len(keys), *answers]
    if top != expected_top or sub != expected_sub:
        raise ValueError(f"{name} 双层表头与评测集约定不同")
    rows = []
    for index, values in enumerate(sheet.iter_rows(min_row=3, values_only=True), start=3):
        if all(value is None for value in values):
            continue
        if len(values) != len(keys) + len(answers):
            raise ValueError(f"{name} 第 {index} 行列数不对")
        key = dict(zip(keys, values[: len(keys)], strict=True))
        rows.append(EvaluationRow(name, index, key, dict(zip(answers, values[len(keys) :], strict=True)), resolve_source(key["存储地址"])))
    return rows


@lru_cache(maxsize=1)
def load_evaluation(path: Path = WORKBOOK) -> tuple[tuple[EvaluationRow, ...], tuple[EvaluationRow, ...]]:
    """(预检统计表, 审核明细) 的数据行；双层表头不计入。"""
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != WORKBOOK_SHA256:
        raise ValueError("评测集工作簿不是已固定的 V3.33 版本")
    workbook = load_workbook(path, read_only=False, data_only=False)
    return tuple(_sheet(workbook, PRECHECK)), tuple(_sheet(workbook, AUDIT))


__all__ = ["AUDIT", "PRECHECK", "RENAMED", "SAMPLES", "SOURCE_PREFIX", "WORKBOOK", "EvaluationRow", "load_evaluation", "resolve_source"]
