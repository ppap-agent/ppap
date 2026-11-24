"""R4 scope commitments that require evidence from the original."""

from decimal import Decimal

from audit.checker_lib.uaes.v1.msa_r4 import Report, UaesMsaAgentR4


def test_explicit_report_unit_allows_comparing_asymmetric_tolerance():
    report = Report("GRR", None, "GR&R", studied=("6.6",), limits=("+0.2/−0.1",), dimension_units=frozenset({"mm"}))
    assert UaesMsaAgentR4.plan_feature_match("6.5–6.8 mm", report) is True
    physically_equal = Report("GRR", None, "GR&R", studied=(str(Decimal("6.6") * 1000) + "μm",), limits=("+200/-100μm",))
    assert UaesMsaAgentR4.plan_feature_match("6.5–6.8 mm", physically_equal) is True


def test_dimension_unit_is_cited_from_the_original_measurement_heading():
    from audit.contracts import AuditInput, RuleInput
    from audit.presets import preset_rule_body
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "量具重复性和再现性分析表"
    sheet.append(["量具重复性和再现性分析表"])
    sheet.append(["测量参数（mm）", "6.6"])
    sheet.append(["参数规格", "+0.2/-0.1"])
    sheet.append(["结论", "测量系统可以接受"])
    item = AuditInput(audit_id="report", file_url="report.xlsx")
    reader = UaesMsaAgentR4(item, RuleInput.of(preset_rule_body("UAES", "U05"), rule_id="u05"))

    report = reader.sheet_report(reader.rule_ctx(workbook), item, sheet)

    assert isinstance(report, Report)
    assert report.dimension_units == {"mm"}
    assert (sheet, 2, 1) in report.study_cells  # the unit-bearing heading, not the nominal cell
    assert reader.plan_feature_match("6.5–6.8mm", report) is True
