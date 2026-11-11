"""Independent source facts for UAES's simple records and photo boundary."""

import pytest
from audit.checker_lib.uaes.records import UaesEngineeringHistoryAgent, UaesLabQualificationAgent, UaesPackagingAgent, UaesPhotoAgent, UaesTraceabilityAgent, UaesTrialAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook
from PIL import Image


def checked(tmp_path, agent_cls, entry, rows, *, title="原件"):
    book = Workbook()
    book.active.title = title
    for row in rows:
        book.active.append(row)
    path = tmp_path / "source.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", entry)
    agent = agent_cls(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check(0)


@pytest.mark.parametrize("missing", [None, "包装材料清单"])
def test_packaging_uses_original_content_not_the_filename(tmp_path, missing):
    sections = ["包装方案", "零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"]
    result = checked(tmp_path, UaesPackagingAgent, "U09", [[section] for section in sections if section != missing])
    assert result.status == ("PASS" if missing is None else "NG")
    assert result.evidence and result.evidence.sources


def test_engineering_history_accepts_declared_business_aliases(tmp_path):
    result = checked(tmp_path, UaesEngineeringHistoryAgent, "U11", [["工程更改一览表"], ["项目代号", "物料编号", "更改内容", "更改原因", "更改日期"]], title="工程更改")
    assert result.status == "PASS"


def test_traceability_area_labels_do_not_replace_their_content(tmp_path):
    labels = ["基本信息", "分供方追溯", "生产过程追溯", "交付追溯", "追溯状态总结"]
    result = checked(tmp_path, UaesTraceabilityAgent, "U18", [["供应商零件追溯"]] + [[label, None if label == "分供方追溯" else "实际追溯内容"] for label in labels], title="供应商零件追溯")
    assert result.status == "UNABLE"


@pytest.mark.parametrize(("date_text", "expected"), [("2099-12-31", "UNABLE"), ("2000-01-01", "NG"), ("not readable", "UNABLE")])
def test_laboratory_expiry_uses_the_actual_deadline(tmp_path, date_text, expected):
    result = checked(tmp_path, UaesLabQualificationAgent, "U03", [["中国合格评定国家认可委员会"], ["CNAS"], ["截止日期", date_text]])
    assert result.status == expected


@pytest.mark.parametrize(("trial", "inspection", "expected"), [("☑通过", "☑合格", "PASS"), ("☑未通过", "☑合格", "NG"), ("通过", "合格", "UNABLE")])
def test_trial_requires_actual_unambiguous_selections(tmp_path, trial, inspection, expected):
    result = checked(tmp_path, UaesTrialAgent, "U15", [["流转记录", "流程结束 流程完成"], ["试装结论", trial], ["检验结论", inspection]])
    assert result.status == expected


def test_photo_success_does_not_claim_correct_part_content(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (16, 12), "white").save(path)
    body = preset_rule_body("UAES", "U13")
    agent = UaesPhotoAgent(AuditInput(audit_id="photo-version", file_url=str(path)), RuleInput(rule_id="photo-rules", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(0)
    assert result.status == "PASS"
    assert "需人工确认样品照片是否符合要求" in result.suggestions


@pytest.mark.parametrize(
    ("text", "detected", "expected"),
    [("受控", True, "PASS"), ("受控文件", True, "PASS"), ("存档资料", True, "UNABLE"), ("不受控", True, "UNABLE"), ("非受控", True, "UNABLE"), ("受控", False, "UNABLE")],
)
def test_controlled_stamp_uses_located_paddle_seal_facts_not_preprinted_labels(tmp_path, monkeypatch, text, detected, expected):
    from types import SimpleNamespace

    import fitz
    from audit.checker_lib.uaes.records import UaesControlledDocumentAgent
    from audit.engine.runtime import bind_runtime
    from documents.ocr.paddle import build_evidence

    path = tmp_path / "controlled.pdf"
    with fitz.open() as document:
        document.new_page(width=400, height=200).insert_text((20, 20), "controlled stamp label")
        document.save(path)
    original = path.read_bytes()
    reads, calls = [], []

    def read(file_path, page_number):
        reads.append((file_path, page_number))
        raw = {
            "dataInfo": {"pages": [{"width": 400, "height": 200}]},
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "layout_det_res": {"boxes": [{"label": "seal", "coordinate": [10, 10, 60, 60]}] if detected else []},
                        "seal_res_list": [{"rec_texts": [text], "bbox": [10, 10, 60, 60]}] if detected else [],
                        "parsing_res_list": [],
                        "table_res_list": [],
                    }
                }
            ],
        }
        return build_evidence(raw, page_count=1, pdf_path=file_path, source_pages=[page_number])

    monkeypatch.setattr("documents.ai.run_json", lambda *args, **kwargs: calls.append(kwargs))
    body = preset_rule_body("UAES", "U10")
    agent = UaesControlledDocumentAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rules", **{key: value for key, value in body.items() if key != "kind"}))
    with bind_runtime(SimpleNamespace(read_paddle_page=read)):
        result = agent.check(0)
        second = agent.check(0)
    assert result.status == second.status == expected
    assert reads == [(str(path), 1)] and calls == []
    assert result.evidence.used == ("visual" if detected else "none")
    assert all(source.kind == "visual" for source in result.evidence.sources)
    assert path.read_bytes() == original


# U04:3 names 同项目、同物料编号 only; customer and revision do not pair the drawing.
@pytest.mark.parametrize(("variant", "expected"), [("same", "PASS"), ("wrong_part", "UNABLE"), ("wrong_customer", "PASS"), ("wrong_revision", "PASS"), ("duplicate", "UNABLE"), ("unreadable", "ERR")])
def test_materials_only_pairs_current_unique_matching_originals(tmp_path, variant, expected):
    from audit.checker_lib.uaes.materials import UaesMaterialsAgent
    from audit.contracts import GroupContextInput

    def original(filename, *, part="P12345", customer="UAES", revision="A"):
        book = Workbook()
        book.active.title = "图纸" if "图纸" in filename else "材料清单"
        for row in [["零件号", part], ["零件名称", "支架"], ["客户名称", customer], ["版本", revision], ["项目", "M1"]]:
            book.active.append(row)
        path = tmp_path / filename
        book.save(path)
        book.close()
        return AuditInput(audit_id=filename, file_url=str(path), file_metadata={"original_filename": filename})

    target = original("清单.xlsx")
    reference = original(
        "图纸.xlsx", part="WRONG" if variant == "wrong_part" else "P12345", customer="理想" if variant == "wrong_customer" else "UAES", revision="B" if variant == "wrong_revision" else "A"
    )
    references = {target.audit_id: target, reference.audit_id: reference}
    if variant == "duplicate":
        extra = original("图纸副本.xlsx")
        references[extra.audit_id] = extra
    if variant == "unreadable":
        from pathlib import Path

        Path(reference.file_url).write_bytes(b"corrupt supplied source")
    body = preset_rule_body("UAES", "U04")
    agent = UaesMaterialsAgent(target, RuleInput(rule_id="rules", **{key: value for key, value in body.items() if key != "kind"}), GroupContextInput(by_audit_id=references))
    result = agent.check(2)
    assert result.status == expected, result.suggestions
    if expected == "PASS":
        import json

        assert {json.loads(source.locator)["file_version_id"] for source in result.evidence.sources} == {"清单.xlsx", "图纸.xlsx"}


def test_l2_precheck_reports_individual_source_facts_with_no_false_certification(tmp_path):
    book = Workbook()
    sections = ["包装方案", "零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"]
    for phrase in sections:
        book.active.append([phrase])
    path = tmp_path / "packaging.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U09")
    agent = UaesPackagingAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rules", **{key: value for key, value in body.items() if key != "kind"}))
    facts = agent.precheck()
    assert {fact["id"] for fact in facts} == {f"U09:S{i}" for i in range(1, 8)}
    assert all(fact["status"] == "satisfied" and fact["evidence"]["sources"] for fact in facts)
    assert all(fact["certified"] is False for fact in facts)


def test_pfmea_scores_use_original_columns_not_action_results(tmp_path):
    from audit.checker_lib.uaes.fmea import UaesPfmeaAgent

    book = Workbook()
    sheet = book.active
    sheet.title = "专用PFMEA"
    sheet.append(["PFMEA"])
    labels = [
        "Potential Failure Mode 潜在的故障模式",
        "Potential Effect(s) of Failure 潜在的故障影响",
        "SEV严重度",
        "Potential Cause(s) Mechanism(s) of Failure 潜在的引起故障的机理",
        "OCC频度",
        "Current Process Controls Prevention目前的预防控制",
        "Current Process Controls Detection 目前的探测控制",
        "DET探测度",
    ]
    sheet.append([*labels, "Actions Results 措施结果", None, None])
    sheet.merge_cells("I2:K2")
    sheet.append([*labels, "SEV严重度", "OCC频度", "DET探测度"])
    sheet.append(["壳体裂纹", "导致整车召回", 3, "压铸参数异常", 4, "参数监控", "100%在线检测", 2, 3, 2, 2])
    path = tmp_path / "source.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U07")
    agent = UaesPfmeaAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(1)
    # The post-action S/O/D repeat the labels; the original scores still decide.
    assert result.status == "NG", result.suggestions
    assert "整车召回" in result.suggestions


def control_plan(tmp_path, rows):
    from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent

    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    sheet.append(["控制计划"])
    sheet.append(["工序名称", "生产设备", "特殊特性"])
    for row in rows:
        sheet.append(row)
    path = tmp_path / "cp.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U08")
    return UaesControlPlanAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))


def test_control_plan_continuation_rows_belong_to_their_operation(tmp_path):
    # The operation and its resources are written once; later rows leave them blank.
    agent = control_plan(tmp_path, [["压铸", "压铸机", None], [None, None, "3,5"]])
    assert agent.check(3).status == "PASS"
    assert agent.check(0).status == "PASS"
    unmarked = control_plan(tmp_path, [["压铸", "压铸机", None], [None, None, None]])
    assert unmarked.check(3).status == "NG"


def test_pfmea_continuation_rows_have_their_scores_checked(tmp_path):
    from audit.checker_lib.uaes.fmea import UaesPfmeaAgent

    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    sheet.append(["PFMEA"])
    sheet.append(["失效模式", "失效后果", "严重度", "失效原因", "发生度", "预防控制", "探测控制", "探测度"])
    sheet.append(["裂纹", "外观不良", 5, "参数异常", 3, "参数监控", "目视", 6])
    # A further control of the same failure mode leaves mode and cause blank.
    sheet.append([None, None, 5, None, 3, "首件确认", "CMM", 11])
    path = tmp_path / "pfmea.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U07")
    agent = UaesPfmeaAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(1)
    assert result.status == "NG", result.suggestions
    assert "1至10" in result.suggestions


@pytest.mark.parametrize("confirmed", [True, None, False])
def test_unreadable_trailing_row_ends_the_rows_only_when_confirmed_as_footer(tmp_path, confirmed):
    from audit.checker_lib.uaes.native import NativeFacts, UnknownFact

    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    sheet.append(["失效模式", "严重度"])
    sheet.append(["裂纹", 5])
    # An uncached formula stands for any anchor the reader cannot establish.
    sheet.append(["=1+1", "符号说明"])
    path = tmp_path / "footer.xlsx"
    book.save(path)
    book.close()
    asked = []

    def footer(_table, row):
        asked.append(row)
        return confirmed

    facts = NativeFacts(str(path), "version", lambda *args, **kwargs: None)
    if confirmed is True:
        assert [row.row for row in facts.rows({"mode": ("失效模式",), "s": ("严重度",)}, anchor=("mode",), footer=footer)] == [2]
    else:
        with pytest.raises(UnknownFact):
            facts.rows({"mode": ("失效模式",), "s": ("严重度",)}, anchor=("mode",), footer=footer)
    assert asked == [3]


def test_pfmea_blank_failure_mode_reads_its_own_group_not_another(tmp_path):
    from audit.checker_lib.uaes.fmea import UaesPfmeaAgent

    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    sheet.append(["PFMEA"])
    sheet.append(["失效模式", "失效后果", "严重度", "失效原因", "发生度", "预防控制", "探测控制", "探测度"])
    # Each failure mode is written once; its further causes leave it blank.
    sheet.append(["裂纹", "外观不良", 5, "参数异常", 3, "参数监控", "目视", 6])
    sheet.append([None, "外观不良", 5, "排气不良", 3, "模具评审", "试模记录", 3])
    sheet.append(["气孔", "泄漏", 7, "熔液含气", 3, "除气", "气密", 3])
    sheet.append([None, "泄漏", 7, "排气不良", 3, "模具评审", "试模记录", 3])
    path = tmp_path / "pfmea.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U07")
    agent = UaesPfmeaAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(1)
    # Same cause and controls under different failure modes may score differently;
    # only the rejected engineering-reasonableness parts remain, for a reviewer.
    assert result.status == "UNABLE", result.suggestions
    assert "已拒绝自动判定" in result.suggestions


def agent_for(tmp_path, agent_cls, entry, sheets):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    path = tmp_path / "source.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", entry)
    return agent_cls(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))


def test_control_plan_reads_equipment_tooling_as_production_resources(tmp_path):
    from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent

    agent = agent_for(tmp_path, UaesControlPlanAgent, "U08", {"控制计划": [["控制计划"], ["工序名称/操作描述", "设备/工装"], ["压铸", None], ["机加工", "CNC"]]})
    result = agent.check(0)
    assert result.status == "NG", result.suggestions
    assert "压铸" in result.suggestions


@pytest.mark.parametrize("options,status", [("■样件 □试生产 □生产", "NG"), ("■样件 ■试生产 □生产", "UNABLE")])
def test_control_plan_type_is_the_one_marked_option(tmp_path, options, status):
    from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent

    agent = agent_for(tmp_path, UaesControlPlanAgent, "U08", {"控制计划": [["控制计划"], [options], ["工序名称", "特殊特性"], ["压铸", "3"]]})
    assert agent.check(2).status == status


def test_special_list_names_each_row_in_a_part_name_and_number_column(tmp_path):
    from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent

    rows = [["特殊特性清单"], ["序号", "产品名称/零件号 Name/Code", "特殊特性名称", "特性值", "特性类别"], [1, "F01R10Y776机加壳体", "孔径", "Φ7±0.1", "SC"], [2, None, "平面度", "0.15", "SC"]]
    result = agent_for(tmp_path, UaesSpecialCharacteristicsAgent, "U12", {"特殊特性清单": rows}).check(0)
    assert result.status == "NG", result.suggestions
    assert "第4行" in result.suggestions


def test_capability_directory_page_can_be_its_own_sheet(tmp_path):
    from audit.checker_lib.uaes.engineering import UaesCapabilityAgent

    agent = agent_for(tmp_path, UaesCapabilityAgent, "U06", {"目录": [["序号", "特性"], [1, "孔径"]], "孔径": [["读数次数", 125]]})
    # The directory is found; the batch then lacks the PGQP1 it is compared with.
    assert "目录" not in agent.check(3).suggestions


def test_a_colon_label_reads_its_value_across_spacer_cells(tmp_path):
    from audit.checker_lib.uaes.native import NativeFacts

    path = tmp_path / "grr.xlsx"
    book = Workbook()
    book.active.append(["量具编号:", None, None, "PE702438", None, "测量参数:", None])
    book.active.append(["量具名称:", None, None, None, None, "参数规格:", "6.5"])
    book.save(path)
    book.close()
    facts = NativeFacts(str(path), "version", lambda *args, **kwargs: None)
    assert facts.field(("量具编号",)) == "PE702438"
    with pytest.raises(Exception):
        facts.field(("量具名称",))


@pytest.mark.parametrize(
    "sheets,expected",
    [
        # A report labels the name "Part number:"; the directory states the actual number.
        ({"目录": [["零件号码", "F01RD0Y776"]], "孔径": [["Part number:", "节气门"], ["Drawing number", "F01RD0Y776"]]}, "F01RD0Y776"),
        # A header band prints the number under its label.
        ({"控制计划": [["零件号", "客户", "零件名称"], ["F01RD0Y776", "UAES", "节气门壳体"]]}, "F01RD0Y776"),
        ({"孔径": [["Part number:", "节气门"]]}, None),
    ],
    ids=["mislabelled-report", "value-under-label", "only-a-name"],
)
def test_a_part_number_is_the_labelled_value_that_has_digits(tmp_path, sheets, expected):
    from audit.checker_lib.uaes.native import _ACCEPT, _IDENTITY, NativeFacts, UnknownFact

    path = tmp_path / "source.xlsx"
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(path)
    book.close()
    facts = NativeFacts(str(path), "version", lambda *args, **kwargs: None)
    if expected is None:
        with pytest.raises(UnknownFact):
            facts.field(_IDENTITY["part"][1], accept=_ACCEPT["part"])
    else:
        assert facts.field(_IDENTITY["part"][1], accept=_ACCEPT["part"]) == expected


def _flow_chart(*operations, legend=("加工", "搬运", "贮存", "检验"), symbols=("◇", "○", "△", "□")):
    rows = [["工序号", *legend, "过程描述"], [None, *symbols, None]]
    for step, description, lane in operations:
        rows.append([step, *(symbols[index] if index == lane else None for index in range(len(legend))), description])
    return {"PFD": rows}


@pytest.mark.parametrize(
    "operations,status",
    [
        ([(10, "进料检验", 3), (20, "机加工", 0), (30, "入库", 2)], "PASS"),
        # A test is an inspection activity and must use the inspection symbol.
        ([(10, "进料检验", 3), (20, "气密", 0)], "NG"),
        # One activity column holds one symbol.
        ([(10, "机加工", 0), (20, "清洗", 3)], "PASS"),
    ],
    ids=["symbols-follow-legend", "test-with-machining-symbol", "plain-operations"],
)
def test_flow_chart_symbols_are_read_from_cells(tmp_path, operations, status):
    from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent

    agent = agent_for(tmp_path, UaesProcessFlowAgent, "U16", _flow_chart(*operations))
    assert agent.check(1).status == status


def test_flow_chart_symbol_outside_the_legend_fails(tmp_path):
    from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent

    sheets = _flow_chart((10, "进料检验", 3), (20, "机加工", 0))
    sheets["PFD"][3][1] = "▽"
    assert agent_for(tmp_path, UaesProcessFlowAgent, "U16", sheets).check(1).status == "NG"


def test_flow_chart_without_any_key_operation_mark_fails_without_an_image(tmp_path, monkeypatch):
    from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent

    monkeypatch.setattr(UaesProcessFlowAgent, "visual_result", lambda *args, **kwargs: pytest.fail("no image is needed"))
    agent = agent_for(tmp_path, UaesProcessFlowAgent, "U16", _flow_chart((10, "进料检验", 3), (20, "机加工", 0)))
    assert agent.check(0).status == "NG"


@pytest.mark.parametrize("mark", ["K", "★"])
def test_flow_chart_key_mark_leaves_only_the_other_facts_to_the_image(tmp_path, monkeypatch, mark):
    from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent

    asked = []
    monkeypatch.setattr(UaesProcessFlowAgent, "visual_result", lambda self, fields, description: asked.append(set(fields)) or ("PASS", description))
    agent = agent_for(tmp_path, UaesProcessFlowAgent, "U16", _flow_chart((10, "进料检验", 3), (20, f"机加工 {mark}", 0)))
    agent.check(0)
    assert asked == [{"characteristic_marks", "legend"}]


def test_emf_text_reads_the_words_an_emf_picture_draws():
    import struct

    from audit.document_tools import emf_text

    text = "受 控".encode("utf-16-le")
    record = bytearray(76) + text
    struct.pack_into("<II", record, 0, 84, len(record))
    struct.pack_into("<II", record, 44, len("受 控"), 76)
    end = struct.pack("<II", 14, 8)
    assert emf_text(bytes(record) + end) == ["受 控"]
    assert emf_text(struct.pack("<II", 84, 4)) == []


def test_an_emf_stamp_placed_on_the_sheet_is_the_controlled_stamp(monkeypatch):
    from pathlib import Path

    from audit.checker_lib.uaes.engineering import UaesProcessFlowAgent
    from audit.contracts import AuditInput, RuleInput

    path = next((Path(__file__).parents[3] / "assets/uaes/samples").glob("F01R10Y776*/**/流程图776.xlsx"))
    monkeypatch.setattr(UaesProcessFlowAgent, "visual_pdf", lambda *args, **kwargs: pytest.fail("no image is needed"))
    body = preset_rule_body("UAES", "U16")
    agent = UaesProcessFlowAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    assert agent.check(2).status == "PASS"
