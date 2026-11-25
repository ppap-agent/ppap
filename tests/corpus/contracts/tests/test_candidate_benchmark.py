"""A benchmark is resubmitted from its originals and submission roles alone, and every row keeps a place in the comparison."""

import json
from pathlib import Path

from corpus_contracts.benchmark_diff import category
from corpus_contracts.candidate_run import benchmark_batches

BENCHMARK = Path(__file__).resolve().parents[4] / "assets/uaes/rulebook/v1.0/candidates/m4/benchmark"


def test_benchmark_batches_submit_every_file_by_its_submission_role_under_an_opaque_name():
    inventory = [json.loads(line) for line in (BENCHMARK / "file-inventory.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    runs = benchmark_batches(BENCHMARK)
    assert [name for name, _ in runs] == [json.loads(line)["batch"] for line in (BENCHMARK / "batches.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    for name, batch in runs:
        rows = [row for row in inventory if row["batch"] == name]
        root = BENCHMARK.parents[6]
        # Every submitted file reports, a defect batch's carried-over files included; nothing is carried silently.
        assert sorted(batch.files) == sorted(root / row["file"] for row in rows if row["submission_role"] == "submitted")
        assert sorted(batch.references) == sorted(root / row["file"] for row in rows if row["submission_role"] == "reference")
        assert not batch.others
        # The directory-derived name, which carries part numbers, never becomes the run's name.
        assert batch.part.startswith("benchmark-") and name not in batch.id


def test_a_row_without_a_conclusion_is_kept_and_differences_are_named():
    assert category(None, "PASS") == "没有输出"
    assert category("PASS", "PASS") == "一致"
    assert category("PASS", "NG") == "程序通过、参考不通过"
    assert category("NG", "PASS") == "程序不通过、参考通过"
    assert category("NG", "UNABLE") == "参考待确认、程序给了结论"
    assert category("UNABLE", "NA") == "程序待确认、参考有结论"
    assert category("NA", "NG") == "不适用对不上"
    assert category("ERR", "PASS") == "技术错误"
