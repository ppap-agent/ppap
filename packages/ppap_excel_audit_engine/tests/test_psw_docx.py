"""PSW DOCX rules consume real Word structures and embedded image inputs."""

import json
from copy import deepcopy
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

import pytest
from docx import Document, oxml
from PIL import Image
from ppap_core.contracts import AuditInput, RuleInput
from ppap_core.types import PpapDocumentType
from ppap_excel_audit_engine import run_audit


def submission_control(*, generic=False):
    properties = '<w:alias w:val="首次提交"/>'
    if not generic:
        properties += '<w14:checkbox><w14:checked w14:val="1"/></w14:checkbox>'
    return oxml.parse_xml(f"""<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">
      <w:sdtPr>{properties}</w:sdtPr>
      <w:sdtContent><w:p><w:r><w:t>☒ 首次提交</w:t></w:r></w:p></w:sdtContent>
    </w:sdt>""")


def image_payload():
    image = Image.new("RGB", (48, 48), color=(220, 0, 0))
    payload = BytesIO()
    image.save(payload, format="PNG")
    image.close()
    payload.seek(0)
    return payload


def psw_document(*, images=True, checkboxes=True, nested_submission=False, generic_submission=False, images_outside=False, merged_disposition=False, duplicate=False):
    document = Document()
    document.add_paragraph("DOCX PSW 测试表单")
    delivery = document.add_table(rows=3, cols=2)
    delivery.cell(0, 0).merge(delivery.cell(0, 1)).text = "生产件批准交付物文件清单"
    delivery.cell(1, 0).text = "项目"
    delivery.cell(1, 1).text = "附件"
    delivery.cell(2, 0).text = "尺寸报告"
    delivery.cell(2, 1).text = "尺寸报告.xlsx"
    main = document.add_table(rows=60, cols=6)
    main.cell(0, 0).merge(main.cell(0, 5)).text = "零件提交保证书 PSW"
    main.cell(1, 0).text = "供应商名称/供应商代码"
    main.cell(1, 3).text = "顾客名称"
    main.cell(2, 0).text = "示例总公司/S001"
    main.cell(2, 3).text = "理想汽车"
    fields = (
        (3, "重量(kg)", "1.2345"),
        (4, "零件名称", "扰流板"),
        (5, "理想汽车零件号", "L123"),
        (6, "供方料号", "S123"),
        (7, "图纸号", "D123"),
        (8, "模具穴号", "1"),
        (9, "工程更改版本", "A"),
        (10, "版本日期", "2026-08-01"),
        (11, "街道地址", "示例路1号"),
        (12, "城市", "上海"),
        (13, "省", "上海"),
        (14, "国家", "中国"),
        (15, "顾客名称", "理想汽车"),
        (16, "设计开发", "张三"),
        (17, "采购工程", "李四"),
        (18, "PTC", "王五"),
        (19, "经授权的供方代表签字", "赵六"),
    )
    for row, label, value in fields:
        main.cell(row, 0).text = label
        main.cell(row, 1).text = value
    main.cell(31, 0).text = "提交原因"
    if checkboxes:
        if nested_submission or generic_submission:
            nested = main.cell(32, 0).add_table(rows=1, cols=1)
            nested.cell(0, 0).paragraphs[0]._p.append(submission_control(generic=generic_submission))
        else:
            main.cell(32, 0).text = "☒ 首次提交"
    main.cell(44, 0).text = "认可情况"
    if merged_disposition:
        main.cell(44, 1).merge(main.cell(44, 3)).text = "☒ 批准 ☒ 条件批准 ☐ 拒绝" if checkboxes else "批准 条件批准 拒绝"
    else:
        for column, label in enumerate(("批准", "条件批准", "拒绝"), 1):
            mark = "☒" if column == 1 else "☐"
            main.cell(44, column).text = f"{mark} {label}" if checkboxes else label
    for row, label in enumerate(("尺寸测量", "外观标准", "材料和功能试验", "统计过程数据"), 49):
        main.cell(row, 0).text = f"☒ {label}" if checkboxes else label
    main.cell(54, 0).text = "这些结果均满足所有图纸和规范要求"
    main.cell(54, 1).text = "☒ 是" if checkboxes else "是"
    if images:
        for row in (56, 57):
            paragraph = document.add_paragraph() if images_outside else main.cell(row, 4).paragraphs[0]
            paragraph.add_run().add_picture(image_payload())
    if duplicate:
        document.element.body.append(deepcopy(main._tbl))
    return document


class WordVisualRuntime:
    def __init__(self):
        self.calls = []
        self.unexpected_calls = []

    def run_image_prompt(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(
            {
                "psw_stamp_ends_with_company": True,
                "classification": "视觉检查",
                "item": "PSW 视觉检查",
                "priority": "P0",
                "status": "PASS",
                "suggestions": "已识别印章与签字",
            }
        )

    def __getattr__(self, name):
        self.unexpected_calls.append(name)
        raise AssertionError(f"Native DOCX must not use external conversion or rendering: {name}")


@pytest.fixture(params=["v2", "v3"])
def audit(request):
    def check(path, runtime):
        results = run_audit(
            AuditInput(audit_id="psw-word", file_url=str(path)),
            RuleInput(rule_id="confirmed-psw-rules", rule_profile=request.param, type=PpapDocumentType.PSW.get_type_name(), rules=[{"id": str(index)} for index in range(1, 11)]),
            runtime=runtime,
        )
        assert runtime.unexpected_calls == []
        return results

    return check


def test_word_fields_controls_and_images_reach_the_existing_rules_without_rendering(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document().save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert [result.status for result in results] == ["PASS"] * 10
    assert all(result.evidence is not None for result in results)
    assert results[0].evidence.used == "structure"
    assert results[0].evidence.source_format == "docx"
    assert results[2].evidence.used == "visual"
    assert results[2].evidence.sources[0].locator.startswith("docx://word/document.xml#")
    assert len(runtime.calls) == 2
    assert all(call["image_base64_list"] for call in runtime.calls)
    assert all("word/document.xml" in call["user_text"] for call in runtime.calls)


def test_missing_stamp_images_and_required_checkmarks_do_not_invent_passes(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document(images=False, checkboxes=False).save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert [results[index].status for index in (2, 4, 6, 7)] == ["NG"] * 4
    assert runtime.calls == []
    assert results[2].evidence.diagnostics[0].code == "psw.visual.missing"


@pytest.mark.parametrize("control", ["nested-checkbox", "generic-checkbox-character"])
def test_nested_submission_controls_remain_authoritative(tmp_path, audit, control):
    path = tmp_path / "psw.docx"
    psw_document(nested_submission=control == "nested-checkbox", generic_submission=control == "generic-checkbox-character").save(path)

    results = audit(path, WordVisualRuntime())

    assert results[6].status == "PASS"


def test_visual_rules_can_use_embedded_images_outside_the_main_table(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document(images_outside=True).save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert results[2].status == "PASS"
    assert results[4].status == "PASS"
    assert len(runtime.calls) == 2


def test_a_corrupt_embedded_image_package_is_an_execution_error(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document().save(path)
    with ZipFile(path) as archive:
        entries = [(item.filename, archive.read(item)) for item in archive.infolist()]
    image = next(payload for name, payload in entries if name.startswith("word/media/"))
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_STORED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    package = bytearray(buffer.getvalue())
    package[package.index(image)] ^= 1
    path.write_bytes(package)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert [result.status for result in results] == ["ERR"] * 10
    assert runtime.calls == []


def test_merged_disposition_cell_preserves_multiple_selected_options(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document(merged_disposition=True).save(path)

    results = audit(path, WordVisualRuntime())

    assert results[7].status == "NG"


def test_ambiguous_primary_tables_do_not_send_arbitrary_images_to_visual_rules(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document(duplicate=True).save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert results[2].status == "NG"
    assert results[4].status == "NG"
    assert runtime.calls == []
