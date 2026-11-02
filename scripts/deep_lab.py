"""Run one Deep recipe in a case workspace. Shared config stays in repo `.grok`."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, build_bundle_map, build_group_context_for_bundle, load_poc_cases
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
HOME = ROOT / ".grok"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing {name}")
    return value


def ensure_home() -> None:
    """Create the shared home once. Do not overwrite an existing config."""
    HOME.mkdir(mode=0o700, exist_ok=True)
    (HOME / "skills").mkdir(exist_ok=True)
    config = HOME / "config.toml"
    if config.exists():
        return
    model = _env("LLM_MODEL")
    base_url = _env("LLM_BASE_URL")
    key = _env("LLM_API_KEY")
    text = (
        "[models]\n"
        f"default = {json.dumps(model)}\n"
        'default_reasoning_effort = "low"\n'
        f"[model.{json.dumps(model)}]\n"
        f"model = {json.dumps(model)}\n"
        f"name = {json.dumps(model)}\n"
        f"base_url = {json.dumps(base_url)}\n"
        'api_backend = "responses"\n'
        f"api_key = {json.dumps(key)}\n"
        "max_retries = 0\n"
        "[cli]\nauto_update = false\n"
        "[memory]\nenabled = false\n"
    )
    config.write_text(text)
    config.chmod(0o600)


def _role(seq: str) -> str:
    kind = POC_SEQ_TO_DOC_TYPE.get(seq)
    return kind.value[1] if kind is not None else seq


def prepare(recipe: str, bundle: str, file_name: str) -> tuple[Path, dict[str, str]]:
    template = ROOT / "recipes" / recipe / "prompt.md"
    if not template.is_file():
        raise SystemExit(f"missing recipe {template}")
    grouped = build_bundle_map(load_poc_cases())
    cases = grouped[bundle]
    context = build_group_context_for_bundle(cases)
    if len(context.by_doc_type) < 2:
        raise SystemExit(f"{bundle} has no group context")
    selected = [case for case in cases if case.file_name == file_name]
    if len(selected) != 1:
        raise SystemExit(f"{file_name} matched {len(selected)} files in {bundle}")
    case = selected[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = OUTPUT / recipe / bundle / f"{case.file_seq}-{case.file_path.stem}" / stamp
    files = work / "files"
    files.mkdir(parents=True)
    copied: dict[str, str] = {}
    for item in cases:
        destination = files / item.file_path.name
        if destination.exists():
            destination = files / f"{item.file_seq}-{item.file_path.name}"
        shutil.copy2(item.file_path, destination)
        copied[item.file_name] = str(destination.relative_to(work))
    target = copied[case.file_name]
    related = "\n".join(f"- {_role(item.file_seq)}: {copied[item.file_name]}" for item in cases if item.file_name != case.file_name)
    rules = "\n".join(f"{index}. {case.expected[index].rule_desc}" for index in sorted(case.expected))
    prompt = template.read_text(encoding="utf-8").format(target=target, related=related or "无", rules=rules)
    (work / "prompt.txt").write_text(prompt, encoding="utf-8")
    (work / "case.json").write_text(
        json.dumps(
            {
                "recipe": recipe,
                "bundle": bundle,
                "target": case.file_name,
                "seq": case.file_seq,
                "group_types": sorted(context.by_doc_type),
                "files": copied,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    expected = {str(index): case.expected[index].status for index in sorted(case.expected)}
    return work, expected


def run_grok(work: Path) -> int:
    grok = shutil.which("grok")
    if grok is None:
        raise SystemExit("grok is not on PATH")
    env = os.environ.copy()
    env["GROK_HOME"] = str(HOME)
    env["GROK_MEMORY"] = "0"
    env["GROK_DISABLE_AUTOUPDATER"] = "1"
    venv_bin = ROOT / ".venv" / "bin"
    if venv_bin.is_dir():
        env["PATH"] = str(venv_bin) + os.pathsep + env.get("PATH", "")
    command = [
        grok,
        "--agent",
        "grok-build",
        "--cwd",
        str(work),
        "--prompt-file",
        str(work / "prompt.txt"),
        "--model",
        _env("LLM_MODEL"),
        "--reasoning-effort",
        os.environ.get("OPENAI_REASONING_EFFORT", "low").strip() or "low",
        "--output-format",
        "streaming-messages-json",
        "--max-turns",
        "16",
        "--sandbox",
        "off",
        "--permission-mode",
        "bypassPermissions",
    ]
    with (work / "stdout.jsonl").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
        completed = subprocess.run(command, cwd=work, env=env, stdout=stdout, stderr=stderr, timeout=900)
    return completed.returncode


def score(work: Path, expected: dict[str, str]) -> dict:
    path = work / "result.json"
    if not path.is_file():
        report = {"match": False, "error": "no result.json", "checks": []}
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            actual = {str(item["id"]): str(item["status"]).upper() for item in payload["checks"]}
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            report = {"match": False, "error": type(exc).__name__, "checks": []}
        else:
            rows = []
            for ident, want in expected.items():
                got = actual.get(ident, "MISSING")
                rows.append({"id": ident, "expected": want, "actual": got, "match": got == want})
            extra = sorted(set(actual) - set(expected))
            report = {"match": all(row["match"] for row in rows) and not extra, "checks": rows, "extra": extra}
    (work / "score.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"work": str(work), **report}, ensure_ascii=False), flush=True)
    return report


def _selected(bundle: str, seq: str, file_name: str, run_all: bool):
    if run_all:
        return load_poc_cases()
    cases = load_poc_cases(bundle=bundle, seq_set={seq} if seq else None)
    if file_name:
        cases = [case for case in cases if case.file_name == file_name]
    if not cases:
        raise SystemExit("no matching POC case")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", default="basic")
    parser.add_argument("--bundle", default="Blanc Spoiler-有特殊特性清单")
    parser.add_argument("--seq", default="11")
    parser.add_argument("--file", default="")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    ensure_home()
    selected = _selected(args.bundle, args.seq, args.file, args.all)
    summary = []
    for index, case in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] {case.bundle_id} {case.file_name}", flush=True)
        try:
            work, expected = prepare(args.recipe, case.bundle_id, case.file_name)
            code = run_grok(work)
            if code != 0:
                row = {"bundle": case.bundle_id, "file": case.file_name, "work": str(work), "match": False, "error": f"exit {code}"}
                print(json.dumps(row, ensure_ascii=False), flush=True)
            else:
                report = score(work, expected)
                row = {"bundle": case.bundle_id, "file": case.file_name, "work": str(work), "match": report["match"]}
        except subprocess.TimeoutExpired:
            row = {"bundle": case.bundle_id, "file": case.file_name, "match": False, "error": "timeout"}
            print(json.dumps(row, ensure_ascii=False), flush=True)
        except (OSError, SystemExit) as exc:
            row = {"bundle": case.bundle_id, "file": case.file_name, "match": False, "error": str(exc)}
            print(json.dumps(row, ensure_ascii=False), flush=True)
        summary.append(row)
    matched = sum(1 for row in summary if row["match"])
    totals = {"recipe": args.recipe, "cases": len(summary), "matched": matched, "results": summary}
    destination = OUTPUT / args.recipe / "summary.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(summary), "matched": matched, "summary": str(destination)}, ensure_ascii=False), flush=True)
    if matched != len(summary):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
