"""The packaged executable source wording stays tied to the current accepted PRD."""

import hashlib
import re
from pathlib import Path

from audit.precheck_sources import required_structure, structure_source


def test_l2_criteria_are_exact_original_155_and_template_identity_additions():
    source = Path(__file__).resolve().parents[3] / "assets" / "UAES" / "预检规则" / structure_source()["source"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == structure_source()["source_sha256"]
    section = source.read_text().split("## 2. L2 条目的")[1].split("## 3.")[0]
    entries, current = {}, None
    for line in section.splitlines():
        match = re.match(r"#### ([ULN]\d\d) ", line)
        if match:
            current = match[1]
            entries[current] = []
        if current and re.match(r"\| S\d+ \|", line):
            identifier, criterion, clause, outcome = [cell.strip() for cell in line.strip("|").split("|")]
            entries[current].append({"id": current + ":" + identifier, "criterion": criterion, "source_clause": clause, "outcome": outcome})
    assert len(entries) == 22 and sum(len(rows) for rows in entries.values()) == 155
    for key, rows in entries.items():
        assert list(required_structure(key)) == rows
    assert [row["id"] for row in required_structure("L13")][-3:] == ["L13:S8", "L13:S9", "L13:S10"]
    assert required_structure("E12") == ()


def test_l1_spec_wording_is_frozen_for_all_nineteen_customer_entries():
    from audit.precheck_sources import l1_criteria, l1_spec

    entries = ["U01", "U02", "U03", "U04", "U05", "U06", "U07", "U08", "L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "N01", "N02", "N03"]
    root = Path(__file__).resolve().parents[3] / "assets" / "UAES" / "预检规则"
    for entry in entries:
        spec = l1_spec(entry)
        assert spec and hashlib.sha256((root / spec["source"]).read_bytes()).hexdigest() == spec["source_sha256"]
        criteria = l1_criteria(entry)
        assert criteria and len({row["id"] for row in criteria}) == len(criteria)
        assert all(row["id"].startswith(entry + ":") and row["criterion"] for row in criteria)
    ids = {row["criterion_id"] for row in l1_criteria("N02")}
    assert "R08" in ids and not ids & {"R13", "R14"}
    assert not any(row["criterion_id"].startswith("EXE") for row in l1_criteria("L02"))
    assert l1_spec("L12") is None and l1_criteria("L12") == ()
