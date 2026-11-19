import hashlib
import io
import re
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


def _rows(name: str) -> list[list[str]]:
    """Markdown table rows of one rule document, cells split on unescaped pipes."""
    text = (ASSETS / name).read_text(encoding="utf-8")
    return [[cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip()[1:-1])] for line in text.splitlines() if line.strip().startswith("|") and line.strip().endswith("|")]


def _bare(cell: str) -> str:
    return re.sub(r"[*`]", "", cell).strip()


def test_packaged_rules_match_the_rule_documents_they_were_extracted_from():
    l1, l2 = rule_source("l1.json"), rule_source("l2.json")
    sources = [(spec["source"], spec["source_sha256"]) for spec in l1["specs"].values()] + [(l2["source"], l2["source_sha256"]), (l2["basis"]["source"], l2["basis"]["source_sha256"])]
    drift = [name for name, digest in sources if hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() != digest]
    assert drift == [], f"rule documents changed since extraction, re-extract l1.json / l2.json: {drift}"
    assert set(l1["routing"]) | set(l2["entries"]) == set(ENTRIES)
    for spec, checks in L1.items():
        assert set(checks) <= {item["criterion_id"] for item in l1["specs"][spec]["criteria"]}
    assert set(L2) <= {item["id"] for rows in l2["entries"].values() for item in rows} | {item["id"] for rows in l2["basis"]["entries"].values() for item in rows}
    # Every packaged criterion is a table row of its source document with the same id and name.
    for spec in l1["specs"].values():
        rows = {(_bare(row[0]), row[1]) for row in _rows(spec["source"]) if len(row) > 1}
        assert [item["criterion_id"] for item in spec["criteria"] if (item["criterion_id"], item["criterion"]) not in rows] == [], spec["source"]


def test_the_rule_package_has_the_scale_its_documents_state():
    """README 研发实现说明 §二: 41 entries (UAES 19 / 理想 17 / 蔚来 5), 155 required_structure, 27 specs."""
    names = sorted(path.name for path in ASSETS.glob("PPAP-*.md"))
    outline = [name for name in names if name.startswith("PPAP-00_")]
    routing = [name for name in names if name.startswith("PPAP-00-A_")]
    assert outline == ["PPAP-00_数据标准化预检总纲_v0.21.md"] and routing == ["PPAP-00-A_客户与文件类型覆盖矩阵_路由表_v1.13.md"]
    specs = [name for name in names if not name.startswith("PPAP-00")]
    assert len(specs) == 27
    # 总纲 附录 A lists exactly these 27 spec files.
    appendix = {_bare(row[1]) for row in _rows(outline[0]) if len(row) > 2 and re.fullmatch(r"`PPAP-(?!00)[^`]+\.md`", row[1])}
    assert appendix == set(specs)

    entries = {row[0]: row for row in _rows(routing[0]) if re.fullmatch(r"[ULN]\d{2}", row[0]) and len(row) == 7}
    assert len(entries) == 41
    assert [sum(key.startswith(prefix) for key in entries) for prefix in "ULN"] == [19, 17, 5]
    layers = {key: _bare(row[4]) for key, row in entries.items()}
    assert sorted(layers.values()).count("L1") == 19 and sorted(layers.values()).count("L2") == 22
    customers = {"U": "UAES", "L": "理想", "N": "蔚来"}
    assert {key: (customers[key[0]], _bare(row[2])) for key, row in entries.items()} == {key: (customer, names[0]) for key, (customer, names) in ENTRIES.items()}

    l1, l2 = rule_source("l1.json"), rule_source("l2.json")
    assert l1["routing"] == {key: row[3] for key, row in entries.items() if layers[key] == "L1"}
    assert set(l2["entries"]) == {key for key, layer in layers.items() if layer == "L2"}
    assert sum(len(rows) for rows in l2["entries"].values()) == 155
    # 总纲 附录 A.2: the basis criteria outside the 155 (PPAP-T12 S8 for U12, S9 for U12 / L12 / N04; 10 分则 M-15 / M-16).
    assert {key: [item["id"] for item in rows] for key, rows in l2["basis"]["entries"].items()} == {"U12": ["U12:S8", "U12:S9"], "L12": ["L12:S9"], "N04": ["N04:S9"]}
    counts = {spec: len(item["criteria"]) for spec, item in l1["specs"].items()}
    # 10 分则 §6: P 8 + C 25 + M 16 + X 9 = 58; 09 分则 §7.6: 21 项判定基线 (R13 / R14 audit-only, R24 an exemption).
    assert counts == {"01-1": 35, "01-2": 51, "02-1": 33, "02-2": 37, "02-3": 36, "03": 40, "06": 21, "07": 16, "09": 21, "10": 58}


def test_recognized_customer_and_type_route_to_one_entry():
    assert entry_key("UAES", "E03") == "U09"
    assert entry_key("UAES", "PFMEA") == "U07"
    assert entry_key("理想", "控制计划") == "L08"
    assert entry_key("蔚来", "全功能检验报告") == "N01"
    assert entry_key("蔚来", "T01a") == entry_key("蔚来", "测量报告") == "N01"
    assert entry_key("蔚来", "T01") is None and entry_key("理想", "T01") == "L01"
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
    assert "按文件类型推定" in photo["U13:S1"]["evidence"]
    # PPAP-00-A v1.13 U13 S1: a single-page pure-image PDF also counts and only a file the user named is B03.
    pdf = results(b"%PDF", "照片.pdf", "U13")["U13:S1"]
    assert (pdf["result"], pdf["code"]) == ("未执行", None) and NOT_IMPLEMENTED in pdf["reason"]
    # Only a file the user chose as a sample photo by hand and that is not an image is B03; recognition alone is not.
    sheet = xlsx(["样品照片"])
    assert precheck_content(sheet, "照片.xlsx", "U13")[0]["result"] == "未执行"
    named = precheck_content(sheet, "照片.xlsx", "U13", named=True)[0]
    assert (named["result"], named["code"]) == ("不满足", "B03") and "不是图片" in named["reason"]
    assert precheck_content(b"\x89PNG", "照片.png", "U13", named=True)[0]["result"] == "满足"
    assert precheck_content(b"%PDF", "照片.pdf", "U13", named=True)[0]["result"] == "未执行"
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
    # 检具验收报告 (T05) is outside 蔚来's type set M: S01, never S02 (总纲 v0.21 §3.1).
    assert [(item["code"], item["scope"], item["file_version_id"]) for item in result["statuses"]] == [("S01", "content", "gauge")]
    levels = {item["file_version_id"]: (item["level"], item["entry_key"]) for item in result["files"]}
    assert levels == {"cp": ("block", "L08"), "gauge": ("ok", None)}
    assert result["files"][0]["criteria"] == content["cp"] and result["conclusion"] == "fail"
    assert entry_criteria("L08")[0].startswith("PPAP-10")


def test_an_asterisk_marking_a_special_characteristic_is_not_a_template_placeholder():
    # PPAP-07 PRE-09 c: "＊" left from a template blocks; under 特别特性 it marks the row (sample F01R10Y776 目录 sheet).
    marked = xlsx(("NO", "工程名", "规格", "特别特性"), ("", "", "", "产品"), (1, "2#电机舱直径", "Φ34.22±0.04", "*"), (2, "6#轴孔直径", "Φ8.12±0.0125", "*"))
    assert results(marked, "cpk.xlsx", "U06")["PRE-09"]["result"] == "满足"
    left = xlsx(("NO", "工程名", "Cpk"), (1, "2#电机舱直径", "*"))
    outcome = results(left, "cpk.xlsx", "U06")["PRE-09"]
    assert (outcome["result"], outcome["code"]) == ("不满足", "B05")


def test_uaes_material_fields_accept_the_aliases_of_the_t12_field_table():
    # PPAP-T12 §6: 物料名称 also as 产品名称; 物料编号 only as 物料号 / 物料编码 / 料号 (零件号 is not one).
    table = xlsx(("序号", "产品名称/零件号", "特殊特性名称", "特性值", "特性类别"), (1, "F01RD0YA41毛坯壳体", "3#孔径", "4XΦ7.1", "3"))
    found = results(table, "特殊特性清单.xlsx", "U12")
    assert found["U12:S1"]["result"] == "满足"
    assert (found["U12:S2"]["result"], found["U12:S2"]["code"]) == ("不满足", "B05")


def test_criteria_new_in_the_package_are_listed_and_not_decided():
    """10 分则 v2.6 M-15 / M-16 and the 总纲 附录 A.2 basis criteria PPAP-T12 S8 / S9 are not implemented."""
    head = ["工序号", "工序名称", "生产设备", "特性分类", "产品特性", "过程特性", "规格/公差", "评价测量技术", "样本容量", "频次", "控制方法", "反应计划"]
    cp = results(xlsx(head, ["10", "下料", "锯床", "", "长度", "", "100±1", "卷尺", "1", "每班", "首检", "隔离"]), "CP.xlsx", "L08")
    assert [(cp[cid]["result"], cp[cid]["reason"]) for cid in ("M-15", "M-16")] == [("未执行", NOT_IMPLEMENTED)] * 2
    # M-05 v2.1: 「整列空白」 needs the whole-file count and the four false-empty exclusions; an empty column is not B05 here.
    assert (cp["M-05"]["result"], cp["M-05"]["code"]) == ("未执行", None) and NOT_IMPLEMENTED in cp["M-05"]["reason"]
    table = results(xlsx(["物料名称", "壳体", "物料编号", "F01"], ["序号", "产品特性", "特性值", "特性类型"], [1, "外径", "10±0.1", "SC"]), "特殊特性.xlsx", "U12")
    assert [(cid, table[cid]["result"], table[cid]["source"]) for cid in ("U12:S8", "U12:S9")] == [(cid, "未执行", "PPAP-T12_特殊特性清单_预检规则_v1.10.md") for cid in ("U12:S8", "U12:S9")]
    assert table["U12:S1"]["source"].startswith("PPAP-00-A_")
    assert "L12:S9" in results(xlsx(["CCSC特性管理清单"]), "特殊特性.xlsx", "L12") and "N04:S8" not in results(xlsx(["特殊特性清单"]), "特殊特性.xlsx", "N04")


def test_a_criterion_that_needs_image_recognition_is_marked_unreadable_not_left_undone():
    workbook = Workbook()
    workbook.active.append(["过程流程图"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    results = precheck_content(buffer.getvalue(), "流程图.xlsx", "U16", customer="UAES")
    image_bound = [item for item in results if item["result"] == "未执行" and NEEDS_IMAGE in item["reason"]]
    assert image_bound and all(item["unread"] for item in image_bound)
