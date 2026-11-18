"""clause-check bench: run the whole skill on real clauses and judge what its reports find.

    list                      the cases
    run   --name N [--case ID ...] [--jobs J] [--model M] [--thinking T] [--timeout MIN]
                              one headless pi session per case runs the clause-check skill from scratch. It starts in
                              its own folder .local/clause-check-bench/N/<case>/ (its working directory), with only
                              that skill loaded and no context files, extensions or MCP, inside sandbox-exec that
                              hides bench/, earlier work and every other case folder. Afterwards the report,
                              session summary and transcript are copied to bench/clause-check/runs/N/<case>/
    score runs/N [--rejudge]  a judging Claude Code session (independent of the model under test) compares each
                              report with the case's reference findings and the out-of-scope list, and lists wrong
                              statements and repeated questions; writes judge.json per case and summary.md

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
SKILL = ROOT / ".claude" / "skills" / "clause-check"
DEFAULT_MODEL = "xai/grok-4.7"
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


def hidden(work: Path) -> list[Path]:
    """What the session working in `work` must not see: the references and run results, earlier clause-check work,
    every other case folder (of this run and earlier runs), and this machine's Claude Code sessions and scratch
    files. None of them is an ancestor of `work`, so the session's own directory stays an ordinary place to work."""
    others = [folder for folder in WORK.glob("*/*") if folder.is_dir() and folder.resolve() != work.resolve()]
    return [HERE, ROOT / ".local" / "clause-check", *others, Path.home() / ".claude", Path("/private/tmp/claude-501")]


def sandbox_profile(work: Path) -> str:
    """macOS Seatbelt profile: everything allowed except reading or writing the hidden paths."""
    deny = " ".join(f'(subpath "{path.resolve()}")' for path in hidden(work))
    return f"(version 1)(allow default)(deny file-read* file-write* {deny})"


def pi(prompt: str, work: Path, session_dir: Path, model: str, thinking: str, timeout_minutes: int) -> dict:
    """One headless pi session started in `work`, with only the clause-check skill, inside sandbox-exec."""
    command = ["sandbox-exec", "-p", sandbox_profile(work)]
    command += ["pi", "-p", "--mode", "json", "--session-dir", str(session_dir), "--no-skills", "--skill", str(SKILL)]
    command += ["--no-context-files", "--no-approve", "--no-extensions", "--no-mcp", "--model", model, "--thinking", thinking, prompt]
    started = time.monotonic()
    summary: dict = {"model": model, "thinking": thinking, "models": [], "turns": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "result": "", "is_error": True}
    environment = {**os.environ, "TMPDIR": str(session_dir)}
    process = subprocess.Popen(command, cwd=work, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout_minutes * 60)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        summary["timed_out"] = True
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool_execution_start":
            summary["tool_calls"] += 1
        elif event.get("type") == "message_end" and event.get("message", {}).get("role") == "assistant":
            message = event["message"]
            usage = message.get("usage") or {}
            summary["turns"] += 1
            summary["input_tokens"] += sum(usage.get(key) or 0 for key in ("input", "cacheRead", "cacheWrite"))
            summary["output_tokens"] += usage.get("output") or 0
            used = f"{message.get('provider')}/{message.get('model')}"
            if used not in summary["models"]:
                summary["models"].append(used)
            summary["result"] = "\n".join(block.get("text", "") for block in message.get("content", []) if block.get("type") == "text")
        elif event.get("type") == "agent_settled":
            summary["is_error"] = bool(event.get("aborted")) or process.returncode not in (0, None)
    if summary["is_error"] and stderr:
        summary["stderr"] = stderr[-2000:]
    summary["duration_ms"] = int((time.monotonic() - started) * 1000)
    return summary


def hidden_reads(transcript: Path, work: Path) -> list[str]:
    """Tool calls in the transcript that reach for a hidden path; the sandbox refuses them, this records the attempt."""
    paths = hidden(work)
    marks = [str(path.resolve()) for path in paths]
    marks += [str(path.resolve().relative_to(ROOT)) for path in paths if path.resolve().is_relative_to(ROOT)]
    marks += ["~/" + str(path.relative_to(Path.home())) for path in paths if path.is_relative_to(Path.home())]
    found = []
    for line in transcript.read_text(encoding="utf-8").splitlines() if transcript.exists() else []:
        message = json.loads(line).get("message") or {}
        for block in message.get("content") or [] if message.get("role") == "assistant" else []:
            if not isinstance(block, dict) or block.get("type") in ("text", "thinking"):
                continue
            text = json.dumps(block.get("arguments", block), ensure_ascii=False)
            if any(mark in text for mark in marks):
                found.append(text[:200])
    return found


def prompt_for(case: dict) -> str:
    """The task: the clause and the inputs by absolute path. The working directory is where the session starts."""
    lines = [
        f"使用 clause-check 技能检查条款 {case['id']}。",
        f"- 规则文件：{ROOT / case['rules']}",
        "- 当前目录就是工作目录。",
    ]
    if case["files"]:
        lines.append("- 要对照的真实文件：")
        lines += [f"  - {ROOT / path}" for path in case["files"]]
        lines.append(f"- 这些文件的现有判定在真值表 {ROOT / case['truth']} 里，按文件名查。")
    lines.append("- 按技能把白话说明补到没有缺项、没有质量问题为止。最后只回复报告文件的路径。")
    return "\n".join(lines)


def run_case(case: dict, name: str, model: str, thinking: str, timeout_minutes: int) -> str:
    """Run one case whose folders `main` already created; the session never touches bench/, results are copied after."""
    out = HERE / "runs" / name / slug(case["id"])
    work = WORK / name / slug(case["id"])
    session_dir = work / ".session"
    session_dir.mkdir()
    result = pi(prompt_for(case), work, session_dir, model, thinking, timeout_minutes)
    sessions = sorted(session_dir.glob("*.jsonl"))
    if sessions:
        shutil.copy(sessions[-1], out / "transcript.jsonl")
    result["hidden_reads"] = hidden_reads(out / "transcript.jsonl", work)
    (out / "session.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    reports = sorted(work.rglob("*.report.md")) or sorted(work.rglob("report.md"))
    for report in reports:
        shutil.copy(report, out / report.name)
    for ontology in work.rglob("*.ofn"):
        if ".robot" not in ontology.parent.name:
            shutil.copy(ontology, out / ontology.name)
    state = "超时" if result.get("timed_out") else "失败" if result["is_error"] else "完成"
    return f"{case['id']}：{state}，报告 {len(reports)} 份，{result['duration_ms'] // 1000} 秒，{result['input_tokens'] + result['output_tokens']} tokens"


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

1. 逐条判断每个参照发现在报告里是否被指出：found（意思相同即可，不要求措辞一致）、partial（只说到一部分）、missed。
2. 列出报告里不对应任何参照发现的问题：kind 为 out_of_scope（属于上面的越界）或 new（其他新发现，留给人判断）。
3. 列出报告里按条款原文明显说错的结论，例如把能让同一份文件得出相反结论的几种理解说成“结论都相同”，把并不矛盾的情况说成“原文自相矛盾”。
4. 列出“需要回答的问题”里与前面某个问题问的是同一件事的问题。
只输出 JSON，不要其他文字：
{{"references": [{{"reference": "...", "result": "found|partial|missed", "where": "报告中对应的句子，没有则为空"}}],
  "extra": [{{"finding": "报告原句或概括", "kind": "new|out_of_scope", "why": "一句话"}}],
  "wrong": [{{"statement": "报告原句", "why": "一句话"}}],
  "repeated": [{{"question": "重复的问题", "same_as": "它重复的那个问题"}}]}}"""


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


def plain_problems(report: str, case: dict) -> tuple[list[str], int]:
    """(technical words or identifiers, number of missing plain descriptions) in a report."""
    sources = [clause_text(case), *(Path(path).name for path in case["files"]), case["id"]]
    allowed = {word.casefold() for text in sources for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text)}
    words = [word for word in TECH_WORDS if word in report]
    words += [word for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", report) if word.casefold() not in allowed and word not in ("PASS", "NG")]
    return words, report.count(MISSING)


def score(run: Path, rejudge: bool) -> None:
    data = cases()
    header = "| 条款 | 找到 | 部分 | 漏掉 | 新发现 | 越界 | 说错 | 重复问题 | 技术词 | 待补 | 越界读取 | 用时 | tokens | 模型 |"
    rows = [header, "|" + "---|" * (header.count("|") - 1)]
    details = []
    for case in data["cases"]:
        folder = run / slug(case["id"])
        if not folder.exists():
            continue
        reports = sorted(folder.glob("*report.md"))
        session = json.loads((folder / "session.json").read_text(encoding="utf-8")) if (folder / "session.json").exists() else {}
        seconds = session.get("duration_ms", 0) // 1000
        tokens = session.get("input_tokens", 0) + session.get("output_tokens", 0)
        models = "、".join(session.get("models") or []) or "未知"
        reads = session.get("hidden_reads", [])
        if not reports:
            rows.append(f"| {case['id']} | 没有报告 |" + " |" * 8 + f" {len(reads)} | {seconds}s | {tokens} | {models} |")
            continue
        report = reports[0].read_text(encoding="utf-8")
        verdict_path = folder / "judge.json"
        if rejudge or not verdict_path.exists():
            verdict_path.write_text(json.dumps(judge(case, report, data["out_of_scope"]), ensure_ascii=False, indent=2), encoding="utf-8")
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        results = [item.get("result") for item in verdict.get("references", [])]
        extra, wrong, repeated = verdict.get("extra", []), verdict.get("wrong", []), verdict.get("repeated", [])
        words, missing = plain_problems(report, case)
        rows.append(
            f"| {case['id']} | {results.count('found')} | {results.count('partial')} | {results.count('missed')} | "
            f"{sum(1 for item in extra if item.get('kind') == 'new')} | {sum(1 for item in extra if item.get('kind') == 'out_of_scope')} | "
            f"{len(wrong)} | {len(repeated)} | {len(words)} | {missing} | {len(reads)} | {seconds}s | {tokens} | {models} |"
        )
        details.append(f"\n## {case['id']}\n")
        details += [f"- {item.get('result')}：{item.get('reference')}" for item in verdict.get("references", []) if item.get("result") != "found"]
        details += [f"- {item.get('kind')}：{item.get('finding')}（{item.get('why')}）" for item in extra]
        details += [f"- 说错：{item.get('statement')}（{item.get('why')}）" for item in wrong]
        details += [f"- 重复问题：{item.get('question')}（同：{item.get('same_as')}）" for item in repeated]
        details += [f"- 越界读取：{read}" for read in reads]
        if words:
            details.append(f"- 技术词：{'、'.join(sorted(set(words)))}")
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
    running.add_argument("--model", default=DEFAULT_MODEL, help=f"pi model (default {DEFAULT_MODEL})")
    running.add_argument("--thinking", default="high")
    running.add_argument("--timeout", type=int, default=60, help="minutes per case")
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
    taken = [case["id"] for case in chosen if (HERE / "runs" / args.name / slug(case["id"])).exists() or (WORK / args.name / slug(case["id"])).exists()]
    if taken:
        print(f"运行 {args.name} 里已有 {'、'.join(taken)}，换一个 --name", file=sys.stderr)
        return 2
    # Every case folder exists before any session starts, so each session's sandbox can name the others.
    for case in chosen:
        (HERE / "runs" / args.name / slug(case["id"])).mkdir(parents=True)
        (WORK / args.name / slug(case["id"])).mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for line in pool.map(lambda case: run_case(case, args.name, args.model, args.thinking, args.timeout), chosen):
            print(line, flush=True)
    score(HERE / "runs" / args.name, rejudge=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
