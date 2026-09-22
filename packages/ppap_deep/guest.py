"""gVisor guest auditor only. Production process runtime uses auditor.py, not this heuristic."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

WORK = Path("/work")
GROK = Path("/grant/bin/grok")


def evaluate(spec: dict, files: dict[str, bytes]) -> dict:
    findings: list[dict] = []
    checked: list[str] = []
    unchecked: list[str] = []
    for item in spec["files"]:
        blob = files.get(item["file_version_id"], b"")
        if item.get("role") == "target":
            ready = bool(blob)
            kind = str((item.get("rule_body") or {}).get("kind") or "")
            if kind == "presence":
                status = "PASS" if ready else "NG"
                suggestions = "" if ready else "未完成的暂存不能被审核受理。"
                label = item.get("rule_title") or "资料就绪"
            elif not ready:
                status = "NG"
                suggestions = "未完成的暂存不能被审核受理。"
                label = item.get("rule_title") or "深度审核"
            else:
                status = "PASS"
                suggestions = ""
                label = item.get("rule_title") or "深度审核已完成"
            findings.append(
                {
                    "item_key": f"target:{item['file_version_id']}",
                    "classification": "资料",
                    "item": label,
                    "priority": "P0",
                    "status": status,
                    "suggestions": suggestions,
                    "locator": {
                        "kind": "file_version",
                        "file_version_id": item["file_version_id"],
                        "filename": item["filename"],
                    },
                }
            )
            checked.append(item["filename"])
        else:
            unchecked.append(f"{item['filename']}（参考，未作为待审完整检查）")
    return {
        "protocol_revision": spec["protocol_revision"],
        "round_id": spec["round_id"],
        "files": spec["files"],
        "findings": findings,
        "coverage": {"checked": checked, "unchecked": unchecked, "locator_gap": False},
        "note": f"deep_audit kernel={os.uname().release}",
    }


def _model_grant() -> str:
    if not GROK.is_file():
        return "model=disabled"
    try:
        proc = subprocess.run(
            [str(GROK), "--single", "Reply with exactly: DEEP_GUEST_OK", "--output-format", "plain", "--verbatim"],
            capture_output=True,
            timeout=25,
            check=False,
            cwd="/work",
            env={
                "HOME": "/work/home",
                "PATH": "/grant/bin:/usr/local/bin:/usr/bin:/bin",
                "TERM": "dumb",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return "model=unavailable"
    text = (proc.stdout or b"").decode("utf-8", "replace")
    if proc.returncode == 0 and "DEEP_GUEST_OK" in text:
        return "model=ok"
    return "model=unavailable"


def main() -> None:
    spec = json.loads((WORK / "spec.json").read_text(encoding="utf-8"))
    files: dict[str, bytes] = {}
    for item in spec["files"]:
        path = WORK / "files" / item["file_version_id"]
        files[item["file_version_id"]] = path.read_bytes() if path.is_file() else b""
    result = evaluate(spec, files)
    result["note"] = f"{result.get('note') or 'deep_audit'} {_model_grant()}"
    (WORK / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
