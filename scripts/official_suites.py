"""Validate, read back, or explicitly publish one reviewed official Suite.

Validation defaults to no model access. --model live explicitly authorizes
real text, visual and Paddle inference without replay or answer recording. --record
writes validation evidence; publication remains a separate command.
Do not run against production without a separately authorized release.
"""

from __future__ import annotations

import argparse
import json

from corpus_contracts.official_models import LiveModels
from corpus_contracts.official_suites import publish, readback, validate
from corpus_contracts.replay import configured_model_access, offline
from product import build_product_store
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("validate", help="Run every corpus sample for this Suite; no publication.")
    verify.add_argument("--record", action="store_true", help="Explicitly write the resulting validation, even when unverified.")
    verify.add_argument("--expected-validation-revision", type=int)
    verify.add_argument("--model", choices=("disabled", "live"), default="disabled")
    read = commands.add_parser("read", help="Read the existing proof/head after an uncertain operation; no writes or execution.")
    release = commands.add_parser("publish", help="Publish only the exact proof and official head explicitly reviewed by the operator.")
    release.add_argument("--expected-head-revision", type=int, required=True)
    release.add_argument("--expected-validation-revision", type=int, required=True)
    release.add_argument("--expected-proof-digest", required=True)
    for command in (verify, read, release):
        command.add_argument("--revision-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "validate" and args.record != (args.expected_validation_revision is not None):
        parser.error("--record and --expected-validation-revision must be supplied together")
    writing = False
    models = None

    def before_record(value):
        nonlocal writing
        emit({"phase": "evaluated", "revision_id": args.revision_id, "expected_validation_revision": args.expected_validation_revision, **value})
        writing = True

    try:
        if args.command == "validate" and args.model == "live":
            if offline() or (access := configured_model_access()) is None:
                raise ValueError("official_live_configuration_required")
            models = LiveModels(access)
        store = build_product_store(Settings(), role="migrator")
        releases = SuiteReleaseStore(store.settings, store.objects, role="migrator")
        if args.command == "read":
            result = readback(releases, args.revision_id)
        elif args.command == "validate":
            result = validate(
                releases,
                args.revision_id,
                record=args.record,
                expected_validation_revision=args.expected_validation_revision,
                before_record=before_record,
                models=models,
                on_progress=emit,
            )
        else:
            emit(
                {
                    "phase": "publish_requested",
                    "revision_id": args.revision_id,
                    "expected_head_revision": args.expected_head_revision,
                    "expected_validation_revision": args.expected_validation_revision,
                    "expected_proof_digest": args.expected_proof_digest,
                }
            )
            writing = True
            result = publish(
                releases,
                args.revision_id,
                expected_revision=args.expected_head_revision,
                expected_validation_revision=args.expected_validation_revision,
                expected_proof_digest=args.expected_proof_digest,
            )
        emit({"phase": "completed", **result})
        return 2 if args.command == "validate" and not result["report"]["verified"] else 0
    except (ValueError, KeyError, PermissionError):
        # Parser/source errors can include document text; expose bounded status.
        emit({"phase": "blocked", "error": "official_suite_precondition_failed", "revision_id": args.revision_id, "retry": False, "models": models.receipt() if models else {"mode": "disabled"}})
        return 2
    except Exception:
        emit(
            {
                "phase": "unknown" if writing else "failed",
                "error": "official_suite_operation_incomplete",
                "revision_id": args.revision_id,
                "next_action": "read" if writing else "inspect_configuration",
                "retry": False,
                "models": models.receipt() if models else {"mode": "disabled"},
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
