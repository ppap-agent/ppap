"""Operator command for a tenant's own model key (tenant BYOK). Customers have no API for it.

    python -m product.tenant_models show --tenant-id <uuid>
    python -m product.tenant_models set --tenant-id <uuid> --base-url <https url> --text-model <model> [--vision-model <model>] [--coding-model <model>] --operator <name> < key-file
    python -m product.tenant_models delete --tenant-id <uuid>

The key is read from standard input and never printed. A key is stored only after one
text, one structured and one vision call succeed with it; otherwise nothing changes.
"""

from __future__ import annotations

import argparse
import io
import json
import sys

from documents.ai import AIError, run_json
from documents.model_access import ModelUnavailable, model_access_scope
from pydantic import SecretStr

from product.store.model_credentials import KeyCandidate, ModelCredentialError, TenantModelStore


def _probe_image() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (240, 80), "white")
    ImageDraw.Draw(image).text((10, 30), "P-204", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def check_capabilities(candidate: KeyCandidate) -> list[str]:
    """The uses this key could not serve. An empty list means it can be stored."""
    missing = []
    with model_access_scope(lambda purpose: candidate.accesses[purpose]):
        for name, call in (
            ("text", lambda: run_json("tenant_key_check", prompt='只返回 JSON：{"ok": true}', max_tokens=50, timeout=60)),
            (
                "structured",
                lambda: run_json(
                    "tenant_key_check",
                    prompt="北京是哪个国家的首都？",
                    schema={"type": "object", "properties": {"country": {"type": "string"}}, "required": ["country"], "additionalProperties": False},
                    max_tokens=100,
                    timeout=60,
                ),
            ),
            (
                "vision",
                lambda: run_json(
                    "tenant_key_check",
                    purpose="vision",
                    prompt="读取图中的编号。",
                    images=[_probe_image()],
                    schema={"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"], "additionalProperties": False},
                    max_tokens=100,
                    timeout=60,
                ),
            ),
        ):
            try:
                if not call().output:
                    missing.append(name)
            except (AIError, ModelUnavailable):
                missing.append(name)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["show", "set", "delete"])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--text-model")
    parser.add_argument("--vision-model")
    parser.add_argument("--coding-model")
    parser.add_argument("--operator", default="")
    args = parser.parse_args(argv)
    from product.composition import build_product_store
    from product.settings import load_settings

    models = TenantModelStore(build_product_store(load_settings(), role="api"))
    try:
        if args.action == "show":
            result = models.public(args.tenant_id)
        elif args.action == "delete":
            result = models.delete(args.tenant_id)
        else:
            if not args.base_url or not args.text_model or not args.operator:
                parser.error("set requires --base-url, --text-model and --operator")
            key = sys.stdin.read().strip()
            candidate = models.candidate(args.tenant_id, SecretStr(key), base_url=args.base_url, text_model=args.text_model, vision_model=args.vision_model, coding_model=args.coding_model)
            missing = check_capabilities(candidate)
            if missing:
                print(json.dumps({"stored": False, "unsupported": missing}, ensure_ascii=False))
                return 1
            result = models.put(args.tenant_id, candidate, operator=args.operator)
    except (ModelCredentialError, ModelUnavailable) as exc:
        print(json.dumps({"error": getattr(exc, "code", "model_unavailable")}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
