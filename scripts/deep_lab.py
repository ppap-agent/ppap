"""Run one Deep recipe in a case workspace. Pi is the default; legacy Grok requires an explicit selection."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, build_bundle_map, build_group_context_for_bundle, load_poc_cases
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
LAB_HOME = ROOT / ".grok"
MODEL = "grok-4.7"
EFFORT = "xhigh"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing {name}")
    return value


def _config_text() -> str:
    base_url = _env("LLM_BASE_URL")
    key = _env("LLM_API_KEY")
    return (
        "[models]\n"
        f"default = {json.dumps(MODEL)}\n"
        f"default_reasoning_effort = {json.dumps(EFFORT)}\n"
        f"[model.{json.dumps(MODEL)}]\n"
        f"model = {json.dumps(MODEL)}\n"
        f"name = {json.dumps(MODEL)}\n"
        f"base_url = {json.dumps(base_url)}\n"
        'api_backend = "responses"\n'
        f"api_key = {json.dumps(key)}\n"
        "max_retries = 0\n"
        "[cli]\nauto_update = false\n"
        "[memory]\nenabled = false\n"
    )


def ensure_home() -> None:
    """Create the shared home only when it has no config. Never replace an existing model menu."""
    LAB_HOME.mkdir(mode=0o700, exist_ok=True)
    (LAB_HOME / "skills").mkdir(exist_ok=True)
    config = LAB_HOME / "config.toml"
    if config.exists():
        return
    config.write_text(_config_text())
    config.chmod(0o600)


def _role(seq: str) -> str:
    kind = POC_SEQ_TO_DOC_TYPE.get(seq)
    return kind.value[1] if kind is not None else seq


def prepare(recipe: str, bundle: str, file_name: str, effort: str, *, variant: str = "original", source: Path | None = None) -> tuple[Path, dict[str, str] | None]:
    template = ROOT / "recipes" / recipe / "prompt.md"
    if recipe != "checker" and not template.is_file():
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
    work = OUTPUT / recipe / effort / bundle / f"{case.file_seq}-{Path(case.file_name).stem}" / stamp
    files = work / "files"
    files.mkdir(parents=True)
    copied: dict[str, str] = {}
    for item in cases:
        destination = files / item.file_name
        if destination.exists():
            destination = files / f"{item.file_seq}-{item.file_name}"
        shutil.copy2(item.file_path, destination)
        copied[item.file_name] = str(destination.relative_to(work))
    if source is not None:
        destination = files / ("input-" + source.name)
        shutil.copy2(source, destination)
        previous = work / copied[file_name]
        if previous != destination:
            previous.unlink()
        copied[file_name] = str(destination.relative_to(work))
    if variant in {"renamed-sheet", "shifted-table"}:
        from openpyxl import load_workbook

        target_path = work / copied[case.file_name]
        if target_path.suffix.lower() not in {".xlsx", ".xlsm"}:
            raise SystemExit("layout variants require an Excel target")
        workbook = load_workbook(target_path)
        for index, sheet in enumerate(workbook.worksheets, 1):
            if variant == "renamed-sheet":
                sheet.title = f"供应商资料{index}"
                continue
            if any(cell.data_type == "f" for row in sheet for cell in row):
                raise SystemExit("shifted-table requires formula-free input")
            merges = [copy.copy(merged) for merged in sheet.merged_cells.ranges]
            if any(merged.min_row < 2 <= merged.max_row for merged in merges):
                raise SystemExit("shifted-table cannot split a merged cell at row 2")
            for merged in merges:
                sheet.unmerge_cells(str(merged))
            sheet.insert_rows(2, 20)
            for merged in merges:
                if merged.min_row >= 2:
                    merged.shift(row_shift=20)
                sheet.merge_cells(str(merged))
        workbook.save(target_path)
        workbook.close()
    if recipe != "checker":
        target = copied[case.file_name]
        related = "\n".join(f"- {_role(item.file_seq)}: {copied[item.file_name]}" for item in cases if item.file_name != case.file_name)
        rules = "\n".join(f"{index}. {case.expected[index].rule_desc}" for index in sorted(case.expected))
        prompt = template.read_text(encoding="utf-8").format(target=target, related=related or "无", rules=rules)
        (work / "prompt.txt").write_text(prompt, encoding="utf-8")
    (work / "case.json").write_text(
        json.dumps(
            {
                "recipe": recipe,
                "variant": variant,
                "source_override": str(source.resolve()) if source else None,
                "model": MODEL,
                "reasoning_effort": effort,
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
    return work, None if source else expected


def run_grok(work: Path, effort: str, *, max_turns: int = 16, timeout: float = 900) -> int:
    grok = shutil.which("grok")
    if grok is None:
        raise SystemExit("grok is not on PATH")
    env = os.environ.copy()
    env["GROK_HOME"] = str(LAB_HOME)
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
        MODEL,
        "--reasoning-effort",
        effort,
        "--output-format",
        "streaming-messages-json",
        "--max-turns",
        str(max_turns),
        "--sandbox",
        "off",
        "--permission-mode",
        "bypassPermissions",
    ]
    with (work / "stdout.jsonl").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen(command, cwd=work, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def run_pi(work: Path, effort: str, *, max_turns: int = 16, timeout: float = 900) -> int:
    """Host-only Pi lab. Keep the provider key in the existing grant gateway."""
    from agent.execution.gateway import grant_request, local_gateway
    from product.settings import Settings

    base_url, model, key = Settings().deep_model_config()
    if not key or not base_url or not model:
        raise SystemExit("Configure the Deep model in .env before running Pi")
    node = shutil.which("node")
    if node is None or not (ROOT / "deploy/deep/node_modules/@earendil-works/pi-coding-agent").is_dir():
        raise SystemExit("Pi requires Node >=22.19 and npm ci --ignore-scripts --prefix deploy/deep")
    home = work / "pi-home"
    home.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="ppap-pi-") as temporary:
        with local_gateway(Path(temporary) / "g.sock", upstream=base_url, model=model, provider_key=key, requests=max_turns) as gateway:
            execution_id = uuid4()
            grant = grant_request(str(gateway.socket_path), "issue", execution_id)
            config = {**grant, "home": str(home), "prompt": str(work / "prompt.txt"), "reasoning_effort": effort, "max_turns": max_turns, "max_tokens": grant["max_total_tokens"]}
            request = home / "request.json"
            request.write_text(json.dumps(config), encoding="utf-8")
            request.chmod(0o600)
            env = {name: value for name, value in os.environ.items() if name in {"PATH", "PYTHONPATH", "LANG", "TMPDIR"}}
            env.update(HOME=str(home), PI_CODING_AGENT_DIR=str(home), PI_OFFLINE="1", PI_TELEMETRY="0")
            env["PATH"] = str(ROOT / ".venv/bin") + os.pathsep + env.get("PATH", "")
            with (work / "stdout.jsonl").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
                process = subprocess.Popen([node, str(ROOT / "deploy/deep/pi-runner.mjs"), str(request)], cwd=work, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    code = process.wait(timeout=timeout)
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    usage = grant_request(str(gateway.socket_path), "revoke_and_usage", execution_id)
            (work / "provider-usage.json").write_text(json.dumps(usage), encoding="utf-8")
            if usage.get("uncertain") or usage.get("in_flight"):
                return 1
            return code


def _rule_input(case):
    from audit.contracts import RuleInput

    kind = POC_SEQ_TO_DOC_TYPE.get(case.file_seq)
    return RuleInput(
        rule_id=case.file_name,
        rule_profile="v3",
        type=kind.get_type_name() if kind is not None else None,
        rules=[
            {
                "id": str(index),
                "category": "",
                "priority": "P0",
                "rule_to_check": case.expected[index].rule_desc,
                "type_of_rule": "",
            }
            for index in sorted(case.expected)
        ],
    )


def _checker_context(bundle: str, work: Path, copied: dict[str, str]):
    grouped = build_bundle_map(load_poc_cases())
    cases = grouped[bundle]
    context = build_group_context_for_bundle(cases)

    def bound(item):
        return item.model_copy(
            update={
                "file_url": str((work / copied[item.audit_id]).resolve()),
                "file_metadata": {
                    **(item.file_metadata or {}),
                    "file_version_id": item.audit_id,
                    "original_filename": Path(copied[item.audit_id]).name,
                    "sha256": hashlib.sha256((work / copied[item.audit_id]).read_bytes()).hexdigest(),
                },
            }
        )

    rules = {item.file_name: _rule_input(item) for item in cases}
    override = work / "rules.json"
    if override.exists():
        from audit.contracts import RuleInput

        payload = json.loads(override.read_text())
        rules[payload["target"]] = RuleInput.model_validate(payload["rule"])
    return context.model_copy(
        update={
            "by_doc_type": {key: [bound(item) for item in rows] for key, rows in context.by_doc_type.items()},
            "by_audit_id": {key: bound(item) for key, item in context.by_audit_id.items()},
            "rules_by_id": rules,
        }
    )


class _CheckerRunner:
    """Host model for one development trial; this is not the container runtime."""

    def __init__(self, effort: str, max_turns: int = 128, backend: str = "pi") -> None:
        if backend not in {"pi", "grok"}:
            raise ValueError("Unsupported model runner")
        self.backend = backend
        self.effort = effort
        self.max_turns = max_turns
        self.input_tokens = 0
        self.output_tokens = 0

    def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
        from agent.client import DeepError
        from agent.execution.guest_model import ModelProtocolError, read_checker_draft

        if time.monotonic() >= deadline:
            raise TimeoutError("Model deadline exceeded")
        (work / "prompt.txt").write_text(prompt, encoding="utf-8")
        timeout = max(1.0, min(2400.0, deadline - time.monotonic()))
        run_model = run_pi if self.backend == "pi" else run_grok
        code = run_model(work, reasoning_effort, max_turns=self.max_turns, timeout=timeout)
        terminal = _terminal(work / "stdout.jsonl")
        usage = _usage(work / "stdout.jsonl")
        self.input_tokens += usage[0]
        self.output_tokens += usage[1]
        if terminal is None:
            raise ModelProtocolError(f"model terminal receipt missing (exit {code})")
        # Provider failures cannot be repaired by revising checker code. Preserve any
        # candidate on disk, but stop before the development loop requests confirmation.
        if terminal.get("is_error") is True or code != 0:
            errors = terminal.get("errors")
            for message in errors if isinstance(errors, list) else []:
                if isinstance(message, str) and (match := re.search(r'"http_status"\s*:\s*([45]\d{2})\b|API error \(status ([45]\d{2})\b', message)):
                    raise DeepError(f"deep_model_http_{match.group(1) or match.group(2)}")
        if self.backend == "pi" and (code != 0 or terminal.get("is_error") is not False):
            raise DeepError("deep_model_failed")
        return read_checker_draft(work, schema, terminal)


def _terminal(path: Path) -> dict | None:
    if not path.is_file():
        return None
    found = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            found = obj
    return found


def _usage(path: Path) -> tuple[int, int]:
    usage = _result_field(path, "usage")
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _result_field(path: Path, key: str):
    return (_terminal(path) or {}).get(key)


def _execution_revision() -> str:
    hasher = hashlib.sha256()
    for package in ("agent", "audit", "documents"):
        for path in sorted((ROOT / "packages" / package / "src").rglob("*")):
            if path.is_file() and path.suffix in {".py", ".md"}:
                hasher.update(str(path.relative_to(ROOT)).encode())
                hasher.update(path.read_bytes())
    for name in ("pi-runner.mjs", "package-lock.json"):
        hasher.update((ROOT / "deploy/deep" / name).read_bytes())
    return "lab-" + hasher.hexdigest()


def run_checker(work: Path, bundle: str, file_name: str, effort: str, *, prior_path: Path | None = None, max_turns: int = 128, backend: str = "pi") -> int:
    from agent.checker_program import adapt, execute
    from agent.checker_spec import CheckerArtifact
    from agent.client import DeepError

    revision = _execution_revision()
    prior = CheckerArtifact.model_validate_json(prior_path.read_bytes()) if prior_path is not None else None
    copied = json.loads((work / "case.json").read_text(encoding="utf-8"))["files"]
    context = _checker_context(bundle, work, copied)
    audit_file = context.by_audit_id[file_name]
    rule = context.rules_by_id[file_name]
    raw = Path(audit_file.file_url).read_bytes()
    deadline = time.monotonic() + 40 * 60
    try:
        artifact = adapt(
            audit_file,
            rule,
            context,
            file_bytes=raw,
            file_sha256=hashlib.sha256(raw).hexdigest(),
            role=_role(json.loads((work / "case.json").read_text(encoding="utf-8"))["seq"]),
            executor_revision=revision,
            runner=_CheckerRunner(effort, max_turns, backend),
            work=work,
            deadline=deadline,
            reasoning_effort=effort,
            prior=prior,
        )
        (work / "artifact.json").write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        results = execute(
            artifact,
            audit_file,
            rule,
            context,
            file_bytes=raw,
            file_sha256=hashlib.sha256(raw).hexdigest(),
            role=_role(json.loads((work / "case.json").read_text(encoding="utf-8"))["seq"]),
            executor_revision=revision,
            work=work / "execute",
        )
    except (DeepError, TimeoutError, OSError) as exc:
        detail = str(exc.__cause__) if exc.__cause__ is not None else str(exc)
        (work / "checker-error.txt").write_text(f"{type(exc).__name__}: {exc}\n{detail}", encoding="utf-8")
        return 1
    full = [item.model_dump(mode="json") for item in results]
    (work / "execution.json").write_text(
        json.dumps(
            {
                "executor_revision": revision,
                "input_digest": artifact.input_digest,
                "program_digest": artifact.program_digest,
                "results": full,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    replay = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--replay", str(work)],
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if key in {"PATH", "PYTHONPATH", "LANG", "TMPDIR"}},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if replay.returncode:
        (work / "replay-error.txt").write_text(replay.stderr[-4000:], encoding="utf-8")

    payload = {"checks": [{"id": str(item.clause_id), "status": str(item.status).upper(), "reason": item.suggestions} for item in results if str(item.clause_id or "").strip()]}
    (work / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return replay.returncode


def replay_checker(work: Path) -> int:
    """Reload saved bytes in a fresh process with relocated input files and no model grant."""
    from agent.checker_program import execute
    from agent.checker_spec import CheckerArtifact

    case = json.loads((work / "case.json").read_text(encoding="utf-8"))
    artifact = CheckerArtifact.model_validate_json((work / "artifact.json").read_bytes())
    replay_work = work / "replay"
    replay_work.mkdir()
    if (work / "rules.json").exists():
        shutil.copy2(work / "rules.json", replay_work / "rules.json")
    for relative in case["files"].values():
        destination = replay_work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / relative, destination)
    context = _checker_context(case["bundle"], replay_work, case["files"])
    target = context.by_audit_id[case["target"]]
    raw = Path(target.file_url).read_bytes()
    results = execute(
        artifact,
        target,
        context.rules_by_id[case["target"]],
        context,
        file_bytes=raw,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        role=_role(case["seq"]),
        executor_revision=_execution_revision(),
        work=replay_work / "execute",
    )
    full = [item.model_dump(mode="json") for item in results]
    first = json.loads((work / "execution.json").read_text(encoding="utf-8"))
    matched = first["results"] == full
    (work / "replay.json").write_text(
        json.dumps(
            {
                "replay_matches": matched,
                "program_digest": artifact.program_digest,
                "input_digest": artifact.input_digest,
                "results": full,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if matched else 1


def score(work: Path, expected: dict[str, str] | None) -> dict:
    path = work / "result.json"
    if not path.is_file():
        report = {"match": False, "error": "no result.json", "checks": []}
    elif expected is None:
        report = {"match": None, "scored": False, "checks": [], "reason": "changed input has no independent oracle"}
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload["checks"]
            if not isinstance(items, list) or not expected:
                raise ValueError("empty expectation or invalid checks")
            actual = {str(item["id"]): str(item["status"]).upper() for item in items}
            if len(actual) != len(items):
                raise ValueError("duplicate clause result")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
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
    parser.add_argument("--replay", type=Path, help="Replay a saved checker run without a model")
    parser.add_argument("--bundle", default="Blanc Spoiler-有特殊特性清单")
    parser.add_argument("--seq", default="11")
    parser.add_argument("--file", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--source", type=Path, help="Adapt the selected rules to a new file; do not reuse its old oracle")
    parser.add_argument("--prior", type=Path, help="Fixed checker artifact to diagnose and adapt to the new input")
    parser.add_argument("--rules", type=Path, help="Explicit RuleInput JSON for one checker case; disables the corpus oracle")
    parser.add_argument("--expected", type=Path, help="Independent clause/status JSON used only for host scoring")
    parser.add_argument("--variant", choices=["original", "renamed-sheet", "shifted-table"], default="original")
    parser.add_argument("--effort", default=EFFORT, choices=["xhigh", "high", "medium", "low"])
    parser.add_argument("--runner", choices=["pi", "grok"], default="pi")
    parser.add_argument("--max-turns", type=int, default=128, help="Model turns per checker development stage (1-128)")
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 128:
        raise SystemExit("--max-turns must be between 1 and 128")
    if args.replay is not None:
        raise SystemExit(replay_checker(args.replay.resolve()))
    load_dotenv(ROOT / ".env")
    if args.runner == "grok":
        ensure_home()
    from product.settings import Settings

    active_model = Settings().deep_model_policy()[1] if args.runner == "pi" else MODEL
    selected = _selected(args.bundle, args.seq, args.file, args.all)
    if (args.rules or args.expected) and (args.recipe != "checker" or len(selected) != 1):
        raise SystemExit("--rules/--expected require one checker case")
    if (args.prior or args.source or args.variant != "original") and (args.recipe != "checker" or len(selected) != 1):
        raise SystemExit("--prior/--source/--variant require one checker case")
    summary = []
    for index, case in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] {case.bundle_id} {case.file_name}", flush=True)
        try:
            work, expected = prepare(args.recipe, case.bundle_id, case.file_name, args.effort, variant=args.variant, source=args.source)
            metadata = json.loads((work / "case.json").read_text()) if (work / "case.json").exists() else {}
            metadata.update(runner=args.runner, model=active_model)
            (work / "case.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
            if args.rules:
                from audit.contracts import RuleInput

                rule = RuleInput.model_validate_json(args.rules.read_bytes())
                (work / "rules.json").write_text(json.dumps({"target": case.file_name, "rule": rule.model_dump(mode="json")}, ensure_ascii=False))
                expected = None
            if args.expected:
                expected = json.loads(args.expected.read_text())
            if args.recipe == "checker":
                code = run_checker(work, case.bundle_id, case.file_name, args.effort, prior_path=args.prior, max_turns=args.max_turns, backend=args.runner)
            else:
                code = (run_pi if args.runner == "pi" else run_grok)(work, args.effort, max_turns=args.max_turns)
            report = score(work, expected)
            row = {
                "bundle": case.bundle_id,
                "file": case.file_name,
                "work": str(work),
                "match": None if report["match"] is None else report["match"] and code == 0,
                "executed": code == 0,
                "exit": code,
            }
            if code != 0:
                failure = work / "checker-error.txt"
                row["error"] = failure.read_text(encoding="utf-8").splitlines()[0] if failure.is_file() else report.get("error") or f"exit {code}"
        except subprocess.TimeoutExpired:
            row = {"bundle": case.bundle_id, "file": case.file_name, "match": False, "error": "timeout"}
            print(json.dumps(row, ensure_ascii=False), flush=True)
        except (OSError, SystemExit) as exc:
            row = {"bundle": case.bundle_id, "file": case.file_name, "match": False, "error": str(exc)}
            print(json.dumps(row, ensure_ascii=False), flush=True)
        summary.append(row)
    matched = sum(1 for row in summary if row["match"])
    succeeded = all(row.get("executed") and row["match"] is not False for row in summary)
    totals = {
        "recipe": args.recipe,
        "effort": args.effort,
        "runner": args.runner,
        "model": active_model,
        "max_turns": args.max_turns,
        "cases": len(summary),
        "matched": matched,
        "executed": sum(bool(row.get("executed")) for row in summary),
        "results": summary,
    }
    destination = OUTPUT / args.recipe / args.effort / "summary.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(summary), "matched": matched, "summary": str(destination)}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if succeeded else 1)


if __name__ == "__main__":
    sys.exit(main())
