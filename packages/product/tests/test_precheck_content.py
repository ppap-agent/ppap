import hashlib
import io
from pathlib import Path

import docx
from openpyxl import Workbook
from product.precheck import precheck_task
from product.precheck_content import ENTRIES, L1, L2, NEEDS_IMAGE, NO_TEXT, NOT_IMPLEMENTED, entry_criteria, entry_key, precheck_content, rule_source

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "uaes" / "prechecks"


def xlsx(*rows, title="Sheet1") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(list(row))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def word(*paragraphs) -> bytes:
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def results(content: bytes, filename: str, key: str) -> dict[str, dict]:
    return {item["criterion_id"]: item for item in precheck_content(content, filename, key)}


def test_packaged_rules_match_the_rule_documents_they_were_extracted_from():
    l1, l2 = rule_source("l1.json"), rule_source("l2.json")
    sources = [(spec["source"], spec["source_sha256"]) for spec in l1["specs"].values()] + [(l2["source"], l2["source_sha256"])]
    drift = [name for name, digest in sources if hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() != digest]
    assert drift == [], f"rule documents changed since extraction, re-extract l1.json / l2.json: {drift}"
    assert set(l1["routing"]) | set(l2["entries"]) == set(ENTRIES) and len(ENTRIES) == 41
    for spec, checks in L1.items():
        assert set(checks) <= {item["criterion_id"] for item in l1["specs"][spec]["criteria"]}
    assert set(L2) <= {item["id"] for rows in l2["entries"].values() for item in rows}


def test_recognized_customer_and_type_route_to_one_entry():
    assert entry_key("UAES", "E03") == "U09"
    assert entry_key("UAES", "PFMEA") == "U07"
    assert entry_key("理想", "控制计划") == "L08"
    assert entry_key("蔚来", "全功能检验报告") == "N01"
    assert entry_key("蔚来", "检具验收报告") is None
    assert entry_key(None, "控制计划") is None


def test_l2_presence_items_use_the_code_the_routing_table_states():
    found = results(xlsx(["包装方案"], ["零件基础信息"], ["供应商基础信息", "ACME"]), "包装.xlsx", "U09")
    assert found["U09:S1"]["result"] == "满足" and found["U09:S1"]["location"] == "Sheet1!A1"
    assert found["U09:S2"]["result"] == "满足"
    assert (found["U09:S4"]["result"], found["U09:S4"]["code"]) == ("不满足", "W01")
    untitled = results(xlsx(["零件基础信息"]), "包装.xlsx", "U09")
    assert (untitled["U09:S1"]["result"], untitled["U09:S1"]["code"]) == ("不满足", "B05")


def test_field_placeholder_is_empty_and_missing_column_blocks():
    content = xlsx(["物料名称", "壳体", "物料编号", "/"], ["序号", "产品特性", "特性值", "特性类型"], [1, "外径", "10±0.1", "SC"], [2, "内径", "", "CC"])
    found = results(content, "特殊特性.xlsx", "U12")
    assert found["U12:S1"]["result"] == "满足" and found["U12:S1"]["evidence"] == "物料名称=壳体"
    assert (found["U12:S2"]["result"], found["U12:S2"]["code"], found["U12:S2"]["location"]) == ("不满足", "W02", "Sheet1!D1")
    assert found["U12:S3"]["result"] == "满足"
    assert (found["U12:S7"]["result"], found["U12:S7"]["code"]) == ("不满足", "W02")
    assert "Sheet1 第 4 行" in found["U12:S7"]["evidence"]
    without = results(xlsx(["物料名称", "壳体"], ["序号", "产品特性"], [1, "外径"]), "特殊特性.xlsx", "U12")
    assert (without["U12:S5"]["result"], without["U12:S5"]["code"]) == ("不满足", "B05")


def test_l1_control_plan_columns_follow_the_customer_difference_layer():
    head = ["工序号", "工序名称", "生产设备", "特性分类", "产品特性", "过程特性", "规格/公差", "评价测量技术", "样本容量", "频次", "控制方法", "反应计划"]
    full = xlsx(head, ["10", "下料", "锯床", "SC", "长度", "", "100±1", "卷尺", "1", "每班", "首检", "隔离"])
    assert results(full, "CP.xlsx", "L08")["M-04"]["result"] == "满足"
    short = xlsx(head[:-1], ["10", "下料", "锯床", "SC", "长度", "", "100±1", "卷尺", "1", "每班", "首检"])
    m04 = results(short, "CP.xlsx", "L08")["M-04"]
    assert (m04["result"], m04["code"]) == ("不满足", "B05") and "反应计划" in m04["reason"]
    uaes = [column for column in head if column not in {"频次", "控制方法"}]
    found = results(xlsx(uaes, ["10", "下料", "锯床", "SC", "长度", "", "100±1", "卷尺", "1", "隔离"]), "CP.xlsx", "U08")
    assert found["M-04"]["result"] == "满足"
    assert results(full, "CP.xlsx", "L08")["M-14"]["result"] == "S02"


def test_l1_placeholders_are_reported_with_the_spec_code():
    bom = results(xlsx(["零件号", "Q-1", "零件名称", "支架", "供应商", "ACME"], ["序号", "零件号", "零件名称", "单件重量"], [1, "123456", "螺栓", "约12"]), "BOM.xlsx", "U04")
    assert (bom["PC-C2"]["result"], bom["PC-C2"]["code"]) == ("不满足", "B03")
    assert (bom["PC-C3"]["result"], bom["PC-C3"]["code"]) == ("不满足", "B05") and "约12" in bom["PC-C3"]["evidence"]
    assert bom["PC-B1"]["result"] == "满足"
    msa = results(xlsx(["量具编号", "待填"], ["%GRR", "8.5%"]), "MSA.xlsx", "U05")
    assert (msa["MSA-PRE-03"]["result"], msa["MSA-PRE-03"]["code"], msa["MSA-PRE-03"]["location"]) == ("不满足", "W02", "Sheet1!B1")
    assert msa["MSA-PRE-16"]["result"] == "满足"


def test_unimplemented_handoff_and_image_items_are_never_satisfied():
    found = results(xlsx(["零部件设计验证计划和报告"], ["序号", "试验项目"]), "DVP.xlsx", "L02")
    assert {item["result"] for item in found.values()} == {"未执行"} and found["COR-01"]["reason"] == NOT_IMPLEMENTED
    inspection = results(xlsx(["检验报告"]), "检验.xlsx", "U01")
    assert inspection["IR-09"]["result"] == "S03" and inspection["IR-13"]["reason"] == NEEDS_IMAGE
    pfd = results(xlsx(["过程流程图"]), "PFD.xlsx", "U16")
    assert pfd["U16:S4"]["result"] == "未执行" and pfd["U16:S8"]["result"] == "未执行"


def test_word_check_boxes_and_files_without_text():
    content = word("试装单", "流转记录：流程结束 流程完成", "试装结论 ☑通过 □不通过", "检验结论 □合格 ☑不合格")
    found = results(content, "试装单.docx", "U15")
    assert all(found[f"U15:S{n}"]["result"] == "满足" for n in range(1, 6))
    assert found["U15:S6"]["result"] == "S03" and "☑不合格" in found["U15:S6"]["evidence"]
    photo = results(b"\x89PNG", "照片.png", "U13")
    assert photo["U13:S1"]["result"] == "满足" and photo["U13:S2"]["result"] == "S03"
    assert results(b"%PDF", "照片.pdf", "U13")["U13:S1"]["code"] == "B03"
    scanned = results(b"\x89PNG", "包装.png", "U09")
    assert {item["reason"] for item in scanned.values()} == {NO_TEXT}


def test_capacity_formula_mismatch_is_b06():
    content = xlsx(["零件产能评估表"], ["线体/工序类别", "良率", "合格品数", "产出数量"], ["装配", "98%", 98, 100], ["测试", "90%", 95, 100])
    found = results(content, "产能.xlsx", "L14")
    assert (found["L14:S6"]["result"], found["L14:S6"]["code"]) == ("不满足", "B06") and "Sheet1 第 4 行" in found["L14:S6"]["location"]
    assert (found["L14:S7"]["result"], found["L14:S7"]["code"]) == ("不满足", "B05")


def test_task_merges_content_findings_and_registers_uncovered_types():
    def row(id_, customer, doc_type):
        return {
            "file": {"id": id_, "original_filename": f"{id_}.xlsx"},
            "selection": "selected",
            "validation": {"state": "valid"},
            "match": {"state": "matched", "document_type": doc_type, "metadata": {"customer": customer, "part_number": "P1", "part": "壳体"}},
        }

    content = {"cp": [{"criterion_id": "M-04", "criterion": "必列齐备", "result": "不满足", "code": "B05", "location": "Sheet1 第 1 行", "evidence": "已有列：工序号", "reason": "缺必列：反应计划"}]}
    result = precheck_task({"files": [row("cp", "理想", "控制计划"), row("gauge", "蔚来", "检具验收报告")]}, content)
    assert [(item["code"], item["location"]) for item in result["blocks"]] == [("B05", "cp.xlsx › Sheet1 第 1 行")]
    assert [(item["code"], item["scope"], item["file_version_id"]) for item in result["statuses"]] == [("S02", "content", "gauge")]
    levels = {item["file_version_id"]: (item["level"], item["entry_key"]) for item in result["files"]}
    assert levels == {"cp": ("block", "L08"), "gauge": ("ok", None)}
    assert result["files"][0]["criteria"] == content["cp"] and result["conclusion"] == "fail"
    assert entry_criteria("L08")[0].startswith("PPAP-10")
