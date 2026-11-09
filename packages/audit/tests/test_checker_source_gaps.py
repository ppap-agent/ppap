"""A bound clause without a reader reports a gap rather than an adapter fault."""

import pytest
from audit.checker_lib.source import SourceAudit
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


def test_reader_and_gap_keep_exact_ids_and_distinct_execution_counts(tmp_path):
    path = tmp_path / "input.xlsx"
    book = Workbook()
    book.active.append(["零件号", "P12345"])
    book.save(path)
    book.close()

    class Audit(SourceAudit):
        def part(self):
            value = self.native().field(("零件号",))
            return ("PASS" if value == "P12345" else "NG"), "核对原件零件号"

    rule = RuleInput(rule_id="rules", rules=[{"id": "C:9"}, {"id": "C:2"}])
    resource = {"supported_suffixes": (".xlsx",), "clauses": {"C:2": {"method": "part"}}, "known_gaps": {"C:9": "尚无经过验证的跨文件读取器。"}}
    audit = Audit(AuditInput(audit_id="frozen", file_url=str(path)), rule, resource=resource)
    try:
        assert [check.rule_item.id for check in audit.checks] == ["C:9", "C:2"]
        gap, read = [check.check() for check in audit.checks]
        assert gap.status == "UNABLE" and gap.checks_performed == 0
        assert gap.clause_id == "C:9" and gap.suggestions == resource["known_gaps"]["C:9"]
        assert read.status == "PASS" and read.checks_performed == 1
        assert read.clause_id == "C:2" and read.evidence.sources
    finally:
        audit.close()


def test_clause_carrier_limit_stops_before_source_or_model_read(tmp_path):
    class Audit(SourceAudit):
        def read(self):
            raise AssertionError("This clause cannot read PDF")

    rule = RuleInput(rule_id="rules", rules=[{"id": "R1"}])
    resource = {"supported_suffixes": (".xlsx", ".pdf"), "clauses": {"R1": {"method": "read", "carriers": (".xlsx",)}}}
    audit = Audit(AuditInput(audit_id="frozen", file_url=str(tmp_path / "not-read.pdf")), rule, resource=resource)
    try:
        result = audit.evaluate("R1")
        assert result.status == "UNABLE" and result.checks_performed == 0
        assert "不支持" in result.suggestions
    finally:
        audit.close()


@pytest.mark.parametrize("header_row,header_column", [(61, 1), (1, 121)])
def test_header_outside_search_scope_is_unknown_not_a_missing_column(tmp_path, header_row, header_column):
    path = tmp_path / "moved-header.xlsx"
    book = Workbook()
    book.active.cell(header_row, header_column, "工序名称")
    book.active.cell(header_row + 1, header_column, "装配")
    book.save(path)
    book.close()

    class Audit(SourceAudit):
        def operations(self):
            self.native().rows({"operation": ("工序名称",)})
            return "PASS", "工序已读取"

    rule = RuleInput(rule_id="rules", rules=[{"id": "R1"}])
    resource = {"supported_suffixes": (".xlsx",), "clauses": {"R1": {"method": "operations"}}}
    audit = Audit(AuditInput(audit_id="frozen", file_url=str(path)), rule, resource=resource)
    try:
        result = audit.evaluate("R1")
        assert result.status == "UNABLE" and result.checks_performed == 0
        assert "未覆盖完整范围" in result.suggestions
    finally:
        audit.close()
