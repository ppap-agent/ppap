"""The packaged executable source wording stays tied to the current accepted PRD."""
import hashlib
import re
from pathlib import Path

from audit.precheck_sources import required_structure, structure_source


def test_l2_criteria_are_exact_original_155_and_template_identity_additions():
    source = Path(__file__).resolve().parents[3] / "assets" / "PPAP PRD v3.7" / "预检规则" / structure_source()["source"]
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
