"""A related original is a source mismatch, missing, or unknown; only a mismatch or a missing one asks for a file."""

import pytest
from audit.checker_lib.source import SourceAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from openpyxl import Workbook


def _book(path, title, part):
    book = Workbook()
    book.active.append([title])
    book.active.append(["零件号", part])
    book.active.append(["特性名称", "特性类别"])
    book.active.append(["孔径", "CC"])
    book.save(path)
    book.close()
    return str(path)


class Audit(SourceAudit):
    compared: list[str] = []

    def peer(self):
        self.related(("特殊特性清单",), same=("part",))
        # Reached only after the pair is established: the content comparison.
        type(self).compared.append(self.audit_file.audit_id)
        return "PASS", "内容一致"


def _run(tmp_path, peer_part):
    target = AuditInput(audit_id="target", file_url=_book(tmp_path / "pfmea.xlsx", "PFMEA", "P12345"))
    peers = [] if peer_part is None else [AuditInput(audit_id="list", file_url=_book(tmp_path / "list.xlsx", "特殊特性清单", peer_part))]
    group = GroupContextInput(by_doc_type={"PFMEA": [target], **({"特殊特性清单": peers} if peers else {})}, by_audit_id={item.audit_id: item for item in [target, *peers]})
    rule = RuleInput(rule_id="rules", rules=[{"id": "R1"}])
    Audit.compared = []
    audit = Audit(target, rule, group, resource={"supported_suffixes": (".xlsx",), "clauses": {"R1": {"method": "peer"}}})
    try:
        return audit.evaluate("R1")
    finally:
        audit.close()


def test_a_related_original_for_another_part_is_ng_of_nature_source_mismatch_without_comparison(tmp_path):
    result = _run(tmp_path, "P99999")
    assert result.status == "NG" and result.checks_performed == 1
    assert result.supply is not None and result.supply.model_dump(exclude_none=True) == {
        "kind": "source_mismatch",
        "document": "特殊特性清单",
        "part_number": "P12345",
        "found_part_number": "P99999",
        "found_file_version_id": "list",
    }
    # The basis names both part numbers; the content comparison never ran.
    assert "P99999" in result.suggestions and "P12345" in result.suggestions
    assert Audit.compared == []


def test_an_absent_related_original_is_missing_with_the_part_to_supply(tmp_path):
    result = _run(tmp_path, None)
    assert result.status == "NG"
    assert result.supply is not None and result.supply.model_dump(exclude_none=True) == {"kind": "missing", "document": "特殊特性清单", "part_number": "P12345"}
    assert Audit.compared == []


@pytest.mark.parametrize("peer_part", ["待填", "/"])
def test_an_unreadable_related_identity_is_unknown_and_asks_for_no_file(tmp_path, peer_part):
    result = _run(tmp_path, peer_part)
    assert result.status == "UNABLE"
    assert result.supply is None
    assert Audit.compared == []


def test_the_same_part_is_compared(tmp_path):
    result = _run(tmp_path, "P12345")
    assert result.status == "PASS" and result.supply is None
    assert Audit.compared == ["target"]
