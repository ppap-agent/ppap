"""The public engine executes actual files and reports unavailable routes."""

import pytest
from openpyxl import Workbook
from ppap_core.contracts import AuditInput, RuleInput
from ppap_excel_audit_engine import run_audit


@pytest.mark.parametrize("profile", ["v2", "v3"])
@pytest.mark.parametrize(
    ("filename", "document_type"),
    [("unknown.xlsx", "不存在的类型"), ("original.doc", "零件提交保证书"), ("control-plan.docx", "控制计划")],
    ids=["unknown-type", "raw-doc-needs-app-runtime", "no-docx-checker"],
)
def test_unavailable_routes_return_execution_errors_without_reading_unrelated_files(tmp_path, profile, filename, document_type):
    path = tmp_path / filename

    results = run_audit(AuditInput(audit_id="unavailable-route", file_url=str(path)), RuleInput(rule_id="rules", rule_profile=profile, type=document_type))

    assert len(results) == 1
    assert results[0].status == "ERR"
    assert "No agent registered" in results[0].suggestions
    assert not path.exists()


@pytest.mark.parametrize("profile", ["v2", "v3"])
def test_public_engine_rejects_unsupported_suffix_with_supported_formats_in_the_error(tmp_path, profile):
    with pytest.raises(ValueError) as error:
        run_audit(AuditInput(audit_id="unsupported", file_url=str(tmp_path / "original.bin")), RuleInput(rule_id="rules", rule_profile=profile, type="控制计划"))

    assert ".bin" in str(error.value)
    assert "PDF" in str(error.value)
    assert "Excel" in str(error.value)


@pytest.mark.parametrize("profile", ["v2", "v3"])
@pytest.mark.parametrize(("weight", "expected"), [(3.1897, "PASS"), (3.189, "NG")])
def test_supplier_alias_executes_the_existing_checker_on_actual_workbook_values(tmp_path, profile, weight, expected):
    path = tmp_path / "suppliers.xlsx"
    workbook = Workbook()
    workbook.active.title = "材料产品及分供方清单"
    workbook.active.append(["零件号", "单件重量"])
    workbook.active.append(["PART-001", weight])
    workbook.save(path)
    workbook.close()
    rules = RuleInput(
        rule_id="confirmed-supplier-rules",
        rule_profile=profile,
        type="材料分供方",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 5)],
    )

    results = run_audit(AuditInput(audit_id="supplier-alias", file_url=str(path)), rules)

    assert len(results) == 4
    assert results[0].status == expected
    if expected == "NG":
        assert "单件重量必须是四位小数格式" in results[0].suggestions
