"""NIO source facts, including pair uncertainty, through the real Round engine."""

import io
import json

import pytest
from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.presets import preset_rule, preset_rule_body
from openpyxl import Workbook
from product import run_round_from_bytes


def serialized(book):
    content = io.BytesIO()
    book.save(content)
    book.close()
    return content.getvalue()


def pfmea(*, process="10 成型", characteristic="CC", part="P12345", revision="A", severity=6, detection=6, missing_header=False, function=None, start="2026-01-01"):
    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    for label, value in [
        ("公司名称", "供应商"),
        ("车型项目", "项目"),
        ("零件名称", "支架"),
        ("零件号", part),
        ("版本", revision),
        ("客户名称", "蔚来"),
        ("制造地址", None if missing_header else "上海"),
        ("PFMEA开始日期", start),
        ("PFMEA修订日期", "2026-02-01"),
        ("跨职能团队", "质量/制造"),
    ]:
        sheet.append([label, value])
    sheet.append(["过程步骤", "特殊特性", "过程项功能", "失效影响", "严重度", "现行探测控制", "探测度"])
    sheet.append(
        [
            process,
            characteristic,
            function if function is not None else "内部：压装\n客户工厂：装车\n最终用户：乘坐",
            "内部：返工（4）\n客户工厂：返工（6）\n最终用户：异响（5）",
            severity,
            "双感官检查（6）\n目视检查（8）",
            detection,
        ]
    )
    history = book.create_sheet("变更履历")
    history.append(["修订日期"])
    history.append(["2026-01-01"])
    history.append(["2026-02-01"])
    return serialized(book)


def pfd(*, part="P12345", revision="A", customer="蔚来", process="成型", mark="CC"):
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet.append(["零件号", part, "版本", revision])
    sheet.append(["客户名称", customer])
    sheet.append(["工序号", "工序名称", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别"])
    sheet.append([10, process, "孔径", mark, "温度", "SC"])
    return serialized(book)


def execute(target_content, references):
    body = preset_rule_body("蔚来", "N02")
    target = RoundInput("target", "target-file", "target-version", "PFMEA.xlsx", "ready", rule_version_id="nio-pfmea", rule_body=body, rule_execution=freeze_target_rule_execution(body, "PFMEA.xlsx"))
    inputs = [target]
    blobs = {"target-version": target_content}
    for index, content in enumerate(references):
        version = f"pfd-{index}"
        inputs.append(RoundInput("reference", version, version, "过程流程图.xlsx", "ready"))
        if content is not None:
            blobs[version] = content
    result = run_round_from_bytes(RoundWork("round", "tenant", inputs), blobs)
    findings = {row["item_key"].split(":rule:", 1)[1]: row for row in result["findings"]}
    return [findings.get(clause.id) for clause in preset_rule("蔚来", "N02").clauses], result


def test_nio_source_rules_execute_and_preserve_both_originals():
    findings, result = execute(pfmea(), [pfd()])
    assert [row["status"] if row else None for row in findings] == ["PASS"] * 7
    assert all(row["coverage"] == "mapped" for row in result["coverage"]["rule_items"])
    assert result["coverage"]["unchecked"] == ["过程流程图.xlsx（参考，未作为待审完整检查）"]
    sources = findings[5]["locator"]["evidence"]["sources"]
    assert {json.loads(row["locator"])["file_version_id"] for row in sources} == {"target-version", "pfd-0"}
    assert any("成型" in row["excerpt"] for row in sources)


@pytest.mark.parametrize(
    ("kwargs", "rule_index"),
    [
        ({"missing_header": True}, 0),
        ({"start": "2026-01-02"}, 1),
        ({"function": "S：作用 O：工厂 C：客户"}, 2),
        ({"process": "10 检验"}, 5),
        ({"process": "101 成型"}, 5),
        ({"characteristic": "KPC"}, 6),
        ({"severity": 5}, 3),
        ({"detection": 8}, 4),
    ],
)
def test_nio_source_controlled_defects(kwargs, rule_index):
    findings, _ = execute(pfmea(**kwargs), [pfd()])
    assert findings[rule_index]["status"] == "NG"


@pytest.mark.parametrize("references", [[], [pfd(), pfd()], [pfd(part="OTHER")], [pfd(part=None)], [pfd(revision="B")], [pfd(customer="理想")]])
def test_uncertain_pair_is_not_a_definitive_supplier_conflict(references):
    findings, _ = execute(pfmea(), references)
    assert [findings[index]["status"] for index in [5, 6]] == ["UNABLE", "UNABLE"]
    assert all(findings[index]["status"] == "PASS" for index in range(5))


@pytest.mark.parametrize("reference", [None, b"broken workbook"], ids=["unavailable", "damaged"])
def test_provided_but_unreadable_pair_is_an_execution_error(reference):
    findings, _ = execute(pfmea(), [reference])
    assert [findings[index]["status"] for index in [5, 6]] == ["ERR", "ERR"]
    assert all(findings[index]["status"] == "PASS" for index in range(5))
