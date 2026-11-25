"""Independent U08 workbooks through the frozen runner. The fixture supplies only literal reading facts."""

import json
from types import SimpleNamespace

from corpus_contracts.uaes_r4_fixture import book, check


def reading(monkeypatch, facts):
    calls = []

    def run(task, *, prompt, **_):
        rows = json.loads(prompt)
        calls.append((task, rows))
        return SimpleNamespace(output={"complete": True, "read_rows": sorted({k.split("|")[0] for k in rows}), "facts": facts(task, rows)})

    monkeypatch.setattr("documents.ai.run_json", run)
    return calls


def test_warehouse_and_shipping_can_express_actual_order_in_different_words(tmp_path, monkeypatch):
    heading = ["工序号", "过程名称/操作描述", "产品/过程规格/公差", "控制方法", "评价/测量技术"]
    a = "较早收货的批料优先领用。"
    b = "先把较早入库的批次交付，再发运其余批次。"
    source = book(tmp_path / "流程.xlsx", {"控制计划": [heading, [10, "进料", "序列号", None, "扫码"], [20, "仓储", "批次号", a, "扫码"], [30, "发运", "流转卡", b, "扫码"]]})
    calls = reading(monkeypatch, lambda task, data: [{"source": k, "quote": v, "kind": "oldest_first"} for k, v in data.items() if v in (a, b)])
    out = check(source, "U08", "CP:2")
    assert out["status"] == "PASS" and all(text in out["suggestions"] for text in (a, b)), out
    assert len(calls) == 1


def test_separate_pg_items_and_binding_to_the_same_cp_row(tmp_path, monkeypatch):
    part = "F010000001"
    cp = book(
        tmp_path / "控制.xlsx",
        {
            "控制计划": [
                ["零件号", part],
                ["工序号", "过程名称/操作描述", "产品", "过程", "产品/过程规格/公差", "评价/测量技术"],
                [10, "例行审核", "扭转载荷试验", None, "在额定载荷下保持十分钟", "扭矩台"],
            ]
        },
    )
    pg = book(
        tmp_path / "采购.xlsx",
        {"PGQP1": [["零件号", part], ["5. 产品审核要求"], ["项目", "内容描述"], ["年度尺寸复核", "只核尺寸，不属于型式验证或年度材质检测。"], ["扭转载荷试验", "每轮型式验证均进行此项。"]]},
    )
    names = ["年度尺寸复核", "扭转载荷试验"]

    def facts(task, data):
        out = []
        for k, v in data.items():
            prefix = k.split("|")[0] + "|"
            if task == "cp_product_audit_item" and k.endswith("|内容"):
                name = data[prefix + "项目"]
                out.append({"source": k, "quote": v, "name": name, "translation": None, "kind": "type_test" if name == names[1] else "dimension_only_excluded"})
            if task == "cp_test_application" and k.endswith(("|CP产品特性", "|CP规格公差")):
                out.append({"source": k, "quote": v, "name": names[1], "kind": "test_named" if k.endswith("|CP产品特性") else "test_instruction"})
        return out

    calls = reading(monkeypatch, facts)
    out = check(cp, "U08", "CP:10", {"E12": [pg]})
    assert out["status"] == "PASS" and "只核尺寸" in out["suggestions"] and "额定载荷" in out["suggestions"], out
    assert [task for task, _ in calls] == ["cp_product_audit_item", "cp_test_application"]
