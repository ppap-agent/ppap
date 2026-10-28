"""Throwaway research inputs. Read originals; never change corpus or its oracle."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

RULE_TEXTS = {
    "tolerance": (
        "检查“尺寸”区域的数据，当“检查数据”中的数值不超过对应行的“判定标准”内容，且对应行的“判定结果”为OK或合格则通过；否则不通过。"
        "注意：结果判定PASS或正确不能等同于OK或合格，需严格按照文字要求，另外不关注大小写。"
    ),
    "duplicates": "检查“尺寸”区域数据，当“检查数据”中同行的数据中出现两个或两个以上数值一致则不通过，否则通过",
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _read_case(repo: Path, name: str, case_id: str, family: str, first: int, last: int, expected: dict, business_rows: list[int]) -> dict:
    relative = Path("assets/v2.0plus/fallback") / name
    source = repo / relative
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    workbook = load_workbook(source, data_only=True)
    sheet = workbook.active
    observations = []
    for row in sheet.iter_rows(min_row=first, max_row=last, min_col=1, max_col=13):
        for cell in row:
            if cell.value is not None:
                observations.append({"id": cell.coordinate, "cell": cell.coordinate, "row": cell.row, "column": get_column_letter(cell.column), "value": cell.value})
    merges = [
        {"min_row": item.min_row, "max_row": item.max_row, "min_col": item.min_col, "max_col": item.max_col} for item in sheet.merged_cells.ranges if item.min_row >= first and item.max_row <= last
    ]
    images = []
    for item in sheet._images:
        anchor = getattr(item.anchor, "_from", None)
        if anchor is not None and first <= anchor.row + 1 <= last:
            images.append({"anchor_row": anchor.row + 1, "anchor_column": get_column_letter(anchor.col + 1), "content_observed": False})
    workbook.close()
    case = {
        "id": case_id,
        "family": family,
        "source_path": str(relative),
        "source_sha256": sha,
        "derivation_root": "d98ba7ac6fae9e9ac347ec016a3a285b5d3351d3063161779a3beb17cc9777a0" if family == "jifeng" else sha,
        "scope": f"{sheet.title}!A{first}:M{last}",
        "sheet": sheet.title,
        "observations": observations,
        "merged_ranges": merges,
        "images": images,
        "observed_modalities": ["native_cell_values", "merged_cell_ranges"],
        "unobserved_modalities": ["embedded_image_contents", "visual_styling", "outside_selected_region"],
        "empty_cells": "Within the declared region, unlisted native cells are empty or merged children. This does not prove the document lacks an image/value.",
        "expected": expected,
        "business_rows": business_rows,
        "rule_texts": RULE_TEXTS,
        "annotation": {"source": "agent_source_inspection", "human_verified": False, "scope": "Selected native-cell dimension region only; not full-document audit quality."},
        "rule_source": "assets/V2.0测试/规则V2.0/01 全功能检验报告.docx",
        "split": "development_probe_not_holdout",
    }
    case["observation_sha256"] = _digest(observations)
    return case


def build_cases(repo_root: Path | str) -> list[dict]:
    repo = Path(repo_root)
    jifeng_expected = {"headers": [51], "records": list(range(45, 51)), "specification": ["D", "E"], "measured": ["H", "I", "J", "K", "L"], "judgment": ["M"]}
    jifeng = _read_case(repo, "1-全功能检验报告_继峰.xlsx", "jifeng", "jifeng", 44, 51, jifeng_expected, [50])
    yada = _read_case(
        repo,
        "1-全功能检验报告_上海亚大.xlsx",
        "yada",
        "yada",
        33,
        38,
        {"headers": [38], "records": [34, 35, 36, 37], "specification": ["E", "F"], "measured": ["I", "J", "K"], "judgment": ["L", "M"]},
        [34, 35, 36],
    )
    error = _read_case(repo, "1-全功能检验报告_继峰 - 错误.xlsx", "jifeng_error", "jifeng", 44, 51, copy.deepcopy(jifeng_expected), [50])
    shifted = copy.deepcopy(jifeng)
    shifted["id"] = "jifeng_shifted"
    shifted["derivation"] = {"kind": "synthetic_observation_coordinate_translation", "rows": 7, "columns": 2, "not_a_workbook_conversion": True, "content_change": False}
    shifted["scope"] = "表单!C51:O58"
    for item in shifted["observations"]:
        item["row"] += 7
        item["column"] = get_column_letter(column_index_from_string(item["column"]) + 2)
        item["cell"] = item["id"] = f"{item['column']}{item['row']}"
    for item in shifted["merged_ranges"]:
        item["min_row"] += 7
        item["max_row"] += 7
        item["min_col"] += 2
        item["max_col"] += 2
    for item in shifted["images"]:
        item["anchor_row"] += 7
        item["anchor_column"] = get_column_letter(column_index_from_string(item["anchor_column"]) + 2)
    for name, values in shifted["expected"].items():
        shifted["expected"][name] = [v + 7 for v in values] if name in {"headers", "records"} else [get_column_letter(column_index_from_string(v) + 2) for v in values]
    shifted["business_rows"] = [r + 7 for r in shifted["business_rows"]]
    shifted["observation_sha256"] = _digest(shifted["observations"])
    return [jifeng, yada, error, shifted]


if __name__ == "__main__":
    import sys

    for case in build_cases(sys.argv[1]):
        print(json.dumps({k: case[k] for k in ("id", "family", "source_sha256", "scope", "expected")}, ensure_ascii=False))
