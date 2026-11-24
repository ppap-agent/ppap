"""An observed zero or false flag retains its actual original value."""

from types import SimpleNamespace

from audit.checker_lib.uaes.native import UaesSourceAgent
from audit.checker_lib.uaes.v1.reader import Reader


def test_structured_witness_retains_zero_and_false():
    original = SimpleNamespace(audit_id="document-version")
    native = UaesSourceAgent.__new__(UaesSourceAgent)
    native.audit_file = original
    native._evidence_sources = []
    modern = Reader.__new__(Reader)
    modern.audit_file = original
    locator = {"kind": "xlsx_cell", "sheet": "sheet", "cell": "A1"}

    for reader, evidence in ((native, "_evidence_sources"), (modern, "_cited")):
        reader.witness(locator, 0)
        reader.witness(locator, False)
        assert [item.excerpt for item in getattr(reader, evidence)] == ["0", "False"]
