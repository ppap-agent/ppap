"""Local research: real grok-build tools over original files, no product task.

The existing PPAP gateway owns model grants and usage. This local harness does
not impersonate a container or bypass the production GrokRunner guards.
"""

from __future__ import annotations

import argparse
import base64
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

from full_document_contract import FullDocumentResult, validate_result
from hole_experiment import MODEL, REASONING_EFFORT

HERE = Path(__file__).resolve().parent
RUNNER_SOURCE = Path(__file__).read_bytes()
ROOT = HERE.parents[3]
SOURCES = {
    "jifeng": "1-全功能检验报告_继峰.xlsx",
    "yada": "1-全功能检验报告_上海亚大.xlsx",
    "jifeng_error": "1-全功能检验报告_继峰 - 错误.xlsx",
}
TOOLS = "run_terminal_cmd,read_file,list_dir,grep,write,search_replace,get_task_output,wait_tasks,kill_task"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def append(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def scrub(value, secrets, blobs):
    """Keep observable actions, not reasoning blocks; images become local blobs."""
    if isinstance(value, dict):
        if isinstance(value.get("type"), str) and value["type"] in {"thinking", "redacted_thinking", "reasoning", "reasoning_text", "reasoning_summary_text"}:
            return {"type": "reasoning_omitted"}
        if value.get("type") == "base64" and str(value.get("media_type", "")).startswith("image/"):
            return scrub("data:" + value["media_type"] + ";base64," + value["data"], secrets, blobs)
        return {key: scrub(item, secrets, blobs) for key, item in value.items() if key not in {"encrypted_content", "signature", "thinking", "reasoning_content"}}
    if isinstance(value, list):
        return [scrub(item, secrets, blobs) for item in value]
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            mime, encoded = value.split(";base64,", 1)
            raw = base64.b64decode(encoded)
            digest = hashlib.sha256(raw).hexdigest()
            blobs.mkdir(exist_ok=True)
            target = blobs / digest
            target.write_bytes(raw)
            return {"image_blob": str(target.name), "sha256": digest, "mime": mime[5:], "bytes": len(raw)}
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


def cli_config(base_url, token):
    config = f"""[models]
default = {json.dumps(MODEL)}
[model.{json.dumps(MODEL)}]
model = {json.dumps(MODEL)}
name = {json.dumps(MODEL)}
base_url = {json.dumps(base_url)}
api_key = {json.dumps(token)}
api_backend = "responses"
max_retries = 0
max_request_bytes = 8388608
max_completion_tokens = 16000
vision = true
reasoning_effort = "medium"
supports_reasoning_effort = true
[cli]
auto_update = false
use_leader = false
session_registry = false
[memory]
enabled = false
[features]
telemetry = false
title_refresh = false
turn_summary = false
[telemetry]
trace_upload = false
[harness]
disable_workspace_teleport = true
[cursor_worker]
auto_start = false
"""
    for provider, keys in {"claude": ["agents", "hooks", "mcps", "rules", "skills"], "codex": ["hooks", "skills"], "cursor": ["agents", "hooks", "mcps", "rules", "skills"]}.items():
        config += f"[compat.{provider}]\n" + "".join(f"{key} = false\n" for key in keys)
    return config


def run_case(case_id, args, settings):
    sys.path.insert(0, str(ROOT / "packages/ppap_deep/src"))
    from ppap_deep.isolation.model_gateway import Gateway, Grants

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

    grants = RecordedGrants(MODEL, ttl=args.seconds, requests=args.max_requests, input_tokens=1_500_000, output_tokens=100_000)
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
            document = inputs / "document.xlsx"
            rules = inputs / "rules.docx"
            shutil.copy2(args.assets_root / "assets/v2.0plus/fallback" / SOURCES[case_id], document)
            shutil.copy2(args.assets_root / "assets/V2.0测试/规则V2.0/01 全功能检验报告.docx", rules)
            for path in (document, rules):
                path.chmod(0o400)
            shutil.copytree(inputs, output / "inputs")
            for filename in ("inspect_original.py", "full_document_contract.py"):
                shutil.copy2(HERE / filename, helper / filename)
            shutil.copy2(ROOT / "packages/ppap_deep/src/ppap_deep/semantic/document_tools.py", helper / "document_tools.py")
            shutil.copytree(helper, output / "tools")
            dump(work / "output-schema.json", FullDocumentResult.model_json_schema())
            prompt = (HERE / "full_document_prompt.md").read_text().replace("{{PYTHON}}", sys.executable).replace("{{TOOLS}}", str(helper)).replace("{{INPUTS}}", str(inputs))
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
                    "input_token_budget": 1_500_000,
                    "output_token_budget": 100_000,
                    "seconds_limit": args.seconds,
                    "schema_sha256": sha(work / "output-schema.json"),
                    "runner_sha256": hashlib.sha256(RUNNER_SOURCE).hexdigest(),
                    "contract_sha256": sha(helper / "full_document_contract.py"),
                    "reader_sha256": sha(helper / "inspect_original.py"),
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
            shutil.copytree(work, output / "work", ignore=shutil.ignore_patterns("grok-sessions-*"))
            artifact = work / "execution.json"
            if artifact.is_file():
                raw = json.loads(artifact.read_text())
                summary["validation"] = validate_result(raw, document, rules)
                summary["rule_statuses"] = {item["rule_id"]: item["status"] for item in raw.get("checks", [])}
            else:
                summary["validation"] = {"errors": ["execution_artifact_missing"], "training_eligible": False}
            try:
                receipt = json.loads((terminal or {}).get("result", ""))
            except (ValueError, TypeError):
                receipt = None
            summary["run_completed"] = (
                process.returncode == 0
                and not (terminal or {}).get("is_error", True)
                and receipt == {"completed": True, "artifact": "execution.json"}
                and not summary["validation"]["errors"]
                and summary["source_unchanged"]
            )
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, default=ROOT)
    parser.add_argument("--settings-root", type=Path, required=True)
    parser.add_argument("--grok", default=shutil.which("grok"))
    parser.add_argument("--max-requests", type=int, default=32)
    parser.add_argument("--seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.output.exists() or not args.grok or not 1 <= args.max_requests <= 32 or not 1 <= args.seconds <= 1800:
        parser.error("new output directory, grok, and bounded requests/time are required")
    args.output = args.output.resolve()
    args.output.mkdir(mode=0o700, parents=True)
    from ppap_core.settings import Settings

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
