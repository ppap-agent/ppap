"""clause-check bench: run the whole skill on real clauses and judge what its reports find.

    list                      the cases
    run   --name N [--case ID ...] [--jobs J] [--model M]
                              one headless Claude Code session per case runs the clause-check skill from scratch;
                              its work stays in .local/clause-check-bench/N/<case>/, the report, session
                              summary and transcript are copied to bench/clause-check/runs/N/<case>/
    score runs/N [--rejudge]  a judging session compares each report with the case's reference findings and the
                              out-of-scope list; writes judge.json per case and summary.md for the run

The reference findings are the starting point, not the truth: new findings the judge cannot match are listed for
the product owner, whose verdicts are added to cases.json over time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "clause-check" / "scripts"
WORK = ROOT / ".local" / "clause-check-bench"
MISSING = "【待补白话说明】"
TECH_WORDS = ("OWL", "ROBOT", "HermiT", "本体", "公理", "探针", "推理机", "可满足", "Probe", "__")


def cases() -> dict:
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


def slug(case_id: str) -> str:
    return case_id.replace(":", "-")


def clause_text(case: dict) -> str:
    rules = json.loads((ROOT / case["rules"]).read_text(encoding="utf-8"))
    return next(rule["rule_to_check"] for rule in rules["rules"] if rule["id"] == case["id"])


def claude(prompt: str, *options: str) -> dict:
    """One headless Claude Code session; returns its JSON result (result text, cost, duration, turns)."""
    started = time.monotonic()
    done = subprocess.run(["claude", "-p", prompt, "--output-format", "json", *options], cwd=ROOT, capture_output=True, text=True)
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        result = {"is_error": True, "result": (done.stdout + done.stderr)[-4000:]}
    result.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
    return result


def run_case(case: dict, name: str, model: str | None) -> str:
    out = HERE / "runs" / name / slug(case["id"])
    work = WORK / name / slug(case["id"])
    if out.exists() or work.exists():
        return f"{case['id']}：已存在，跳过（换一个 --name）"
    out.mkdir(parents=True)
    work.mkdir(parents=True)
    files = "、".join(f"`{name}`" for name in case["files"])
    prompt = "\n".join(
        [
            f"使用 clause-check 技能检查条款 {case['id']}（规则文件 {case['rules']}）。",
            f"- 工作目录用 {work.relative_to(ROOT)}/，代替技能里的 .local/clause-check/<类型>/；形式化文件、推理结果和报告都放在这里。",
            f"- 需要对照的真实文件：{files}。在 assets/ 下按文件名查找，现有判定见 assets/corpus/ 里的真值表。" if files else "- 这条不需要对照真实文件。",
            "- 不要读取 bench/ 目录下的任何内容。",
            "- 不要修改仓库里已有的任何文件，不提交。",
            "- 按技能把白话说明补到没有缺项、没有质量问题为止。最后只回复报告文件的路径。",
        ]
    )
    settings = json.dumps({"permissions": {"deny": ["Read(./bench/**)", "Edit(./bench/**)", "Write(./bench/**)"]}})
    options = ["--permission-mode", "bypassPermissions", "--settings", settings]
    if model:
        options += ["--model", model]
    result = claude(prompt, *options)
    (out / "session.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    transcript = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT)) / f"{result.get('session_id')}.jsonl"
    if transcript.exists():
        shutil.copy(transcript, out / "transcript.jsonl")
    reports = sorted(work.rglob("*.report.md")) or sorted(work.rglob("report.md"))
    for report in reports:
        shutil.copy(report, out / report.name)
    for ontology in work.rglob("*.ofn"):
        if ontology.parent == work or ontology.parent.parent == work:
            shutil.copy(ontology, out / ontology.name)
    state = "失败" if result.get("is_error") else "完成"
    return f"{case['id']}：{state}，报告 {len(reports)} 份，{result.get('duration_ms', 0) // 1000} 秒，${result.get('total_cost_usd', 0):.2f}"


JUDGE = """你是条款检查报告的评判。只比对，不评价条款本身，不替规则做选择。

条款 {case_id} 原文：
{clause}

参照发现（已知原文没说清的地方）：
{references}

越界（报告不应出现的分析）：
{out_of_scope}

待评判的报告：
<<<
{report}
>>>

逐条判断每个参照发现在报告里是否被指出：found（意思相同即可，不要求措辞一致）、partial（只说到一部分）、missed。
再列出报告里不对应任何参照发现的问题：kind 为 out_of_scope（属于上面的越界）或 new（其他新发现，留给人判断）。
只输出 JSON，不要其他文字：
{{"references": [{{"reference": "...", "result": "found|partial|missed", "where": "报告中对应的句子，没有则为空"}}],
  "extra": [{{"finding": "报告原句或概括", "kind": "new|out_of_scope", "why": "一句话"}}]}}"""


def judge(case: dict, report: str, out_of_scope: list[str]) -> dict:
    prompt = JUDGE.format(
        case_id=case["id"],
        clause=clause_text(case),
        references="\n".join(f"- {item}" for item in case["reference"]),
        out_of_scope="\n".join(f"- {item}" for item in out_of_scope),
        report=report,
    )
    result = claude(prompt, "--disallowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "Agent")
    text = result.get("result", "")
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = json.loads(text[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        verdict = {"error": text[-2000:]}
    verdict["cost_usd"] = result.get("total_cost_usd", 0)
    return verdict


def plain_problems(report: str, case: dict) -> tuple[int, int]:
    """(technical words or identifiers, missing plain descriptions) in a report."""
    allowed = {word.casefold() for text in [clause_text(case), *case["files"]] for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text)}
    words = sum(report.count(word) for word in TECH_WORDS)
    words += sum(1 for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", report) if word.casefold() not in allowed and word not in ("PASS", "NG"))
    return words, report.count(MISSING)


def score(run: Path, rejudge: bool) -> None:
    data = cases()
    rows = ["| 条款 | 找到 | 部分 | 漏掉 | 新发现 | 越界 | 技术词 | 待补 | 用时 | 费用 |", "|---|---|---|---|---|---|---|---|---|---|"]
    details = []
    for case in data["cases"]:
        folder = run / slug(case["id"])
        if not folder.exists():
            continue
        reports = sorted(folder.glob("*report.md"))
        session = json.loads((folder / "session.json").read_text(encoding="utf-8")) if (folder / "session.json").exists() else {}
        seconds, cost = session.get("duration_ms", 0) // 1000, session.get("total_cost_usd", 0)
        if not reports:
            rows.append(f"| {case['id']} | 没有报告 |  |  |  |  |  |  | {seconds}s | ${cost:.2f} |")
            continue
        report = reports[0].read_text(encoding="utf-8")
        verdict_path = folder / "judge.json"
        if rejudge or not verdict_path.exists():
            verdict_path.write_text(json.dumps(judge(case, report, data["out_of_scope"]), ensure_ascii=False, indent=2), encoding="utf-8")
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        results = [item.get("result") for item in verdict.get("references", [])]
        extra = verdict.get("extra", [])
        words, missing = plain_problems(report, case)
        rows.append(
            f"| {case['id']} | {results.count('found')} | {results.count('partial')} | {results.count('missed')} | "
            f"{sum(1 for item in extra if item.get('kind') == 'new')} | {sum(1 for item in extra if item.get('kind') == 'out_of_scope')} | "
            f"{words} | {missing} | {seconds}s | ${cost:.2f} |"
        )
        details.append(f"\n## {case['id']}\n")
        details += [f"- {item.get('result')}：{item.get('reference')}" for item in verdict.get("references", []) if item.get("result") != "found"]
        details += [f"- {item.get('kind')}：{item.get('finding')}（{item.get('why')}）" for item in extra]
        if "error" in verdict:
            details.append(f"- 评判失败：{verdict['error'][:200]}")
    text = f"# clause-check bench：{run.name}\n\n" + "\n".join(rows) + "\n" + "\n".join(details) + "\n"
    (run / "summary.md").write_text(text, encoding="utf-8")
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    running = commands.add_parser("run")
    running.add_argument("--name", default=time.strftime("%Y%m%d-%H%M"))
    running.add_argument("--case", action="append", default=[])
    running.add_argument("--jobs", type=int, default=2)
    running.add_argument("--model")
    scoring = commands.add_parser("score")
    scoring.add_argument("run", type=Path)
    scoring.add_argument("--rejudge", action="store_true")
    args = parser.parse_args(argv)

    data = cases()
    if args.command == "list":
        for case in data["cases"]:
            print(f"{case['id']}\t参照 {len(case['reference'])} 条\t{case['rules']}")
        return 0
    if args.command == "score":
        score(args.run.resolve(), args.rejudge)
        return 0
    chosen = [case for case in data["cases"] if not args.case or case["id"] in args.case]
    if not chosen:
        print("没有匹配的条款", file=sys.stderr)
        return 2
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for line in pool.map(lambda case: run_case(case, args.name, args.model), chosen):
            print(line, flush=True)
    score(HERE / "runs" / args.name, rejudge=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
