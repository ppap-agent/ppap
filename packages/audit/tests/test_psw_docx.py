"""PSW DOCX rules consume real Word structures and embedded image inputs."""

from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from audit.contracts import AuditInput, RuleInput
from audit.engine import run_audit
from audit.engine.app_runtime import run_audit_engine
from audit.engine.checkers.client import AIClient
from audit.kinds import PpapDocumentType
from docx import Document, oxml
from PIL import Image


def submission_control(*, generic=False):
    properties = '<w:alias w:val="首次提交"/>'
    if not generic:
        properties += '<w14:checkbox><w14:checked w14:val="1"/></w14:checkbox>'
    return oxml.parse_xml(f"""<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">
      <w:sdtPr>{properties}</w:sdtPr>
      <w:sdtContent><w:p><w:r><w:t>☒ 首次提交</w:t></w:r></w:p></w:sdtContent>
    </w:sdt>""")


def image_payload(size, color):
    image = Image.new("RGB", size, color=color)
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
        for row, size, color in ((56, (48, 48), (220, 0, 0)), (57, (32, 40), (0, 0, 180))):
            paragraph = document.add_paragraph() if images_outside else main.cell(row, 4).paragraphs[0]
            paragraph.add_run().add_picture(image_payload(size, color))
    if duplicate:
        document.element.body.append(deepcopy(main._tbl))
    return document


class WordVisualRuntime:
    def __init__(self):
        self.calls = []
        self.unexpected_calls = []

    def run_image_prompt(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("Native/Paddle PSW must not call LLM vision")

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
        assert runtime.calls == []
        return results

    return check


def test_native_word_fields_and_controls_remain_known_while_visuals_stay_unconfirmed(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document().save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert [result.status for result in results] == ["PASS", "PASS", "UNABLE", "PASS", "UNABLE", "PASS", "PASS", "PASS", "PASS", "PASS"]
    assert all(result.evidence is not None for result in results)
    assert results[0].evidence.used == "structure"
    assert results[0].evidence.source_format == "docx"
    assert results[2].evidence.used == "none"
    assert results[2].evidence.sources == ()
    assert runtime.calls == []


def test_missing_stamp_images_and_required_checkmarks_do_not_invent_passes(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document(images=False, checkboxes=False).save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert [results[index].status for index in (2, 4, 6, 7)] == ["NG", "NG", "NG", "NG"]
    assert runtime.calls == []
    assert results[2].evidence.used == "structure"


@pytest.mark.parametrize("profile", ["v2", "v3"])
def test_docx_native_fields_remain_known_and_unsupported_visual_facts_stay_unable(tmp_path, monkeypatch, profile):
    path = tmp_path / "psw.docx"
    psw_document().save(path)
    original = path.read_bytes()
    attempts = []

    def reject(self, **kwargs):
        attempts.append(kwargs)
        raise AssertionError("DOCX must not call LLM vision")

    monkeypatch.setattr(AIClient, "structured_completion", reject)
    results = run_audit_engine(
        AuditInput(audit_id="psw-word-native", file_url=str(path)),
        RuleInput(rule_id="confirmed-psw-rules", rule_profile=profile, type=PpapDocumentType.PSW.get_type_name(), rules=[{"id": str(index)} for index in range(1, 11)]),
    )
    assert [result.status for result in results] == ["PASS", "PASS", "UNABLE", "PASS", "UNABLE", "PASS", "PASS", "PASS", "PASS", "PASS"]
    assert attempts == []
    assert path.read_bytes() == original
    assert results[4].evidence.used == "structure"
    assert results[4].evidence.diagnostics[0].code == "psw.visual.unconfirmed"


@pytest.mark.parametrize("control", ["nested-checkbox", "generic-checkbox-character"])
def test_nested_submission_controls_remain_authoritative(tmp_path, audit, control):
    path = tmp_path / "psw.docx"
    psw_document(nested_submission=control == "nested-checkbox", generic_submission=control == "generic-checkbox-character").save(path)

    results = audit(path, WordVisualRuntime())

    assert results[6].status == "PASS"


def test_embedded_images_outside_the_main_table_do_not_invent_seal_facts(tmp_path, audit):
    path = tmp_path / "psw.docx"
    psw_document(images_outside=True).save(path)
    runtime = WordVisualRuntime()

    results = audit(path, runtime)

    assert results[2].status == "UNABLE"
    assert results[4].status == "UNABLE"
    assert runtime.calls == []


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

    assert results[2].status == "UNABLE"
    assert results[4].status == "UNABLE"
    assert runtime.calls == []


def add_graphic_footnote(document):
    relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document.tables[1].cell(19, 1).paragraphs[0]._p.append(oxml.parse_xml(f'<w:r xmlns:w="{word_ns}"><w:footnoteReference w:id="1"/></w:r>'))
    packed = BytesIO()
    document.save(packed)
    with ZipFile(BytesIO(packed.getvalue())) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    relations = ET.fromstring(parts["word/_rels/document.xml.rels"])
    ET.SubElement(
        relations, f"{{{relationship_ns}}}Relationship", {"Id": "rIdFootnote", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes", "Target": "footnotes.xml"}
    )
    parts["word/_rels/document.xml.rels"] = ET.tostring(relations)
    types = ET.fromstring(parts["[Content_Types].xml"])
    ET.SubElement(
        types,
        "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
        {"PartName": "/word/footnotes.xml", "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"},
    )
    parts["[Content_Types].xml"] = ET.tostring(types)
    parts["word/footnotes.xml"] = (
        f'<w:footnotes xmlns:w="{word_ns}" xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:footnote w:id="1"><w:p><w:r><w:pict><v:shape style="width:40pt;height:40pt">'
        '<v:imagedata r:id="rIdSeal"/></v:shape></w:pict></w:r></w:p></w:footnote></w:footnotes>'
    ).encode()
    parts["word/_rels/footnotes.xml.rels"] = (
        f'<Relationships xmlns="{relationship_ns}"><Relationship Id="rIdSeal" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="media/seal.png"/></Relationships>'
    ).encode()
    image = BytesIO()
    Image.new("RGB", (40, 40), "red").save(image, format="PNG")
    parts["word/media/seal.png"] = image.getvalue()
    result = BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return result.getvalue()


@pytest.mark.parametrize("profile", ["v2", "v3"])
def test_unparsed_graphic_footnote_does_not_prove_an_absent_stamp(tmp_path, monkeypatch, profile):
    from documents.word import extract_docx_structure

    path = tmp_path / "footnote-seal.docx"
    content = add_graphic_footnote(psw_document(images=False, checkboxes=False))
    path.write_bytes(content)
    parsed = extract_docx_structure(content)
    assert parsed.valid and not parsed.visual_evidence
    for name in ("OCR_MODEL_NAME", "OCR_OPENAI_API_KEY", "OCR_OPENAI_BASE_URL"):
        monkeypatch.setenv(name, "")
    results = run_audit_engine(
        AuditInput(audit_id="word-with-opaque-footnote", file_url=str(path)),
        RuleInput(rule_id="psw-footnote", rule_profile=profile, type=PpapDocumentType.PSW.get_type_name(), rules=[{"id": str(i)} for i in range(1, 11)]),
    )
    assert results[2].status == "UNABLE"
    assert results[4].status == "UNABLE"


@pytest.mark.parametrize("version", ["v2", "v3"])
def test_word_native_source_reads_bind_real_version_and_original_cell(tmp_path, version):
    import importlib
    import json

    path = tmp_path / "psw.docx"
    psw_document().save(path)
    cls = importlib.import_module(f"audit.checkers.{version}.psw_docx_agent").PswDocxAgent
    checker = cls(AuditInput(audit_id="frozen-word", file_url=str(path)), RuleInput(rule_id="r", type="零件提交保证书", rules=[{"id": str(n)} for n in range(1, 11)]))
    try:
        for index in (0, 1, 3, 5, 6, 7, 8, 9):
            result = checker.check_source_rule(index)
            assert result.status == "PASS"
            assert result.checks_performed > 0
            assert result.evidence.sources
            for source in result.evidence.sources:
                locator = json.loads(source.locator)
                assert locator["file_version_id"] == "frozen-word"
                assert locator["kind"] == "docx_cell"
                assert locator["table"] in {1, 2}
        from openpyxl import Workbook

        context = checker.rule_ctx(checker._load_workbook())
        referenced = Workbook()
        referenced.active.title = context.workbook.worksheets[0].title
        referenced.active["A1"] = "reference value"
        context.bind_workbook(referenced, "reference-version")
        context.note_cell(referenced.active.title, 1, 1, file_version_id="reference-version")
        reference = checker._evidence_from_reads(context).sources[0]
        assert json.loads(reference.locator) == {"kind": "xlsx_cell", "sheet": referenced.active.title, "cell": "A1", "file_version_id": "reference-version"}
        assert reference.excerpt == "reference value"
        referenced.close()
        ops = checker._word_ops_cache
        assert ops.source_locator("word/document.xml", "body/tbl[2]/tr[57]/tc[5]/p[1]/drawing[1]") == {"kind": "docx_cell", "table": 2, "row": 57, "column": 5}
        assert ops.source_locator("word/document.xml", "body/p[1]/drawing[1]") == {"kind": "docx_paragraph", "paragraph": 1}
        assert ops.source_locator("word/header1.xml", "body/p[1]") is None
        assert ops.source_locator("word/document.xml", "body/tbl[999]/tr[1]/tc[1]") is None
    finally:
        checker.close()
