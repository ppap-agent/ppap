"""Audit one batch: a survey of the whole package, then each target file through a workflow that
follows what the previous step left.

    survey     one auditor reads every file once and notes cited facts into a dossier all audits share
    audit      one auditor gives every clause of a target a verdict
    focus      clauses left without a verdict go to a second auditor with fresh attention
    challenge  another auditor tries to overturn each verdict
    arbitrate  clauses the challenger overturned go to a third, who settles them on the originals

A step runs only when the one before left it something to do. The first pass uses the everyday
reasoning effort; focus and arbitrate, which only see the hard cases, use the higher one.

The batch directory holds ``batch.json`` and ``files/``; results are written to ``results.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from auditor.originals import Unreadable, squeeze, transcribe
from auditor.verdicts import Task

AGENT = Path(__file__).parent / "agent"
_lessons = threading.Lock()


def prepare_pi() -> None:
    """Give Pi a private copy of its configuration: it writes locks beside it, and the source stays read-only."""
    source = os.environ.get("AUDITOR_PI_SOURCE")
    if not source:
        return
    home = Path(tempfile.mkdtemp(prefix="pi-agent-"))
    for name in ("models.json", "auth.json", "settings.json"):
        if (Path(source) / name).is_file():
            shutil.copyfile(Path(source) / name, home / name)
            (home / name).chmod(0o600)
    os.environ["PI_CODING_AGENT_DIR"] = str(home)


def pi(work: Path, phase: str, prompt: str, *, model: str, thinking: str, seconds: int) -> dict[str, Any]:
    """One auditor at work in ``work`` until it finishes or its time is up. Returns what the step cost."""
    logs = work / "logs"
    logs.mkdir(exist_ok=True)
    command = ["pi", "-p", "--mode", "json", "--no-session", "--no-context-files", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-mcp"]
    command += ["--skill", str(AGENT / "skills" / "ppap-audit"), "--append-system-prompt", str(AGENT / "AUDITOR.md"), "--tools", "read,bash,write"]
    command += ["--model", model, "--thinking", thinking, prompt]
    started = time.monotonic()
    timed_out = False
    with (logs / f"{phase}.jsonl").open("wb") as stdout, (logs / f"{phase}.err").open("wb") as stderr:
        process = subprocess.Popen(command, cwd=work, env={**os.environ, "AUDIT_PHASE": phase, "PI_OFFLINE": "1", "PI_TELEMETRY": "0"}, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    cost: dict[str, Any] = {"phase": phase, "thinking": thinking, "seconds": round(time.monotonic() - started), "timed_out": timed_out, "exit": process.returncode}
    usage = {"input": 0, "output": 0, "cacheRead": 0}
    requests = tools = 0
    for line in (logs / f"{phase}.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        message: dict[str, Any] = event["message"] if isinstance(event.get("message"), dict) else {}
        if event.get("type") == "message_end" and message.get("role") == "assistant":
            requests += 1
            for key in usage:
                value = (message.get("usage") or {}).get(key)
                usage[key] += value if isinstance(value, int) else 0
            if message.get("errorMessage"):
                cost["error"] = str(message["errorMessage"])[:200]
        elif event.get("type") == "tool_execution_end":
            tools += 1
    return {**cost, "requests": requests, "tool_calls": tools, "input_tokens": usage["input"], "cached_input_tokens": usage["cacheRead"], "output_tokens": usage["output"]}


def _prompt(name: str, **values: Any) -> str:
    return (AGENT / f"{name}.md").read_text(encoding="utf-8").format(**values)


def _workdir(batch: Path, relative: str, files: list[dict[str, Any]]) -> Path:
    work = batch / relative
    work.mkdir(parents=True, exist_ok=True)
    if not (work / "files").exists():
        (work / "files").symlink_to(os.path.relpath(batch / "files", work), target_is_directory=True)
    (work / "files.json").write_text(json.dumps(files, ensure_ascii=False, indent=1), encoding="utf-8")
    return work


def survey(batch: Path, files: list[dict[str, Any]], options: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    work = _workdir(batch, "survey", [{"name": item["name"], "path": item["path"]} for item in files])
    cost = pi(work, "survey", _prompt("survey"), model=options.model, thinking=options.thinking, seconds=options.survey_seconds)
    return Task(work).notes(), cost


def _confirm_seen(task: Task, verdicts: dict[str, dict[str, Any]], limit: int = 24) -> None:
    """Have the vision model transcribe what a 'seen' citation points at, and note whether it reads the same.

    A second reading of the same place by another model. It marks agreement; it refuses nothing, since
    a stamp or a signature may have no text to transcribe.
    """
    from auditor.tools import _clip

    done: dict[tuple[str, str, str], str] = {}
    for verdict in verdicts.values():
        for point in verdict["points"]:
            for item in point["evidence"]:
                if item.get("verified") != "seen" or not item["where"].startswith("page:") or len(done) >= limit:
                    continue
                key = (item["file"], item["where"], item.get("clip", ""))
                if key not in done:
                    try:
                        done[key] = squeeze(transcribe(task.original(item["file"]).render(int(item["where"][5:]), 200, _clip(item.get("clip")))))
                    except (Unreadable, ValueError):
                        continue
                quote = squeeze(item["quote"])
                pairs = {quote[index : index + 2] for index in range(len(quote) - 1)} or {quote}
                item["transcription_agrees"] = sum(1 for pair in pairs if pair in done[key]) / len(pairs) >= 0.6


def audit_target(batch: Path, number: int, target: dict[str, Any], files: list[dict[str, Any]], dossier: list[dict[str, Any]], options: argparse.Namespace) -> dict[str, Any]:
    """One target file through the workflow. Never raises: a failed step leaves its clauses without a verdict."""
    listed = [{"name": item["name"], "path": item["path"], "role": "target" if item["name"] == target["name"] else "other"} for item in files]
    work = _workdir(batch, f"audits/{number:02d}", listed)
    (work / "rules.json").write_text(json.dumps(target["rules"], ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "dossier.json").write_text(json.dumps(dossier, ensure_ascii=False, indent=1), encoding="utf-8")
    kind = str(target.get("document_type") or "")
    learned = Path(options.experience) / f"{kind}.md" if options.experience and kind else None
    if learned and learned.is_file():
        shutil.copyfile(learned, work / "experience.md")
    task = Task(work)
    steps = [pi(work, "audit", _prompt("audit", seconds=options.seconds), model=options.model, thinking=options.thinking, seconds=options.seconds)]

    # Clauses left without a verdict: a fresh auditor, higher effort, only those clauses.
    left = [clause for clause in task.clauses if clause not in task.recorded()]
    if left and not options.no_focus:
        steps.append(pi(work, "focus", _prompt("focus", clauses="、".join(left), seconds=options.seconds // 2), model=options.model, thinking=options.hard_thinking, seconds=options.seconds // 2))

    first = task.recorded()
    disputed: list[str] = []
    if first and not options.no_challenge:
        (work / "first-verdicts.json").write_text(json.dumps(list(first.values()), ensure_ascii=False, indent=1), encoding="utf-8")
        budget = options.seconds * 2 // 3
        steps.append(pi(work, "challenge", _prompt("challenge", seconds=budget), model=options.model, thinking=options.thinking, seconds=budget))
        second = task.recorded()
        disputed = [clause for clause, verdict in second.items() if verdict.get("phase") == "challenge" and clause in first and verdict["status"] != first[clause]["status"]]
        # A changed status is a dispute between two auditors: a third settles it on the originals.
        if disputed:
            (work / "disputes.json").write_text(
                json.dumps([{"clause": clause, "first": first[clause], "second": second[clause]} for clause in disputed], ensure_ascii=False, indent=1), encoding="utf-8"
            )
            steps.append(
                pi(
                    work,
                    "arbitrate",
                    _prompt("arbitrate", clauses="、".join(disputed), seconds=options.seconds // 2),
                    model=options.model,
                    thinking=options.hard_thinking,
                    seconds=options.seconds // 2,
                )
            )

    final = task.recorded()
    try:
        _confirm_seen(task, final)
    except Exception:
        # A confirmation is an extra reading; the verdicts stand without it.
        pass
    upheld = task.upheld()
    rows = []
    for clause in task.clauses:
        verdict = final.get(clause)
        row: dict[str, Any] = {"clause": clause}
        if verdict is None:
            row.update(status="UNABLE", reason="审核员没有在时限内给出结论。", points=[], unaudited=True)
        else:
            row.update(status=verdict["status"], reason=verdict["reason"], points=verdict["points"], settled_by=verdict.get("phase"))
        if clause in first:
            row["first_status"] = first[clause]["status"]
        if clause in upheld:
            row["upheld"] = upheld[clause]
        if clause in disputed:
            row["disputed"] = True
        if clause in first and not options.no_challenge:
            # Whether the second auditor got to this clause before its time ran out.
            row["challenged"] = clause in upheld or verdict is not None and verdict.get("phase") in ("challenge", "arbitrate")
        rows.append(row)
    lessons = task.lessons()
    if learned and lessons:
        with _lessons:
            learned.parent.mkdir(parents=True, exist_ok=True)
            known = learned.read_text(encoding="utf-8").splitlines() if learned.is_file() else []
            learned.write_text("\n".join([*known, *(f"- {lesson}" for lesson in lessons if f"- {lesson}" not in known)]) + "\n", encoding="utf-8")
    seen = work / ".audit" / "seen.jsonl"
    return {
        "name": target["name"],
        "document_type": kind,
        "rows": rows,
        "steps": steps,
        "refused": task.refused(),
        "looks": len(seen.read_text(encoding="utf-8").splitlines()) if seen.is_file() else 0,
        "lessons": lessons,
    }


def audit_batch(batch: Path, options: argparse.Namespace) -> dict[str, Any]:
    spec = json.loads((batch / "batch.json").read_text(encoding="utf-8"))
    files, targets = spec["files"], spec["targets"]
    dossier: list[dict[str, Any]] = []
    survey_cost = None
    # A package of one file has nothing to cross-reference: its auditor orients itself.
    if not options.no_survey and len(files) > 1:
        dossier, survey_cost = survey(batch, files, options)
    with ThreadPoolExecutor(max_workers=max(1, options.concurrency)) as pool:
        results = list(pool.map(lambda pair: audit_target(batch, pair[0], pair[1], files, dossier, options), enumerate(targets, 1)))
    outcome = {
        "model": options.model,
        "thinking": options.thinking,
        "hard_thinking": options.hard_thinking,
        "seconds": options.seconds,
        "survey": {"notes": len(dossier), "step": survey_cost},
        "targets": results,
    }
    (batch / "results.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=1), encoding="utf-8")
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m auditor.run", description="审核一个批次")
    parser.add_argument("batch", type=Path, help="含 batch.json 和 files/ 的目录")
    parser.add_argument("--model", default="local/grok-4.7")
    parser.add_argument("--thinking", default="medium", help="勘查、审核、质证的推理强度")
    parser.add_argument("--hard-thinking", default="high", help="专审、裁决的推理强度")
    parser.add_argument("--seconds", type=int, default=1200, help="每份文件审核一遍的时限；其余步骤按它折算")
    parser.add_argument("--survey-seconds", type=int, default=600)
    parser.add_argument("--concurrency", type=int, default=3, help="同时审核几份文件")
    parser.add_argument("--no-survey", action="store_true")
    parser.add_argument("--no-focus", action="store_true")
    parser.add_argument("--no-challenge", action="store_true")
    parser.add_argument("--experience", help="读法经验的目录：审前读入，审后追加")
    options = parser.parse_args(argv)
    prepare_pi()
    outcome = audit_batch(options.batch, options)
    counts: dict[str, int] = {}
    for target in outcome["targets"]:
        for row in target["rows"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"targets": len(outcome["targets"]), "statuses": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
