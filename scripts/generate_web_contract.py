#!/usr/bin/env python3
"""Generate deterministic Web TypeScript schemas from FastAPI OpenAPI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ppap_api import app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps" / "web" / "src" / "generated" / "openapi.ts"
SKIP_SCHEMAS = {"HTTPValidationError", "ValidationError"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

REQUIRED_ENDPOINTS: dict[tuple[str, str], bool] = {
    ("GET", "/public-config"): False,
    ("POST", "/login/feishu/host"): True,
    ("GET", "/me"): False,
    ("POST", "/files"): True,
    ("GET", "/files/{file_id}/versions/{version_id}"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/preview"): False,
    ("GET", "/integrations/feishu/files"): False,
    ("POST", "/integrations/feishu/files/import"): True,
    ("GET", "/integrations/feishu/usage/quota"): False,
    ("POST", "/integrations/feishu/rounds/{round_id}/writeback"): False,
    ("GET", "/rules"): False,
    ("POST", "/drafts"): True,
    ("GET", "/drafts"): False,
    ("POST", "/file-analyses"): True,
    ("GET", "/file-analyses/{analysis_id}"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/analysis"): False,
    ("GET", "/drafts/{draft_id}"): False,
    ("PUT", "/drafts/{draft_id}"): True,
    ("POST", "/drafts/{draft_id}/accept"): True,
    ("GET", "/rounds"): False,
    ("GET", "/usage"): False,
    ("GET", "/rounds/{round_id}"): False,
    ("GET", "/rounds/{round_id}/files/{file_id}/versions/{version_id}/preview"): False,
    ("POST", "/rounds/{round_id}/resubmit"): True,
    ("PUT", "/rounds/{round_id}/reviews"): True,
    ("GET", "/rule-types"): False,
    ("POST", "/rules/parse"): True,
    ("GET", "/rules/parse/{parse_id}"): False,
    ("POST", "/rules"): True,
    ("GET", "/rules/{rule_version_id}"): False,
    ("GET", "/integrations/feishu/rounds/{round_id}/report"): False,
    ("POST", "/integrations/feishu/rounds/{round_id}/report"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/office"): False,
}


def ref_name(ref: str) -> str:
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        raise ValueError(f"unsupported OpenAPI ref: {ref}")
    return ref[len(prefix):]


def literal(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def ts_type(schema: dict[str, Any], level: int = 0) -> str:
    if not schema:
        return "unknown"
    if "$ref" in schema:
        return f'components["schemas"][{json.dumps(ref_name(schema["$ref"]))}]'
    if "const" in schema:
        return literal(schema["const"])
    if "enum" in schema:
        values = sorted((literal(value) for value in schema["enum"]))
        return " | ".join(values) if values else "never"
    for key, separator in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        if key in schema:
            values = sorted({ts_type(item, level) for item in schema[key]})
            return separator.join(values) if values else "unknown"

    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(sorted(ts_type({**schema, "type": item}, level) for item in kind))
    if kind == "null":
        return "null"
    if kind == "string":
        return "string"
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "array":
        item = ts_type(schema.get("items") or {}, level)
        return f"Array<{item}>"
    if kind == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        indent = "  " * level
        child_indent = "  " * (level + 1)
        rows = []
        for name in sorted(properties):
            marker = "" if name in required else "?"
            rows.append(
                f"{child_indent}{json.dumps(name, ensure_ascii=False)}{marker}: "
                f"{ts_type(properties[name], level + 1)};"
            )
        if rows:
            body = "{\n" + "\n".join(rows) + f"\n{indent}}}"
            additional = schema.get("additionalProperties")
            if isinstance(additional, dict):
                return body + f" & Record<string, {ts_type(additional, level)}>"
            return body
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {ts_type(additional, level)}>"
        return "Record<string, unknown>"
    return "unknown"


def response_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    for status in sorted(operation.get("responses") or {}):
        if not str(status).startswith("2"):
            continue
        response = operation["responses"][status]
        content = response.get("content") or {}
        media = content.get("application/json")
        if media and media.get("schema"):
            return media["schema"]
    return None


def validate_required_operations(spec: dict[str, Any]) -> None:
    failures: list[str] = []
    paths = spec.get("paths") or {}
    for (method, path), needs_body in REQUIRED_ENDPOINTS.items():
        operation = (paths.get(path) or {}).get(method.lower())
        label = f"{method} {path}"
        if not operation:
            failures.append(f"missing OpenAPI operation: {label}")
            continue
        response = response_schema(operation)
        if not response or response == {"type": "object"}:
            failures.append(f"missing explicit 2xx JSON response schema: {label}")
        if needs_body:
            request = (
                ((operation.get("requestBody") or {}).get("content") or {})
                .get("application/json", {})
                .get("schema")
            )
            if not request or request == {"type": "object"}:
                failures.append(f"missing explicit JSON request schema: {label}")
    if failures:
        raise SystemExit("\n".join(failures))


def generate() -> str:
    spec = app.openapi()
    validate_required_operations(spec)
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    names = sorted(name for name in schemas if name not in SKIP_SCHEMAS)
    rows = [
        "/* eslint-disable */",
        "// GENERATED FILE. Source authority: FastAPI app.openapi().",
        "// Rebuild: uv run --no-sync python scripts/generate_web_contract.py",
        "",
        "export interface components {",
        '  schemas: {',
    ]
    for name in names:
        rendered = ts_type(schemas[name], 2)
        rows.append(f"    {json.dumps(name)}: {rendered};")
    rows.extend(
        [
            "  }",
            "}",
            "",
            'export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name]',
            "",
        ]
    )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = generate()
    if args.check:
        current = OUTPUT.read_text() if OUTPUT.is_file() else ""
        if current != generated:
            raise SystemExit(
                "generated Web OpenAPI types are stale; run "
                "uv run --no-sync python scripts/generate_web_contract.py"
            )
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated)


if __name__ == "__main__":
    main()
