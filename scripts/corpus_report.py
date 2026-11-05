"""Oracle coverage matrix and rule-outcome rates. Computed output, never a stored ledger."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from corpus_contracts.oracle import load_oracle
from corpus_contracts.product_round import BLOCKING, UNABLE

MAX_UNABLE_RATE = 0.05
# Live answers vary, so a few wrong ones are measured; a false PASS never is acceptable.
MAX_LIVE_BLOCKING_RATE = 0.02


def _rule_type(rule_ref: str) -> str:
    return rule_ref.rsplit(":", 1)[-1] if rule_ref.startswith("builtin:") else Path(rule_ref).name.removesuffix(".json")


def coverage(cases) -> dict[tuple[str, str, str], dict[str, Counter]]:
    """(dataset, rule type, clause) -> file format -> truth counts."""
    matrix: dict[tuple[str, str, str], dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for case in cases:
        for file in case.files:
            for rule, item in file.expected.items():
                matrix[(case.dataset, _rule_type(file.rule_ref), rule)][file.path.suffix.lower().lstrip(".")][item.status] += 1
    return matrix


def coverage_gaps(matrix) -> list[tuple[str, str, str, str]]:
    """Clauses that were never shown a passing or a failing original."""
    gaps = []
    for key, formats in sorted(matrix.items()):
        seen = {truth for counts in formats.values() for truth in counts}
        missing = {"PASS", "NG"} - seen
        if missing:
            gaps.append((*key, "/".join(sorted(missing))))
    return gaps


def read_results(directories: list[Path]) -> list[dict]:
    rows = []
    for directory in directories:
        for path in sorted(directory.glob("*.jsonl")):
            rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    return rows


def summarize(rows: list[dict]) -> dict[str, dict[str, float]]:
    rules = defaultdict(list)
    for row in rows:
        if row["kind"] == "rule":
            rules[row["dataset"]].append(row)
    summary = {}
    for dataset, items in sorted(rules.items()):
        total = len(items)
        counts = Counter(item["severity"] for item in items)
        summary[dataset] = {
            "rules": total,
            "blocking": counts[BLOCKING],
            "unable": counts[UNABLE],
            "false_pass": sum(item["actual"] == "PASS" and item["truth"] != "PASS" and item["severity"] == BLOCKING for item in items),
            "unable_rate": counts[UNABLE] / total,
            "blocking_rate": counts[BLOCKING] / total,
        }
    return summary


def violations(summary: dict[str, dict[str, float]], *, live: bool) -> list[str]:
    problems = []
    for dataset, values in summary.items():
        if values["unable_rate"] > MAX_UNABLE_RATE:
            problems.append(f"{dataset}: UNABLE rate {values['unable_rate']:.1%} exceeds {MAX_UNABLE_RATE:.0%}")
        if live and values["false_pass"]:
            problems.append(f"{dataset}: {values['false_pass']:.0f} false PASS")
        if live and values["blocking_rate"] > MAX_LIVE_BLOCKING_RATE:
            problems.append(f"{dataset}: wrong-result rate {values['blocking_rate']:.1%} exceeds {MAX_LIVE_BLOCKING_RATE:.0%}")
    return problems


def unstable_rules(rows: list[dict]) -> list[tuple[str, str, str, str, dict[str, int]]]:
    """Clauses that gave more than one answer for the same file across live rounds."""
    answers: dict[tuple[str, str, str, str], Counter] = defaultdict(Counter)
    for row in rows:
        if row["kind"] == "rule":
            answers[(row["dataset"], row["case"], row["file"], row["rule"])][row["actual"] or "ERR"] += 1
    return [(*key, dict(counts)) for key, counts in sorted(answers.items()) if len(counts) > 1]


def render(matrix, gaps, summary, unstable, rows) -> str:
    lines = ["# Corpus report", ""]
    files = [row for row in rows if row["kind"] == "file"]
    if summary:
        lines += ["| 数据集 | 条款结果 | 阻断 | UNABLE | 错误 PASS | UNABLE 率 |", "|---|---|---|---|---|---|"]
        lines += [f"| {name} | {v['rules']:.0f} | {v['blocking']:.0f} | {v['unable']:.0f} | {v['false_pass']:.0f} | {v['unable_rate']:.1%} |" for name, v in summary.items()]
        lines += ["", f"未判定条款：{sum(row['applicable'] - row['asserted'] for row in files)}", ""]
    if unstable:
        lines += ["## 同一文件答案不一致的条款", ""] + [f"- {dataset}/{case} {file} 条款 {rule}: {answers}" for dataset, case, file, rule, answers in unstable] + [""]
    lines += [f"## 缺样本的条款（{len(gaps)}）", "", "| 数据集 | 规则类型 | 条款 | 缺少 |", "|---|---|---|---|"]
    lines += [f"| {dataset} | {kind} | {rule} | {missing} |" for dataset, kind, rule, missing in gaps]
    lines += ["", "## 覆盖矩阵", "", "| 数据集 | 规则类型 | 条款 | 格式 | PASS | NG | UNABLE | NA |", "|---|---|---|---|---|---|---|---|"]
    for (dataset, kind, rule), formats in sorted(matrix.items()):
        for name, counts in sorted(formats.items()):
            lines.append(f"| {dataset} | {kind} | {rule} | {name} | {counts['PASS']} | {counts['NG']} | {counts['UNABLE']} | {counts['NA']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, action="append", default=[], help="Directory of rule-outcome JSONL files; repeat for several rounds")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true", help="Also enforce live-model limits")
    args = parser.parse_args(argv)
    matrix = coverage(load_oracle())
    gaps = coverage_gaps(matrix)
    rows = read_results(args.results)
    if args.results and not rows:
        print("No rule outcomes were reported", file=sys.stderr)
        return 1
    summary = summarize(rows)
    report = render(matrix, gaps, summary, unstable_rules(rows), rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as output:
            output.write(report.split("## 覆盖矩阵", 1)[0])
    problems = violations(summary, live=args.live)
    print(f"clauses={len(matrix)} missing_samples={len(gaps)} datasets={json.dumps(summary, ensure_ascii=False)}")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
