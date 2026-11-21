"""clause-check bench: run the whole skill on real clauses and judge what its reports find.

    list                      the cases
    run   --name N [--case ID ...] [--jobs J] [--model M] [--thinking T] [--timeout MIN]
                              one headless pi session per case runs the clause-check skill from scratch, in a
                              folder outside the home directory, /private/tmp/clause-check-bench/N/<case>/, that
                              holds only rules.json (this one clause) and files/ (the real files). It loads only a
                              read-only copy of the skill, inside sandbox-exec that hides the whole home directory
                              (repositories, credentials, Claude Code and earlier pi sessions) and every other case
                              folder. Afterwards the bench regenerates the report from the session's structured review,
                              so what is scored is what the skill's script says, and copies everything to
                              bench/clause-check/runs/N/<case>/
    score runs/N [--rejudge]  a judging Claude Code session (fixed model, started in an empty folder so it loads no
                              project instructions or memory) compares each report with the case's reference findings
                              and the out-of-scope list; writes judge.json per case and summary.md

Each run records the commit, digests of the skill and of cases.json, the model and the thinking level in run.json.
The reference findings are the starting point, not the truth: new findings the judge cannot match are listed for
the product owner, whose verdicts are added to cases.json over time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL = ROOT / ".claude" / "skills" / "clause-check"
DEFAULT_MODEL = "xai/grok-4.7"
JUDGE_MODEL = "claude-opus-5-5"
STAGE = Path("/private/tmp/clause-check-bench")
TOOLS = STAGE / "tools"
TOOL_PACKAGES = ("openpyxl", "xlrd")
PI_HOME = Path.home() / ".pi" / "agent"
MISSING = "【待补白话说明】"
TECH_WORDS = ("OWL", "ROBOT", "HermiT", "本体", "公理", "探针", "推理机", "可满足", "Probe", "__")
DENIED = "Operation not permitted"

ACTIVE: set[subprocess.Popen] = set()
ACTIVE_LOCK = threading.Lock()


def stop_sessions(signum, _frame) -> None:
    """Stopping the bench also stops every session it started (each runs in its own process group)."""
    with ACTIVE_LOCK:
        for process in ACTIVE:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    sys.exit(128 + signum)


def cases() -> dict:
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


def slug(case_id: str) -> str:
    return case_id.replace(":", "-")


def clause_text(case: dict) -> str:
    rules = json.loads((ROOT / case["rules"]).read_text(encoding="utf-8"))
    return next(rule["rule_to_check"] for rule in rules["rules"] if rule["id"] == case["id"])


def digest(paths: list[Path], base: Path) -> str:
    """A short digest of files' names and contents, so two runs can be told apart by what they ran."""
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(str(path.relative_to(base)).encode() + b"\0" + path.read_bytes())
    return hasher.hexdigest()[:12]


def skill_digest(folder: Path) -> str:
    return digest([path for path in folder.rglob("*") if path.is_file() and "__pycache__" not in path.parts], folder)


def cases_digest() -> str:
    return digest([HERE / "cases.json"], HERE)


def claude(prompt: str, *options: str) -> dict:
    """One headless Claude Code session in an empty folder: no project instructions, no project memory."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="clause-check-judge-") as empty:
        done = subprocess.run(["claude", "-p", prompt, "--output-format", "json", "--model", JUDGE_MODEL, *options], cwd=empty, capture_output=True, text=True)
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        result = {"is_error": True, "result": (done.stdout + done.stderr)[-4000:]}
    result.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
    return result


def stage(case: dict, name: str) -> Path:
    """The case folder: only this clause's rule and its real files. The session works here."""
    folder = STAGE / name / slug(case["id"])
    (folder / "files").mkdir(parents=True)
    document = json.loads((ROOT / case["rules"]).read_text(encoding="utf-8"))
    rule = next(rule for rule in document["rules"] if rule["id"] == case["id"])
    (folder / "rules.json").write_text(json.dumps({"title": document.get("title", ""), "rules": [rule]}, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in case["files"]:
        shutil.copy(ROOT / path, folder / "files" / Path(path).name)
    return folder


def stage_skill(name: str) -> Path:
    """One read-only copy of the skill per run, outside the home directory."""
    target = STAGE / name / "skill" / SKILL.name
    shutil.copytree(SKILL, target, ignore=shutil.ignore_patterns("__pycache__", "*.robot", "*.report.md"))
    for path in [target, *target.rglob("*")]:
        path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    return target


def tools() -> Path:
    """A Python environment outside the home directory with what reading the real files needs; built once."""
    python = TOOLS / "bin" / "python"
    if not python.exists():
        subprocess.run(["uv", "venv", "--quiet", str(TOOLS)], check=True)
        subprocess.run(["uv", "pip", "install", "--quiet", "--python", str(python), *TOOL_PACKAGES], check=True)
    return TOOLS / "bin"


def others(work: Path) -> list[Path]:
    """Every case folder under the stage except this one (this run's and earlier runs')."""
    return [folder for folder in STAGE.glob("*/*") if folder.is_dir() and folder.name != "skill" and folder.parent != TOOLS and folder.resolve() != work.resolve()]


def sandbox_profile(work: Path, skill: Path) -> str:
    """macOS Seatbelt profile. Later rules win: the home directory is closed (only whether a path exists can be
    checked) except pi's own configuration, whose earlier sessions stay closed; other case folders are closed; the
    skill and tools can be read, not written."""
    rules = ["(version 1)", "(allow default)"]
    closed = [Path.home(), Path("/private/tmp/claude-501"), *others(work)]
    rules.append("(deny file-read* file-write* " + " ".join(f'(subpath "{path.resolve()}")' for path in closed) + ")")
    # Tools resolve paths by checking each parent exists: allow existence checks, not reading or listing.
    rules.append(f'(allow file-read-metadata (subpath "{Path.home().resolve()}"))')
    rules.append(f'(allow file-read* file-write* (subpath "{PI_HOME.resolve()}"))')
    rules.append(f'(deny file-read* file-write* (subpath "{(PI_HOME / "sessions").resolve()}"))')
    rules.append(f'(deny file-write* (subpath "{skill.resolve()}") (subpath "{TOOLS.resolve()}"))')
    rules.append(f'(allow file-read* file-write* (subpath "{work.resolve()}"))')
    return "".join(rules)


def environment(work: Path) -> dict[str, str]:
    """The caller's environment without anything pointing into the home directory or the repository."""
    home = Path.home()
    paths = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry and not Path(entry).resolve().is_relative_to(home)]
    temp = work / ".tmp"
    temp.mkdir(exist_ok=True)
    clean = {key: value for key, value in os.environ.items() if key not in ("VIRTUAL_ENV", "PYTHONPATH", "PWD", "OLDPWD") and not key.startswith("UV_")}
    clean = {key: value for key, value in clean.items() if str(ROOT) not in value}
    return {**clean, "PATH": os.pathsep.join([str(tools()), *paths]), "TMPDIR": str(temp)}


def pi(prompt: str, work: Path, skill: Path, model: str, thinking: str, timeout_minutes: int) -> dict:
    """One headless pi session started in `work`, with only the skill copy, inside sandbox-exec."""
    session_dir = work / ".session"
    session_dir.mkdir(exist_ok=True)
    command = ["sandbox-exec", "-p", sandbox_profile(work, skill)]
    command += ["pi", "-p", "--mode", "json", "--session-dir", str(session_dir), "--no-skills", "--skill", str(skill)]
    command += ["--no-context-files", "--no-approve", "--no-extensions", "--no-mcp", "--model", model, "--thinking", thinking, prompt]
    started = time.monotonic()
    summary: dict = {"models": [], "turns": 0, "tool_calls": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "result": "", "is_error": True}
    # Its own process group, so a timeout or stopping the bench ends pi and everything it started.
    process = subprocess.Popen(command, cwd=work, env=environment(work), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    with ACTIVE_LOCK:
        ACTIVE.add(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout_minutes * 60)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        summary["timed_out"] = True
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(process)
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
            summary["input_tokens"] += (usage.get("input") or 0) + (usage.get("cacheWrite") or 0)
            summary["cache_read_tokens"] += usage.get("cacheRead") or 0
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


def denials(transcript: Path) -> list[str]:
    """Tool calls the sandbox refused, found from their results however the path was written."""
    calls: dict[str, str] = {}
    refused = []
    for line in transcript.read_text(encoding="utf-8").splitlines() if transcript.exists() else []:
        message = json.loads(line).get("message") or {}
        if message.get("role") == "assistant":
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") not in ("text", "thinking"):
                    calls[block.get("id", "")] = json.dumps(block.get("arguments", block), ensure_ascii=False)[:200]
        elif message.get("role") == "toolResult" and DENIED in json.dumps(message.get("content"), ensure_ascii=False):
            refused.append(calls.get(message.get("toolCallId", ""), "?"))
    return refused


def review_file(work: Path, case_id: str) -> Path | None:
    """Select the session's latest clause review, not a generated report."""
    candidates = []
    for path in work.rglob("*.review.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("source", "").endswith(f"#{case_id}"):
            candidates.append(path)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def regenerate(review: Path, skill: Path, out: Path) -> int:
    """Regenerate both reports from the original review JSON for scoring."""
    done = subprocess.run(
        [sys.executable, str(skill / "scripts" / "check.py"), str(review),
         "--report", str(out / "report.md"), "--data", str(out / "report.json")],
        cwd=review.parent, capture_output=True, text=True,
    )
    (out / "check.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    return done.returncode


def prompt_for(case: dict) -> str:
    """The task, in terms of the case folder only: the clause, its rule file and the real files beside it."""
    lines = [f"使用 clause-check 技能检查条款 {case['id']}。", "- 规则文件：当前目录下的 rules.json。", "- 当前目录就是工作目录。"]
    if case["files"]:
        lines.append("- 要对照的真实文件在 files/ 下：" + "、".join(f"`{Path(path).name}`" for path in case["files"]) + "。")
    lines.append("- 按技能做到脚本退出码为 0。最后只回复报告文件的路径。")
    return "\n".join(lines)


def run_case(case: dict, name: str, skill: Path, model: str, thinking: str, timeout_minutes: int) -> str:
    """Run one case whose folders `main` already staged; results are copied to bench/ afterwards."""
    out = HERE / "runs" / name / slug(case["id"])
    work = STAGE / name / slug(case["id"])
    result = pi(prompt_for(case), work, skill, model, thinking, timeout_minutes)
    sessions = sorted((work / ".session").glob("*.jsonl"))
    if sessions:
        shutil.copy(sessions[-1], out / "transcript.jsonl")
    result["denials"] = denials(out / "transcript.jsonl")
    review = review_file(work, case["id"])
    result["review"] = review.name if review else None
    result["check_exit"] = None
    result["report_matches"] = None
    if review:
        shutil.copy(review, out / review.name)
        written = review.with_name(review.name.removesuffix(".review.json") + ".report.md")
        if written.exists():
            shutil.copy(written, out / "session-report.md")
        result["check_exit"] = regenerate(review, skill, out)
        if written.exists() and (out / "report.md").exists():
            result["report_matches"] = written.read_text(encoding="utf-8") == (out / "report.md").read_text(encoding="utf-8")
    (out / "session.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    state = "超时" if result.get("timed_out") else "失败" if result["is_error"] else "完成"
    complete = {0: "结构校验通过", 1: "审查记录不合格", 2: "来源不可用", None: "没有审查记录"}.get(result["check_exit"], "检查出错")
    return f"{case['id']}：{state}，{complete}，{result['duration_ms'] // 1000} 秒"


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
3. 列出报告里按条款原文明显说错的结论，例如把能让同一份文件得出不同结论的几种理解说成“结论都相同”，把有结论的情况说成“原文没有给出结论”，把不冲突的情况说成“原文同时给出通过和不通过”。
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
    verdict["judge_models"] = sorted(result.get("modelUsage", {})) or [JUDGE_MODEL]
    verdict["cases_digest"] = cases_digest()
    return verdict


def plain_problems(report: str, case: dict) -> tuple[list[str], int]:
    """(technical words or identifiers, number of missing plain descriptions) in a report."""
    document = json.loads((ROOT / case["rules"]).read_text(encoding="utf-8"))
    rule = next(rule for rule in document["rules"] if rule["id"] == case["id"])
    # The report's heading quotes the rule file's title and the rule's name; their words are the clause's own.
    sources = [rule["rule_to_check"], rule.get("category", ""), document.get("title", ""), *(Path(path).name for path in case["files"]), case["id"]]
    allowed = {word.casefold() for text in sources for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text)}
    words = [word for word in TECH_WORDS if word in report]
    words += [word for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", report) if word.casefold() not in allowed and word not in ("PASS", "NG")]
    return words, report.count(MISSING)


def score(run: Path, rejudge: bool) -> None:
    data = cases()
    info = json.loads((run / "run.json").read_text(encoding="utf-8")) if (run / "run.json").exists() else {}
    header = "| 条款 | 结构校验 | 报告与脚本一致 | 找到 | 部分 | 漏掉 | 新发现 | 越界 | 说错 | 重复问题 | 技术词 | 待补 | 被沙箱拒绝 | 用时 | 输出 tokens |"
    rows = [header, "|" + "---|" * (header.count("|") - 1)]
    details = []
    judged_with: set[str] = set()
    for case in data["cases"]:
        folder = run / slug(case["id"])
        if not folder.exists():
            continue
        session = json.loads((folder / "session.json").read_text(encoding="utf-8")) if (folder / "session.json").exists() else {}
        complete = {0: "是", 1: "否", 2: "无法使用", None: "没有审查记录"}.get(session.get("check_exit"), "出错")
        matches = {True: "是", False: "否（手改或没重跑）", None: "—"}[session.get("report_matches")]
        refused = session.get("denials", [])
        tail = f" {len(refused)} | {session.get('duration_ms', 0) // 1000}s | {session.get('output_tokens', 0)} |"
        report_path = folder / "report.md"
        if not report_path.exists():
            rows.append(f"| {case['id']} | {complete} | {matches} |" + " |" * 9 + tail)
            continue
        report = report_path.read_text(encoding="utf-8")
        verdict_path = folder / "judge.json"
        if rejudge or not verdict_path.exists():
            verdict_path.write_text(json.dumps(judge(case, report, data["out_of_scope"]), ensure_ascii=False, indent=2), encoding="utf-8")
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        judged_with.add(f"{'、'.join(verdict.get('judge_models', []))}，参照 {verdict.get('cases_digest', '?')}")
        results = [item.get("result") for item in verdict.get("references", [])]
        extra, wrong, repeated = verdict.get("extra", []), verdict.get("wrong", []), verdict.get("repeated", [])
        words, missing = plain_problems(report, case)
        rows.append(
            f"| {case['id']} | {complete} | {matches} | {results.count('found')} | {results.count('partial')} | {results.count('missed')} | "
            f"{sum(1 for item in extra if item.get('kind') == 'new')} | {sum(1 for item in extra if item.get('kind') == 'out_of_scope')} | "
            f"{len(wrong)} | {len(repeated)} | {len(words)} | {missing} |" + tail
        )
        details.append(f"\n## {case['id']}\n")
        details += [f"- {item.get('result')}：{item.get('reference')}" for item in verdict.get("references", []) if item.get("result") != "found"]
        details += [f"- {item.get('kind')}：{item.get('finding')}（{item.get('why')}）" for item in extra]
        details += [f"- 说错：{item.get('statement')}（{item.get('why')}）" for item in wrong]
        details += [f"- 重复问题：{item.get('question')}（同：{item.get('same_as')}）" for item in repeated]
        details += [f"- 被沙箱拒绝：{call}" for call in refused]
        if words:
            details.append(f"- 技术词：{'、'.join(sorted(set(words)))}")
        if "error" in verdict:
            details.append(f"- 评判失败：{verdict['error'][:200]}")
    lines = [f"# clause-check bench：{run.name}\n"]
    if info:
        lines.append(f"- 提交 {info.get('commit', '?')}{'（有未提交改动）' if info.get('dirty') else ''}，技能 {info.get('skill_digest', '?')}，参照 {info.get('cases_digest', '?')}")
        lines.append(f"- 模型 {info.get('model', '?')}，思考强度 {info.get('thinking', '?')}")
    if judged_with:
        lines.append(f"- 评判：{'；'.join(sorted(judged_with))}" + ("（参照已和运行时不同）" if any(info.get("cases_digest", "") not in item for item in judged_with) else ""))
    text = "\n".join(lines) + "\n\n" + "\n".join(rows) + "\n" + "\n".join(details) + "\n"
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
    run = HERE / "runs" / args.name
    if run.exists() or (STAGE / args.name).exists():
        print(f"运行 {args.name} 已存在，换一个 --name", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, stop_sessions)
    signal.signal(signal.SIGINT, stop_sessions)
    # Every case folder is staged before any session starts, so each session's sandbox can name the others.
    tools()
    skill = stage_skill(args.name)
    for case in chosen:
        (run / slug(case["id"])).mkdir(parents=True)
        stage(case, args.name)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", str(SKILL), str(HERE)], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    info = {"name": args.name, "commit": commit, "dirty": dirty, "skill_digest": skill_digest(skill), "cases_digest": cases_digest(), "model": args.model, "thinking": args.thinking}
    (run / "run.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for line in pool.map(lambda case: run_case(case, args.name, skill, args.model, args.thinking, args.timeout), chosen):
            print(line, flush=True)
    score(run, rejudge=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
