"""L13 conditions apply to every process row, including a missing device."""

import io

import pytest
from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.presets import preset_rule_body
from openpyxl import Workbook
from product import run_round_from_bytes


@pytest.mark.parametrize(
    ("change", "expected"),
    [(None, ["PASS", "PASS"]), ("supplier", ["NG", "PASS"]), ("result", ["PASS", "NG"]), ("remediation", ["PASS", "NG"]), ("device", ["NG", "PASS"]), ("process", ["UNABLE", "NG"])],
)
def test_process_is_the_source_condition_for_development_and_acceptance(change, expected):
    book = Workbook()
    sheet = book.active
    sheet.title = "设备开发验收清单"
    sheet.append(["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "设备验收", "验收结果", "整改措施", "整改计划"])
    sheet.append(
        [
            None if change == "process" else "10 压装",
            "开发",
            None if change == "device" else "压装机",
            None if change == "supplier" else "供应商",
            "M1",
            0,
            1,
            "专用",
            "2026-01-01",
            "验收",
            None if change == "result" else "条件通过",
            None if change == "remediation" else "增加防护",
            "2026-02-01",
        ]
    )
    content = io.BytesIO()
    book.save(content)
    book.close()
    body = preset_rule_body("理想", "L13")
    target = RoundInput("target", "file", "version", "设备清单.xlsx", "ready", rule_version_id="li-l13", rule_body=body, rule_execution=freeze_target_rule_execution(body, "设备清单.xlsx"))
    result = run_round_from_bytes(RoundWork("round", "tenant", [target]), {"version": content.getvalue()})
    assert [row["status"] for row in result["findings"]] == expected, [(row["status"], row["suggestions"]) for row in result["findings"]]


@pytest.mark.parametrize(("column", "expected"), [("D3", ["NG", "PASS"]), ("K3", ["PASS", "NG"])])
def test_merged_process_still_checks_each_device_row(column, expected):
    book = Workbook()
    sheet = book.active
    sheet.title = "设备开发验收清单"
    sheet.append(["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "设备验收", "验收结果", "整改措施", "整改计划"])
    for process, device in [("10 压装", "压装机A"), (None, "压装机B")]:
        sheet.append([process, "开发", device, "供应商", "M1", 0, 1, "专用", "2026-01-01", "验收", "通过", None, None])
    sheet.merge_cells("A2:A3")
    sheet[column] = None
    content = io.BytesIO()
    book.save(content)
    book.close()
    body = preset_rule_body("理想", "L13")
    target = RoundInput("target", "file", "version", "设备清单.xlsx", "ready", rule_version_id="li-l13", rule_body=body, rule_execution=freeze_target_rule_execution(body, "设备清单.xlsx"))
    result = run_round_from_bytes(RoundWork("round", "tenant", [target]), {"version": content.getvalue()})
    assert [row["status"] for row in result["findings"]] == expected, [(row["status"], row["suggestions"]) for row in result["findings"]]
