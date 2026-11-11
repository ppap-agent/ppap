"""File recognition regression: every corpus target matches its own customer rule."""

import csv

from audit.presets import customer_presets, preset_document_type
from corpus_contracts.oracle import CASES_PATH, REPO_ROOT
from worker.intake.file_analysis import analyze_file


def _targets() -> list[tuple[str, object, str]]:
    # Recognition runs no Suite, so it reads the case table, not the reviewed rules.
    rows = []
    for path in (CASES_PATH, REPO_ROOT / "assets/corpus/uaes-cases.csv"):
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows += [row for row in csv.DictReader(source) if row["角色"] == "target"]
    return sorted({(row["Checker组"], REPO_ROOT / row["原件"], row["提交文件名"]) for row in rows})


_TARGETS = _targets()


def _rules(customer: str) -> list[dict]:
    # A task offers every preset of its customer, as the product does.
    return [
        {"id": preset.entry_id, "title": preset.title, "kind": "engine", "type": preset_document_type(preset), "customers": [customer], "rule_profile": "v4"}
        for preset in customer_presets()
        if preset.customer == customer
    ]


def pytest_generate_tests(metafunc):
    if "target" not in metafunc.fixturenames:
        return
    ftype = metafunc.config.getoption("--ftype")
    targets = [item for item in _TARGETS if not ftype or (item[1].suffix.lower() == ".pdf") == (ftype == "pdf")]
    metafunc.parametrize("target", targets, ids=[f"{group}/{filename}" for group, _path, filename in targets])


def test_target_matches_its_customer_rule(target):
    group, path, filename = target
    customer, entry = group.split(":")
    result = analyze_file(filename, path.read_bytes(), _rules(customer), customer_preference=customer, scoped=True)
    assert (result["matching_status"], result["matched_rule_id"]) == ("matched", entry), result["notices"]
