"""Local research: real grok-build tools over original files, no product task.

The existing PPAP gateway owns model grants and usage. This local harness does
not impersonate a container or bypass the production GrokRunner guards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from agent.execution.secure_files import reject_constant, unique_object
from audit import document_tools
from audit.engine.checkers import dimension_measurement_policy
from bound_document import RULES_SHA256, DocumentBinding, compact_observation, evaluate
from grok_support import append, cli_config, dump, scrub
from hole_experiment import MODEL, REASONING_EFFORT
from inspect_original import inspect_original
from inspect_pdf import inspect_pdf

HERE = Path(__file__).resolve().parent
RUNNER_SOURCE = Path(__file__).read_bytes()
ROOT = HERE.parents[3]
SOURCES = {
    "jifeng": "1-全功能检验报告_继峰.xlsx",
    "yada": "1-全功能检验报告_上海亚大.xlsx",
    "jifeng_error": "1-全功能检验报告_继峰 - 错误.xlsx",
    "giant": "1-全功能检验报告_巨一.xlsx",
    "jifeng_obscured": "1-全功能检验报告_继峰 - 线框被遮挡.xlsx",
}
SOURCES.update({key + "_pdf": value.removesuffix(".xlsx") + ".pdf" for key, value in list(SOURCES.items())})
TOOLS = "read_file"
INPUT_TOKEN_BUDGET = 300_000
OUTPUT_TOKEN_BUDGET = 40_000
REPLAY_SOURCES = {name: (HERE / name).read_bytes() for name in ("inspect_original.py", "inspect_pdf.py", "bound_document.py", "grok_support.py", "hole_experiment.py")}
REPLAY_SOURCES.update({"document_tools.py": Path(document_tools.__file__).read_bytes(), "dimension_measurement_policy.py": Path(dimension_measurement_policy.__file__).read_bytes()})


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repair_context(previous_root: Path, case_id: str, document: Path, rules: Path) -> tuple[dict, str]:
    """One explicit repair of a settled structural rejection, never an unknown call."""
    previous_path = previous_root / case_id / "summary.json"
    previous_bytes = previous_path.read_bytes()
    previous = json.loads(previous_bytes, object_pairs_hook=unique_object, parse_constant=reject_constant)
    usage, terminal = previous.get("usage", {}), previous.get("terminal") or {}
    if (
        previous.get("case_id") != case_id
        or previous.get("repair_of")
        or previous.get("run_completed")
        or previous.get("returncode") != 0
        or terminal.get("is_error", True)
        or usage.get("uncertain", True)
        or usage.get("in_flight", True)
        or not previous.get("source_unchanged")
        or previous.get("validation", {}).get("errors") != ["invalid_source_binding"]
        or previous.get("document_sha256") != sha(document)
        or previous.get("rules_sha256") != sha(rules)
    ):
        raise ValueError("Repair requires a settled first-attempt structural rejection of these exact originals")
    candidate = terminal.get("structured_output")
    DocumentBinding.model_validate(candidate)
    reference = {"summary": str(previous_path.resolve()), "sha256": hashlib.sha256(previous_bytes).hexdigest(), "attempt": 1}
    feedback = {"validation": previous["validation"], "candidate": candidate}
    prompt = (
        "\n\nThis is the single repair of a completed but structurally rejected binding. "
        "Use the original sources above and this exact feedback. Correct invalid mapping or header claims; preserve every genuine body record. "
        "Continuation pages may have no headers: use header_rows=[] there. Never copy header row numbers from another page. "
        "A validation failure is not a business verdict. Return the complete corrected binding, not a patch.\n"
    )
    return reference, prompt + json.dumps(feedback, ensure_ascii=False)


def run_case(case_id, args, settings):
    from agent.execution.gateway import Gateway, Grants

    output = args.output / case_id
    output.mkdir(mode=0o700)
    (output / "runner.py").write_bytes(RUNNER_SOURCE)
    blobs = output / "image-blobs"
    secrets = [settings.llm_api_key]

    class RecordedGrants(Grants):
        def authorize(self, token, body, request_bytes):
            # One declared model/effort policy also covers auxiliary core calls.
            body["reasoning"] = {"effort": REASONING_EFFORT}
            sanitized = scrub(body, secrets, blobs)
            lease = super().authorize(token, body, request_bytes)
            sanitized["store"] = body["store"]
            sanitized["max_output_tokens"] = body["max_output_tokens"]
            append(output / "model-inputs.jsonl", {"request_index": len(lease.request_hashes), "body": sanitized})
            print(json.dumps({"case": case_id, "event": "model_request", "request": len(lease.request_hashes)}), flush=True)
            return lease

    grants = RecordedGrants(MODEL, ttl=args.seconds, requests=args.max_requests, input_tokens=INPUT_TOKEN_BUDGET, output_tokens=OUTPUT_TOKEN_BUDGET)
    gateway = Gateway(("127.0.0.1", 0), grants, settings.llm_base_url, settings.llm_api_key)
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    execution_id = uuid4()
    token = grants.issue(execution_id)
    secrets.append(token)
    started = time.monotonic()
    summary = {
        "case_id": case_id,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "execution_id": str(execution_id),
        "run_completed": False,
        "label": {"eligible": False, "reason": "no_independent_binding_gold"},
    }
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="ppap-hole-") as temporary:
            scratch = Path(temporary).resolve()
            work, inputs, helper, grok_config = [scratch / name for name in ("work", "inputs", "tools", "grok-home")]
            for directory in (work, inputs, helper, grok_config):
                directory.mkdir(mode=0o700)
            document = inputs / ("document" + Path(SOURCES[case_id]).suffix)
            rules = inputs / "rules.docx"
            shutil.copy2(args.assets_root / "assets/v2.0plus/fallback" / SOURCES[case_id], document)
            shutil.copy2(args.assets_root / "assets/V2.0测试/规则V2.0/01 全功能检验报告.docx", rules)
            for path in (document, rules):
                path.chmod(0o400)
            shutil.copytree(inputs, output / "inputs")
            if sha(rules) != RULES_SHA256:
                raise ValueError("Doc01 policy requires its frozen original rules")
            for filename, content in REPLAY_SOURCES.items():
                (helper / filename).write_bytes(content)
            shutil.copytree(helper, output / "tools")
            if document.suffix == ".pdf":
                source_snapshot = inspect_pdf(document, work / "document_read")
                if source_snapshot["coverage"]["unresolved_pages"]:
                    raise ValueError("PDF page coverage is incomplete")
            else:
                source_snapshot = inspect_original(document, work / "document_read")
            rules_snapshot = inspect_original(rules, work / "rules_read")
            dump(work / "output-schema.json", DocumentBinding.model_json_schema())
            prompt = (HERE / "full_document_prompt.md").read_text().replace("{{INPUTS}}", str(inputs)).replace("/document.xlsx", "/" + document.name)
            prompt += "\n\nORIGINAL RULES (native text):\n" + compact_observation(rules_snapshot)
            prompt += "\n\nORIGINAL WORKBOOK (complete native text):\n" + compact_observation(source_snapshot)
            prompt += "\n\nEXTRACTED WORKBOOK MEDIA:\n" + json.dumps(source_snapshot["media"], ensure_ascii=False)
            if document.suffix == ".pdf":
                prompt += (
                    "\nThis input is the original PDF. Tables page-N preserve each page's own grid, not an XLSX twin. "
                    "Cell source page/bbox and page renders are retained in the snapshot. Use these page table IDs as sheet names in the binding. "
                    "page-N-outer-text contains text outside that page's grid. Repeated page headers are not new records. Unknown visual cells cannot be replaced by guesses.\n"
                )
                prompt += "PDF PAGES:\n" + json.dumps(source_snapshot["pages"], ensure_ascii=False)
            if args.repair_from is not None:
                summary["repair_of"], feedback = repair_context(args.repair_from, case_id, document, rules)
                prompt += feedback
            (work / "prompt.txt").write_text(prompt)
            (output / "prompt.txt").write_text(prompt)
            shutil.copy2(work / "output-schema.json", output / "output-schema.json")
            summary.update(
                document_sha256=sha(document),
                rules_sha256=sha(rules),
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                grok_version=subprocess.check_output([args.grok, "--version"], text=True).strip(),
            )
            dump(
                output / "run.json",
                summary
                | {
                    "max_requests": args.max_requests,
                    "input_token_budget": INPUT_TOKEN_BUDGET,
                    "output_token_budget": OUTPUT_TOKEN_BUDGET,
                    "seconds_limit": args.seconds,
                    "schema_sha256": sha(work / "output-schema.json"),
                    "runner_sha256": hashlib.sha256(RUNNER_SOURCE).hexdigest(),
                    "contract_sha256": sha(helper / "bound_document.py"),
                    "measurement_policy_sha256": sha(helper / "dimension_measurement_policy.py"),
                    "reader_sha256": sha(helper / "inspect_original.py"),
                    "pdf_reader_sha256": sha(helper / "inspect_pdf.py"),
                },
            )
            (grok_config / "config.toml").write_text(cli_config(f"http://127.0.0.1:{gateway.server_address[1]}/v1", token))
            (grok_config / "config.toml").chmod(0o600)
            read_paths = [
                inputs,
                helper,
                Path(sys.prefix),
                Path(sys.base_prefix),
                Path("/opt/homebrew"),
                Path("/Applications/LibreOffice.app"),
                Path("/Users/bef0rewind/.cache/codex-runtimes/codex-primary-runtime/dependencies"),
            ]
            read_paths = [str(path) for path in read_paths if path.exists()]
            # The installed core rejects this host's runtime-socket symlink
            # before applying its profile. Use an outer native Seatbelt policy;
            # do not continue with an unenforced inner sandbox.
            if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
                raise RuntimeError("This research launcher requires macOS Seatbelt")
            system_reads = ["/System", "/usr", "/bin", "/sbin", "/Library", "/private/etc", "/dev", str(Path(args.grok).resolve().parent)]
            policy = "(version 1)\n(deny default)\n(allow process* signal sysctl-read mach-lookup ipc-posix-shm iokit-open)\n"
            policy += "(allow file-read-metadata)\n"
            policy += '(allow file-read* (literal "/") (literal "/private") (literal "/private/var"))\n'
            policy += '(allow file-write* (literal "/dev/null"))\n'
            for path in system_reads + read_paths:
                policy += "(allow file-read* (subpath " + json.dumps(str(Path(path).resolve())) + "))\n"
            policy += "(allow file-read* file-write* (subpath " + json.dumps(str(work)) + ") (subpath " + json.dumps(str(grok_config)) + "))\n"
            policy += '(allow network-outbound (remote ip "localhost:' + str(gateway.server_address[1]) + '"))\n'
            seatbelt = scratch / "research.sb"
            seatbelt.write_text(policy)
            shutil.copy2(seatbelt, output / "research.sb")
            canary = scratch / "denied-canary.txt"
            canary.write_text("research-only canary")
            probe = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(seatbelt), "/bin/cat", str(canary)], capture_output=True)
            allowed = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(seatbelt), "/bin/cat", str(document)], capture_output=True)
            summary["local_boundary_probe"] = {"denied_returncode": probe.returncode, "allowed_returncode": allowed.returncode, "diagnostic": allowed.stderr.decode(errors="replace")[:1000]}
            if probe.returncode == 0 or allowed.returncode != 0:
                raise RuntimeError("Local file-access boundary probe failed")
            summary["local_file_boundary"] = {"mechanism": "macOS Seatbelt", "allowed_original_read": True, "outside_canary_denied": True}
            shutil.copy2(seatbelt, output / "research.sb")
            command = [
                args.grok,
                "--agent",
                "grok-build",
                "--cwd",
                str(work),
                "--prompt-file",
                str(work / "prompt.txt"),
                "--model",
                MODEL,
                "--reasoning-effort",
                REASONING_EFFORT,
                "--output-format",
                "streaming-messages-json",
                "--json-schema",
                json.dumps(DocumentBinding.model_json_schema()),
                "--max-turns",
                str(args.max_requests),
                "--sandbox",
                "off",
                "--permission-mode",
                "bypassPermissions",
                "--tools",
                TOOLS,
                "--disable-web-search",
                "--no-subagents",
                "--no-plan",
                "--deny",
                "MCPTool",
            ]
            command = ["/usr/bin/sandbox-exec", "-f", str(seatbelt), *command]
            environment = {
                "PATH": os.environ["PATH"],
                "GROK_HOME": str(grok_config),
                "GROK_MEMORY": "0",
                "GROK_TELEMETRY_TRACE_UPLOAD": "false",
                "GROK_DISABLE_AUTOUPDATER": "1",
                "LANG": "en_US.UTF-8",
                "TMPDIR": str(work),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            process = subprocess.Popen(command, cwd=work, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            messages = queue.Queue()

            def pump(stream, channel):
                for line in stream:
                    messages.put((channel, line))
                messages.put((channel, None))

            for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                threading.Thread(target=pump, args=(stream, channel), daemon=True).start()
            closed, tools_started, tools_finished = set(), 0, 0
            terminal = None
            while len(closed) < 2:
                if time.monotonic() - started > args.seconds:
                    summary["timeout"] = True
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                try:
                    channel, line = messages.get(timeout=1)
                except queue.Empty:
                    continue
                if line is None:
                    closed.add(channel)
                    continue
                if channel == "stderr":
                    append(output / "diagnostics.jsonl", {"message": scrub(line.rstrip(), secrets, blobs)})
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    append(output / "diagnostics.jsonl", {"message": "non_json_stdout", "text": scrub(line[:500], secrets, blobs)})
                    continue
                append(output / "events.jsonl", scrub(event, secrets, blobs))
                for item in event.get("message", {}).get("content", []) if isinstance(event.get("message"), dict) else []:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tools_started += 1
                        print(json.dumps({"case": case_id, "event": "tool", "name": item.get("name"), "count": tools_started}), flush=True)
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tools_finished += 1
                if event.get("type") == "result":
                    terminal = scrub(event, secrets, blobs)
            if process.poll() is None:
                process.wait(timeout=10)
            summary.update(returncode=process.returncode, tool_calls=tools_started, tool_results=tools_finished, terminal=terminal)
            summary["source_unchanged"] = sha(document) == summary["document_sha256"] and sha(rules) == summary["rules_sha256"]
            try:
                candidate = (terminal or {}).get("structured_output")
                dump(work / "binding-candidate.json", candidate)
                binding = DocumentBinding.model_validate(candidate)
                raw = evaluate(binding, source_snapshot, rules_sha256=summary["rules_sha256"])
                dump(work / "execution.json", raw)
                summary["validation"] = {"errors": [], "training_eligible": False}
                summary["rule_statuses"] = {item["rule_id"]: item["status"] for item in raw["checks"]}
            except (ValueError, TypeError) as error:
                summary["validation"] = {"errors": ["invalid_source_binding"], "detail": scrub(str(error)[:4000], secrets, blobs), "training_eligible": False}
            shutil.copytree(work, output / "work", ignore=shutil.ignore_patterns("grok-sessions-*"))
            summary["run_completed"] = process.returncode == 0 and not (terminal or {}).get("is_error", True) and not summary["validation"]["errors"] and summary["source_unchanged"]
            summary["sandbox_events"] = []
            for path in grok_config.rglob("sandbox-events.jsonl"):
                for line in path.read_text().splitlines():
                    summary["sandbox_events"].append(scrub(json.loads(line), secrets, blobs))
            # No raw core home/session/reasoning transcript is retained.
    except Exception as error:
        summary["failure_type"] = type(error).__name__
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    finally:
        summary["usage"] = grants.usage(execution_id)
        summary["run_completed"] = summary["run_completed"] and not summary["usage"]["uncertain"]
        grants.revoke(execution_id)
        gateway.shutdown()
        gateway.server_close()
        gateway_thread.join()
        summary["seconds"] = round(time.monotonic() - started, 3)
        dump(output / "summary.json", summary)
    print(
        json.dumps(
            {"case": case_id, "event": "finished", "seconds": summary["seconds"], "usage": summary["usage"], "validation": summary.get("validation"), "failure_type": summary.get("failure_type")},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--case", action="append", choices=list(SOURCES))
    parser.add_argument("--repair-from", type=Path, help="Previous run root; one explicit repair of settled structural rejections")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, default=ROOT)
    parser.add_argument("--settings-root", type=Path, required=True)
    parser.add_argument("--grok", default=shutil.which("grok"))
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.repair_from is not None and not args.case:
        parser.error("Repair requires explicit --case selections")
    if args.output.exists() or not args.grok or not 1 <= args.max_requests <= 32 or not 1 <= args.seconds <= 1800:
        parser.error("new output directory, grok, and bounded requests/time are required")
    args.output = args.output.resolve()
    args.output.mkdir(mode=0o700, parents=True)
    from product.settings import Settings

    settings = Settings(_env_file=args.settings_root / ".env")
    summaries = []
    for case_id in args.case or list(SOURCES):
        summaries.append(run_case(case_id, args, settings))
        dump(args.output / "summary.json", {"runs": summaries, "training_eligible": 0, "product_acceptance": False})
        if summaries[-1]["usage"].get("uncertain"):
            break
    return 0 if len(summaries) == len(args.case or SOURCES) and all(run["run_completed"] for run in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
