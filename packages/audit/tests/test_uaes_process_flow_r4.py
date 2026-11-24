"""Positive source evidence for UAES R4 process-flow checks."""

from pathlib import Path
from zipfile import ZipFile

from audit.checker_lib.uaes.flowchart import placed_drawings
from audit.checker_lib.uaes.v1.process_flow_r4 import UaesProcessFlowRevision4
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.presets import preset_rule_body


def test_air_leak_station_matches_all_three_originals():
    root = Path(__file__).resolve().parents[3] / "assets/uaes/samples/F01RD0YA40_机加工壳体/测试文件"
    flow, fmea, cp = (AuditInput(audit_id=number, file_url=str(next(root.glob(f"{number}*/*.xlsx")))) for number in ("08", "09", "10"))
    group = GroupContextInput(by_doc_type={"过程流程图": [flow], "潜在过程失效模式及后果分析": [fmea], "控制计划": [cp]})
    reader = UaesProcessFlowRevision4(flow, RuleInput.of(preset_rule_body("UAES", "U16"), rule_id="u16"), group_context=group)
    ctx = reader.rule_ctx(reader._load_workbook())
    chart = reader.chart(ctx)
    assert chart is not None
    step = next(operation for operation in chart.operations if operation.step == "110")

    linked, evidence = reader.station_link(ctx, step, {operation.step for operation in chart.operations}, reader.station_part(chart.sheet, chart.header))

    assert linked
    assert all(term in evidence for term in ("气密性检测", "气检", "测试参数", "控制方法"))


def test_placed_rework_annotation_keeps_its_text_and_anchor(tmp_path):
    ns = "http://schemas.openxmlformats.org"
    path = tmp_path / "flow.xlsx"

    def relationship(kind, target):
        return f'<Relationships xmlns="{ns}/package/2006/relationships"><Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/{kind}" Target="{target}"/></Relationships>'

    with ZipFile(path, "w") as book:
        book.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}/spreadsheetml/2006/main" xmlns:r="{ns}/officeDocument/2006/relationships"><sheets><sheet name="PFD" r:id="rId1"/></sheets></workbook>',
        )
        book.writestr("xl/_rels/workbook.xml.rels", relationship("worksheet", "worksheets/sheet1.xml"))
        book.writestr("xl/worksheets/_rels/sheet1.xml.rels", relationship("drawing", "../drawings/drawing1.xml"))
        book.writestr(
            "xl/drawings/drawing1.xml",
            f'<xdr:wsDr xmlns:xdr="{ns}/drawingml/2006/spreadsheetDrawing" xmlns:a="{ns}/drawingml/2006/main">'
            "<xdr:twoCellAnchor><xdr:from><xdr:col>5</xdr:col><xdr:row>11</xdr:row></xdr:from>"
            "<xdr:to><xdr:col>7</xdr:col><xdr:row>12</xdr:row></xdr:to>"
            "<xdr:sp><xdr:txBody><a:p><a:r><a:t>不合格品</a:t></a:r><a:r><a:t>隔离</a:t></a:r></a:p></xdr:txBody></xdr:sp>"
            "</xdr:twoCellAnchor></xdr:wsDr>",
        )

    (drawing,) = placed_drawings(str(path), "PFD")
    assert (drawing.place, drawing.text) == ((12, 6, 13, 8), "不合格品隔离")
