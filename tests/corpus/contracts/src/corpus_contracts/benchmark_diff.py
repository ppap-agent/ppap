"""Set a candidate benchmark run beside the benchmark's reference judgments, row by row (docs/rules.md, step 7).

    uv run --no-sync python -m corpus_contracts.benchmark_diff <benchmark directory> <run JSONL> ... [--out <directory>]

The run is ``candidate_run --benchmark --json`` output. Rows align on (batch, file, clause) over the benchmark's fixed
expected rows; a row the run left without a conclusion stays in the table as "没有输出". The reference judgments are a
model's, not human truth: this lays the differences out for analysis and computes no accuracy.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

# Production gives the checker programs of one file this many seconds (agent.execution.budget).
CHECKER_BUDGET_SECONDS = 180

CATEGORIES = (
    "一致",
    "程序通过、参考不通过",
    "程序不通过、参考通过",
    "参考待确认、程序给了结论",
    "程序待确认、参考有结论",
    "不适用对不上",
    "技术错误",
    "没有输出",
)


def category(program: str | None, reference: str) -> str:
    """Where one row falls; `program` is None when the run gave the row no conclusion."""
    if program is None:
        return "没有输出"
    if program == reference:
        return "一致"
    if program in {"PASS", "NG", "NA", "UNABLE"}:
        if (program, reference) == ("PASS", "NG"):
            return "程序通过、参考不通过"
        if (program, reference) == ("NG", "PASS"):
            return "程序不通过、参考通过"
        if reference == "UNABLE":
            return "参考待确认、程序给了结论"
        if program == "UNABLE":
            return "程序待确认、参考有结论"
        return "不适用对不上"
    return "技术错误"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compare(benchmark: Path, runs: list[Path]) -> dict[str, Any]:
    expected = _jsonl(benchmark / "expected-rows.jsonl")
    reference = {(row["batch"], row["file"], row["clause"]): row for row in _jsonl(benchmark / "reference-judgments.jsonl")}
    inventory = _jsonl(benchmark / "file-inventory.jsonl")
    rows = [row for path in runs for row in _jsonl(path)]
    findings = {(row["batch"], row["source"], row["clause"]): row for row in rows if row["kind"] == "finding"}
    recognition = {(row["batch"], row["source"]): row for row in rows if row["kind"] == "recognition"}
    ran = {row["batch"] for row in rows}

    aligned = []
    for row in expected:
        key = (row["batch"], row["file"], row["clause"])
        if row["batch"] not in ran:
            continue  # a batch this run did not include is not a missing conclusion
        found, judged = findings.get(key), reference[key]
        program = found["status"] if found else None
        aligned.append(
            {
                "batch": row["batch"],
                "file": row["file"],
                "clause": row["clause"],
                "category": category(program, judged["verdict"]),
                "program": program,
                "reference": judged["verdict"],
                "program_reason": found["reason"] if found else "",
                "reference_reason": judged["reason"],
                "recognition": {k: recognition.get((row["batch"], row["file"]), {}).get(k) for k in ("state", "document_type", "notices")},
            }
        )
    expected_keys = {(row["batch"], row["file"], row["clause"]) for row in expected}
    outside = [row for key, row in sorted(findings.items()) if key not in expected_keys]

    # Each file as recognition took it, against the benchmark's expected type and role (output checks only).
    clause_types = {key[:2]: key[2].split(":")[0] for key in findings}
    files = []
    for item in inventory:
        if item["batch"] not in ran:
            continue
        seen = recognition.get((item["batch"], item["file"]), {})
        audited_as = clause_types.get((item["batch"], item["file"]))
        expected_as = item["document_type"] if item["expected_role"] == "target" else "比对件"
        actual_as = audited_as or ("比对件" if seen.get("state") == "reference" else f"未进入审核（{seen.get('state') or '无识别结果'}）")
        files.append({"batch": item["batch"], "file": item["file"], "expected": expected_as, "actual": actual_as, "agrees": expected_as == actual_as, "notices": seen.get("notices") or []})
    slow = sorted(
        ({"batch": row["batch"], "file": row["source"], "seconds": row["seconds"]} for row in rows if row["kind"] == "timing" and row["seconds"] > CHECKER_BUDGET_SECONDS),
        key=lambda row: -row["seconds"],
    )

    by_clause: dict[str, Counter[str]] = {}
    for row in aligned:
        by_clause.setdefault(row["clause"], Counter())[row["category"]] += 1
    return {
        "rows": aligned,
        "outside_expected": outside,
        "files": files,
        "over_budget": slow,
        "summary": {name: sum(1 for row in aligned if row["category"] == name) for name in CATEGORIES},
        "by_clause": {clause: dict(counts) for clause, counts in sorted(by_clause.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m corpus_contracts.benchmark_diff", description=__doc__.split("\n\n")[0])
    parser.add_argument("benchmark", type=Path, help="基准目录")
    parser.add_argument("runs", type=Path, nargs="+", help="candidate_run --benchmark --json 的输出")
    parser.add_argument("--out", type=Path, help="写出逐行对齐（alignment.jsonl）与汇总（summary.json）的目录")
    args = parser.parse_args(argv)
    result = compare(args.benchmark, args.runs)
    rows = result["rows"]
    print(f"对齐 {len(rows)} 行（{len({row['batch'] for row in rows})} 批）")
    for name, count in result["summary"].items():
        print(f"  {name}\t{count}")
    wrong = [row for row in result["files"] if not row["agrees"]]
    print(f"文件识别与基准预期不同 {len(wrong)} 份；基准范围外的结论 {len(result['outside_expected'])} 条；超过单文件检查时限 {CHECKER_BUDGET_SECONDS} 秒 {len(result['over_budget'])} 份")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / "alignment.jsonl").open("w", encoding="utf-8") as output:
            output.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        summary = {key: value for key, value in result.items() if key != "rows"}
        (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
