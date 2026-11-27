"""Audit one batch. The host schedules the work; each auditor does one small thing and leaves a record.

    prepare    no model: convert, find the tables, have the vision model read scanned pages and pictures once
    read       one reader per file notes cited facts into the dossier every auditor of the batch shares
    audit      one auditor per clause gives that clause a verdict
    focus      a clause left without a verdict goes to another auditor at the higher effort
    challenge  a second auditor tries to overturn the verdict (off unless asked for)
    arbitrate  a clause the challenger overturned goes to a third, who settles it on the originals

A clause is a session of its own, with its own time limit: a hard clause cannot starve the others,
and no session carries a whole file's reading in its context. A step runs only when the one before
left it something to do.

The batch directory holds ``batch.json`` and ``files/``; results are written to ``results.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

from auditor.originals import Original, Unreadable, squeeze, transcribe
from auditor.verdicts import Task

AGENT = Path(__file__).parent / "agent"
SKILL = AGENT / "skills" / "ppap-audit"
# Seconds between the limit an auditor is told and the moment its session is stopped.
GRACE = 30
_lessons = threading.Lock()


def prepare_pi() -> None:
    """Give Pi a private copy of its configuration: it writes locks beside it, and the source stays read-only.

    The copy declares that the model takes pictures, so the auditor looks at a rendered page itself
    instead of only reading another model's transcription of it.
    """
    source = os.environ.get("AUDITOR_PI_SOURCE")
    if not source:
        return
    home = Path(tempfile.mkdtemp(prefix="pi-agent-"))
    for name in ("models.json", "auth.json", "settings.json"):
        if (Path(source) / name).is_file():
            shutil.copyfile(Path(source) / name, home / name)
            (home / name).chmod(0o600)
    models = home / "models.json"
    if models.is_file() and os.environ.get("AUDITOR_BLIND") != "1":
        configured = json.loads(models.read_text(encoding="utf-8"))
        for provider in configured.get("providers", {}).values():
            for model in provider.get("models", []):
                model.setdefault("input", ["text", "image"])
        models.write_text(json.dumps(configured), encoding="utf-8")
    os.environ["PI_CODING_AGENT_DIR"] = str(home)


def standing_instructions() -> Path:
    """The auditor's standing instructions and its skill as one system prompt: nothing to fetch before starting."""
    parts = [(AGENT / "AUDITOR.md").read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in sorted((SKILL / "references").glob("*.md"))]
    handle, path = tempfile.mkstemp(prefix="auditor-", suffix=".md")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write("\n\n".join(parts))
    return Path(path)


def pi(batch: Path, work: Path, phase: str, prompt: str, *, options: argparse.Namespace, thinking: str, seconds: int) -> dict[str, Any]:
    """One auditor at work in ``work`` until it finishes or its time is up. Returns what the step cost."""
    logs = work / "logs"
    logs.mkdir(exist_ok=True)
    command = ["pi", "-p", "--mode", "json", "--no-session", "--no-context-files", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-mcp"]
    command += ["--append-system-prompt", str(options.instructions), "--tools", "read,bash,write", "--model", options.model, "--thinking", thinking, prompt]
    started = time.monotonic()
    environment = {
        **os.environ,
        "AUDIT_PHASE": phase,
        "AUDIT_CACHE": str(batch / "cache"),
        "AUDIT_DOSSIER": str(batch / "dossier.jsonl"),
        "AUDIT_DEADLINE": str(time.time() + seconds - GRACE),
        "AUDIT_SECONDS": str(seconds - GRACE),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
    }
    timed_out = False
    with (logs / f"{phase}.jsonl").open("wb") as stdout, (logs / f"{phase}.err").open("wb") as stderr:
        process = subprocess.Popen(command, cwd=work, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
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
    looks = [entry for entry in _jsonl(work / ".audit" / "looks.jsonl") if entry.get("phase") == phase]
    cost["looks"] = len(looks)
    cost["look_seconds"] = round(sum(float(entry.get("seconds") or 0) for entry in looks))
    return {**cost, "requests": requests, "tool_calls": tools, "input_tokens": usage["input"], "cached_input_tokens": usage["cacheRead"], "output_tokens": usage["output"]}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            found.append(entry)
    return found


def _prompt(name: str, **values: Any) -> str:
    return (AGENT / f"{name}.md").read_text(encoding="utf-8").format(**values)


def _workdir(batch: Path, relative: str, files: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> Path:
    work = batch / relative
    work.mkdir(parents=True, exist_ok=True)
    if not (work / "files").exists():
        (work / "files").symlink_to(os.path.relpath(batch / "files", work), target_is_directory=True)
    (work / "files.json").write_text(json.dumps(files, ensure_ascii=False, indent=1), encoding="utf-8")
    if rules is not None:
        (work / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
    return work


def prepare(batch: Path, files: list[dict[str, Any]], options: argparse.Namespace) -> dict[str, Any]:
    """Make once what every auditor would otherwise make again. No clause is read here and nothing is judged."""
    started = time.monotonic()

    def one(item: dict[str, Any]) -> dict[str, int]:
        try:
            return Original(batch / item["path"], batch / "cache").warm(read=not options.no_transcribe)
        except Exception:
            # Preparation only saves time later: a file it cannot prepare is read by the auditor as it is.
            return {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        made = list(pool.map(one, files))
    return {"seconds": round(time.monotonic() - started), **{key: sum(part.get(key, 0) for part in made) for key in ("pages", "pictures", "transcriptions")}}


def read_file(batch: Path, number: int, item: dict[str, Any], files: list[dict[str, Any]], kinds: dict[str, str], options: argparse.Namespace) -> dict[str, Any]:
    """One reader goes through one file and notes what the clause auditors will all need."""
    listed = [{"name": other["name"], "path": other["path"], "role": "target" if other["name"] == item["name"] else "other"} for other in files]
    work = _workdir(batch, f"read/{number:02d}", listed, {"rules": []})
    kept = work / "step.json"
    if kept.is_file():
        return dict(json.loads(kept.read_text(encoding="utf-8")))
    step = pi(batch, work, "read", _prompt("read", file=item["name"], seconds=options.read_seconds - GRACE), options=options, thinking=options.thinking, seconds=options.read_seconds)
    lessons = Task(work).lessons()
    kind = kinds.get(item["name"])
    if options.experience and kind and lessons:
        learned = Path(options.experience) / f"{kind}.md"
        with _lessons:
            learned.parent.mkdir(parents=True, exist_ok=True)
            known = learned.read_text(encoding="utf-8").splitlines() if learned.is_file() else []
            learned.write_text("\n".join([*known, *(f"- {lesson}" for lesson in lessons if f"- {lesson}" not in known)]) + "\n", encoding="utf-8")
    kept.write_text(json.dumps({**step, "file": item["name"]}, ensure_ascii=False), encoding="utf-8")
    return {**step, "file": item["name"]}


def _dossier(task: Task) -> str:
    notes = task.notes()
    if not notes:
        return "案卷目前是空的。"
    lines = [f"- [{note['file']} {note['where']}] {note['topic']}：{note['fact']}" for note in notes]
    return "案卷（同批审核员记下的事实，出处都重新核对过；用到关键事实时仍要自己到出处看一眼）：\n" + "\n".join(lines)


def _looked(work: Path, phase: str) -> str:
    """Where an earlier auditor of this clause looked, so the next one does not start from nothing."""
    seen = []
    for entry in _jsonl(work / ".audit" / "looks.jsonl"):
        if entry.get("phase") != phase or entry.get("command") in ("submit", "status", "note", "notes", "remember", "uphold"):
            continue
        parts = [str(entry.get("command")), str(entry.get("file") or "")]
        parts += [f"--{key} {entry[key]}" for key in ("sheet", "range", "page", "clip", "picture", "name") if entry.get(key) not in (None, "")]
        if isinstance(entry.get("text"), list):
            parts.append(" ".join(map(str, entry["text"])))
        line = " ".join(part for part in parts if part)
        if line not in seen:
            seen.append(line)
    return "\n".join(f"- look {line}" for line in seen[-50:]) or "（没有记录）"


def _confirm_seen(task: Task, verdict: dict[str, Any], limit: int = 12) -> None:
    """Have the vision model transcribe what a 'seen' citation points at, and note whether it reads the same.

    A second reading of the same place by another model. It marks agreement; it refuses nothing, since
    a stamp or a signature may have no text to transcribe.
    """
    from auditor.tools import _clip

    done: dict[tuple[str, str, str], str] = {}
    for point in verdict["points"]:
        for item in point["evidence"]:
            if item.get("verified") != "seen" or len(done) >= limit:
                continue
            key = (item["file"], item["where"], item.get("clip", ""))
            if key not in done:
                try:
                    original = task.original(item["file"])
                    if item["where"].startswith("page:"):
                        done[key] = squeeze(transcribe(original.render(int(item["where"][5:]), 200, _clip(item.get("clip")))))
                    else:
                        done[key] = squeeze(transcribe(original.picture(item["where"].removeprefix("image:"))))
                except (Unreadable, ValueError):
                    continue
            quote = squeeze(item["quote"])
            pairs = {quote[index : index + 2] for index in range(len(quote) - 1)} or {quote}
            item["transcription_agrees"] = sum(1 for pair in pairs if pair in done[key]) / len(pairs) >= 0.6


def audit_clause(batch: Path, number: int, target: dict[str, Any], rule: dict[str, Any], files: list[dict[str, Any]], kinds: dict[str, str], options: argparse.Namespace) -> dict[str, Any]:
    """One clause of one target through the workflow. Never raises: a failed step leaves the clause without a verdict."""
    started = time.monotonic()
    clause = rule["id"]
    listed = [{"name": item["name"], "path": item["path"], "role": "target" if item["name"] == target["name"] else "other"} for item in files]
    work = _workdir(batch, f"audits/{number:02d}/{re.sub(r'[^0-9A-Za-z]+', '-', clause)}", listed, {"title": target["rules"].get("title", ""), "rules": [rule]})
    kept = work / "result.json"
    if kept.is_file():
        # A clause already settled by an interrupted run of this batch is not audited again.
        return dict(json.loads(kept.read_text(encoding="utf-8")))
    task = Task(work)
    # Clauses refer to one another: the document's whole set is at hand, so nobody goes looking for it elsewhere.
    (work / "clauses.json").write_text(json.dumps(target["rules"], ensure_ascii=False, indent=1), encoding="utf-8")
    kind = str(target.get("document_type") or "")
    learned = Path(options.experience) / f"{kind}.md" if options.experience and kind else None
    brief = {
        "target": target["name"],
        "kind": f"（{target['rules'].get('title') or kind}）" if target["rules"].get("title") or kind else "",
        "files": "、".join(f"{item['name']}{'（' + kinds[item['name']] + '）' if item['name'] in kinds else ''}" for item in files),
        "clause": clause,
        "category": rule.get("category") or "",
        "rule": rule["rule_to_check"],
        "dossier": _dossier(task),
        "experience": f"\n以前审同类文件记下的读法（只是线索，不是这份文件的事实）：\n{learned.read_text(encoding='utf-8')}\n" if learned and learned.is_file() else "",
    }
    steps = []
    try:
        steps.append(pi(batch, work, "audit", _prompt("audit", **brief, seconds=options.seconds - GRACE), options=options, thinking=options.thinking, seconds=options.seconds))
        # No verdict in time: a fresh auditor at the higher effort, told where the first one looked.
        if clause not in task.recorded() and not options.no_focus:
            steps.append(
                pi(
                    batch,
                    work,
                    "focus",
                    _prompt("focus", **brief, looked=_looked(work, "audit"), seconds=options.seconds - GRACE),
                    options=options,
                    thinking=options.hard_thinking,
                    seconds=options.seconds,
                )
            )
        first = task.recorded().get(clause)
        disputed = False
        if first and options.challenge:
            budget = max(180, options.seconds * 2 // 3)
            verdict = json.dumps(first, ensure_ascii=False, indent=1)
            steps.append(pi(batch, work, "challenge", _prompt("challenge", **brief, verdict=verdict, seconds=budget - GRACE), options=options, thinking=options.thinking, seconds=budget))
            second = task.recorded().get(clause)
            # A changed status is a dispute between two auditors: a third settles it on the originals.
            if second and second.get("phase") == "challenge" and second["status"] != first["status"]:
                disputed = True
                steps.append(
                    pi(
                        batch,
                        work,
                        "arbitrate",
                        _prompt("arbitrate", **brief, first=verdict, second=json.dumps(second, ensure_ascii=False, indent=1), seconds=options.seconds - GRACE),
                        options=options,
                        thinking=options.hard_thinking,
                        seconds=options.seconds,
                    )
                )
        final = task.recorded().get(clause)
        if final:
            try:
                _confirm_seen(task, final)
            except Exception:
                # A confirmation is an extra reading; the verdict stands without it.
                pass
    except Exception as error:
        first, final, disputed = None, None, False
        steps.append(
            {
                "phase": "failed",
                "error": f"{type(error).__name__}: {error}"[:300],
                "seconds": 0,
                "timed_out": False,
                "requests": 0,
                "tool_calls": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            }
        )
    upheld = task.upheld()
    row: dict[str, Any] = {"clause": clause}
    if final is None:
        row.update(status="UNABLE", reason="审核员没有在时限内给出结论。", points=[], unaudited=True)
    else:
        row.update(status=final["status"], reason=final["reason"], points=final["points"], settled_by=final.get("phase"))
    if first:
        row["first_status"] = first["status"]
        if options.challenge:
            # Whether the second auditor reached a decision before its time ran out.
            row["challenged"] = clause in upheld or final is not None and final.get("phase") in ("challenge", "arbitrate")
    if clause in upheld:
        row["upheld"] = upheld[clause]
    if disputed:
        row["disputed"] = True
    row["seconds"] = round(time.monotonic() - started)
    seen = work / ".audit" / "seen.jsonl"
    result = {"row": row, "steps": [{**step, "clause": clause} for step in steps], "refused": task.refused(), "looks": len(seen.read_text(encoding="utf-8").splitlines()) if seen.is_file() else 0}
    kept.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def audit_batch(batch: Path, options: argparse.Namespace, pool: ThreadPoolExecutor) -> dict[str, Any]:
    """One batch on the shared pool of auditors: its files are read first, then its clauses are audited."""
    started = time.monotonic()
    spec = json.loads((batch / "batch.json").read_text(encoding="utf-8"))
    files, targets = spec["files"], spec["targets"]
    kinds = {target["name"]: str(target["rules"].get("title") or target.get("document_type") or "") for target in targets}
    prepared = prepare(batch, files, options)
    reading_started = time.monotonic()
    readers = [] if options.no_read else [future.result() for future in [pool.submit(read_file, batch, number, item, files, kinds, options) for number, item in enumerate(files, 1)]]
    reading = round(time.monotonic() - reading_started)
    jobs = [(number, target, rule) for number, target in enumerate(targets, 1) for rule in target["rules"]["rules"]]
    # The hardest-looking clauses first, so the long ones are not all left for the end.
    jobs.sort(key=lambda job: -len(job[2]["rule_to_check"]))
    auditing_started = time.monotonic()
    futures = {(number, rule["id"]): pool.submit(audit_clause, batch, number, target, rule, files, kinds, options) for number, target, rule in jobs}
    done = {key: future.result() for key, future in futures.items()}
    auditing = round(time.monotonic() - auditing_started)
    results = []
    for number, target in enumerate(targets, 1):
        parts = [done[(number, rule["id"])] for rule in target["rules"]["rules"]]
        results.append(
            {
                "name": target["name"],
                "document_type": str(target.get("document_type") or ""),
                "rows": [part["row"] for part in parts],
                "steps": [step for part in parts for step in part["steps"]] + [step for step in readers if step["file"] == target["name"]],
                "refused": sum(part["refused"] for part in parts),
                "looks": sum(part["looks"] for part in parts),
            }
        )
    audited = {target["name"] for target in targets}
    outcome = {
        "model": options.model,
        "thinking": options.thinking,
        "hard_thinking": options.hard_thinking,
        "seconds": options.seconds,
        "concurrency": options.concurrency,
        "wall": {"total": round(time.monotonic() - started), "prepare": prepared["seconds"], "read": reading, "audit": auditing},
        "prepare": prepared,
        "notes": len(_jsonl(batch / "dossier.jsonl")),
        "other_steps": [step for step in readers if step["file"] not in audited],
        "targets": results,
    }
    (batch / "results.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=1), encoding="utf-8")
    return outcome


def audit_batches(batches: list[Path], options: argparse.Namespace) -> list[dict[str, Any]]:
    """Every batch draws on the same pool, so the number of auditors at work is one limit for the whole run."""
    options.instructions = standing_instructions()
    with ThreadPoolExecutor(max_workers=max(1, options.concurrency)) as pool, ThreadPoolExecutor(max_workers=len(batches)) as batchwise:
        return list(batchwise.map(lambda batch: audit_batch(batch, options, pool), batches))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m auditor.run", description="审核一个或几个批次")
    parser.add_argument("batch", type=Path, nargs="+", help="含 batch.json 和 files/ 的目录；给几个就共用同一组审核员")
    parser.add_argument("--model", default="local/grok-4.7")
    parser.add_argument("--thinking", default="medium", help="阅卷、审核、质证的推理强度")
    parser.add_argument("--hard-thinking", default="high", help="专审、裁决的推理强度")
    parser.add_argument("--seconds", type=int, default=600, help="审一条条款的时限；专审、裁决同此，质证为三分之二")
    parser.add_argument("--read-seconds", type=int, default=360, help="阅卷一份文件的时限")
    parser.add_argument("--concurrency", type=int, default=12, help="同时工作的审核员数，所有批次合计")
    parser.add_argument("--no-read", action="store_true")
    parser.add_argument("--no-focus", action="store_true")
    parser.add_argument("--challenge", action="store_true", help="每条结论再由第二名审核员质证，改判的交第三名裁决")
    parser.add_argument("--no-transcribe", action="store_true", help="准备阶段不预先转写扫描页和图片")
    parser.add_argument("--experience", help="读法经验的目录：审前读入，阅卷后追加")
    options = parser.parse_args(argv)
    prepare_pi()
    started = time.monotonic()
    outcomes = audit_batches([batch.resolve() for batch in options.batch], options)
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for target in outcome["targets"]:
            for row in target["rows"]:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"batches": len(outcomes), "statuses": counts, "seconds": round(time.monotonic() - started)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
