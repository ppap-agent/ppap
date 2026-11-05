"""Table-driven business regression: one complete file combination per item."""

import json
import os
from pathlib import Path

import pytest
from corpus_contracts.oracle import OracleCase, build_round, load_oracle
from corpus_contracts.product_round import BLOCKING, compare_file, format_verdicts, severity
from product import run_round_from_bytes


def _is_pdf_case(case: OracleCase) -> bool:
    return any(file.path.suffix.lower() == ".pdf" for file in case.files)


def pytest_generate_tests(metafunc):
    if "audit_case" not in metafunc.fixturenames:
        return
    cases = load_oracle(metafunc.config.getoption("--dataset"))
    ftype = metafunc.config.getoption("--ftype")
    if ftype:
        # A combination runs whole, once: any PDF member makes it a PDF case.
        cases = [case for case in cases if _is_pdf_case(case) == (ftype == "pdf")]
    if not cases:
        raise pytest.UsageError("No oracle combinations selected")
    metafunc.parametrize("audit_case", cases, ids=lambda case: case.id)


def _write_report(rows: list[dict]) -> None:
    directory = os.environ.get("PPAP_CORPUS_REPORT_DIR")
    if not directory:
        return
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    with (path / f"rules-{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
        output.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_audit_case(audit_case: OracleCase, model_replay):
    work, blobs, totals = build_round(audit_case)
    result = run_round_from_bytes(work, blobs)
    rows, failures = [], []
    for file in audit_case.files:
        if file.role != "target":
            continue
        rows.append({"kind": "file", "dataset": audit_case.dataset, "case": audit_case.case_id, "file": file.filename, "applicable": totals[file.file_id], "asserted": len(file.expected)})
        verdicts = compare_file(file_name=file.filename, version_id=file.file_id, expected={rule: item.status for rule, item in file.expected.items()}, result=result, setup_error=None)
        for verdict in verdicts:
            level = severity(verdict, file.expected[verdict.rule_index].gap_status)
            where = {"dataset": audit_case.dataset, "case": audit_case.case_id, "file": file.filename}
            rows.append({"kind": "rule", **where, "rule": verdict.rule_index, "truth": verdict.expected, "actual": verdict.actual, "severity": level})
            if level == BLOCKING:
                failures.append(verdict)
    _write_report(rows)
    if failures:
        pytest.fail(format_verdicts(failures) + (f"\nreplay: {model_replay.path}" if model_replay is not None else ""))
