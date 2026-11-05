"""Compile product-owned DOCX rule text for shipping with audit wheels.

This performs source extraction only. It neither generates Checker code nor
claims that an extracted clause is implemented. Run --check in Corpus tests.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/规则"
OUTPUT = ROOT / "packages/audit/src/audit/preset_rules.json"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
# Explicit source identity. Sorting filenames would swap L02/L03 and U07/U08.
FILES = {
    "UAES": [
        ("E01", "初始样品检验报告审核规则.docx"),
        ("E02", "测量比较报告审核规则.docx"),
        ("E04", "实验室资质证书审核规则.docx"),
        ("T04", "材料产品及分供清单审核规则.docx"),
        ("T07", "测量系统分析评价审核规则.docx"),
        ("T08", "过程能力研究审核规则.docx"),
        ("T10", "潜在过程失效模式及后果分析（PFMEA）审核规则.docx"),
        ("T11", "控制计划（CP）审核规则.docx"),
        ("E03", "包装方案审核规则.docx"),
        ("E05", "材料规范审核规则.docx"),
        ("E06", "样品阶段履历审核规则.docx"),
        ("T12", "特殊特性清单审核规则.docx"),
        ("E07", "生产件样品照片审核规则.docx"),
        ("E08", "订货规范审核规则.docx"),
        ("E09", "试装单审核规则.docx"),
        ("T09", "过程流程图（PFD）审核规则.docx"),
        ("T17", "零件提交保证书（PSW）审核规则.docx"),
        ("E10", "零件追溯说明审核规则.docx"),
        ("E11", "顾客工程批准审核规则.docx"),
    ],
    "理想": [
        ("T01", "01 全功能检验报告.docx"),
        ("T02", "02 零部件设计验证计划和报告.docx"),
        ("T03", "02 零部件设计验证报告（DV报告_PV报告_试验报告）.docx"),
        ("T04", "03 材料产品及分供清单.docx"),
        ("T07", "06 测量系统分析评价.docx"),
        ("T08", "07 过程能力研究.docx"),
        ("T10", "09 潜在过程失效模式及后果分析.docx"),
        ("T11", "10 控制计划.docx"),
        ("T05", "04 检具验收报告.docx"),
        ("T06", "05 模具验收报告.docx"),
        ("T09", "08 过程流程图.docx"),
        ("T12", "11 过程特殊特性清单.docx"),
        ("T13", "12 设备清单.docx"),
        ("T14", "13 生产系统产能评估报告.docx"),
        ("T15", "14 APQP项目问题清单.docx"),
        ("T16", "15 零部件年度试验要求.docx"),
        ("T17", "16 零件提交保证书.docx"),
    ],
    "蔚来": [
        ("T01", "1-2规则-测量报告.docx"),
        ("T10", "9规则-潜在过程失效模式及后果分析（PFMEA）.docx"),
        ("T11", "10规则-控制计划.docx"),
        ("T12", "11规则-特殊特性清单.docx"),
        ("T09", "8规则-过程流程图.docx"),
    ],
}


def cell_text(cell: ET.Element) -> str:
    return "\n".join("".join(node.text or "" for node in paragraph.findall(".//w:t", NS)) for paragraph in cell.findall("w:p", NS)).strip()


def assign_clause_ids(entry: str, clauses: list[dict]) -> None:
    # The source number names a clause; a number used by several rows gets its row order.
    uses = Counter(clause["source_number"] for clause in clauses)
    seen: Counter[str] = Counter()
    for clause in clauses:
        number = clause["source_number"]
        seen[number] += 1
        clause["id"] = f"{entry}:{number}" if uses[number] == 1 else f"{entry}:{number}-{seen[number]}"


def compile_presets() -> str:
    result = []
    expected_files = set()
    for customer, entries in FILES.items():
        prefix = {"UAES": "U", "理想": "L", "蔚来": "N"}[customer]
        for index, (kind, filename) in enumerate(entries, 1):
            entry = f"{prefix}{index:02d}"
            path = SOURCES / customer / filename
            expected_files.add(path)
            with ZipFile(path) as archive:
                document = ET.fromstring(archive.read("word/document.xml"))
            clauses = []
            for table_index, table in enumerate(document.findall(".//w:tbl", NS), 1):
                header = [cell_text(cell) for cell in table.findall("w:tr", NS)[0].findall("w:tc", NS)]
                description_column = next((header.index(label) for label in ("规则说明", "审核规则") if label in header), None)
                category_column = header.index("规则简述") if "规则简述" in header else None
                for row_index, row in enumerate(table.findall("w:tr", NS), 1):
                    cells = [cell_text(cell) for cell in row.findall("w:tc", NS)]
                    if not cells or not cells[0].isdigit():
                        continue
                    if description_column is None or len(cells) <= description_column:
                        raise ValueError(f"Invalid rule header: {path.name}:{row_index}")
                    number = cells[0]
                    category = cells[category_column] if category_column is not None else f"条款 {number}"
                    text = cells[description_column]
                    if not text:
                        raise ValueError(f"Empty rule text: {path.name}:{row_index}")
                    clauses.append({"id": "", "source_number": number, "category": category, "text": text, "locator": f"table:{table_index}/row:{row_index}"})
            if not clauses:
                raise ValueError(f"Empty clauses: {path.name}")
            assign_clause_ids(entry, clauses)
            result.append(
                {
                    "customer": customer,
                    "entry_id": entry,
                    "source_type": kind,
                    "level": "L1" if index <= (3 if customer == "蔚来" else 8) else "L2",
                    "title": path.stem,
                    "source_path": str(path.relative_to(ROOT)),
                    "clauses": clauses,
                }
            )
    if set(SOURCES.rglob("*.docx")) != expected_files:
        raise ValueError("Source files changed; reconcile the authoritative 00-A matrix")
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    compiled = compile_presets()
    if args.check:
        if OUTPUT.read_text() != compiled:
            raise SystemExit("V4 preset rule text differs from its product-owned sources")
    else:
        OUTPUT.write_text(compiled)


if __name__ == "__main__":
    main()
