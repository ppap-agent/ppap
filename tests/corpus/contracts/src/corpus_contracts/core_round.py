"""Controlled assembled regressions over explicit Suite sources and the real Core.

Only fixture construction lives here. No profile/filename routing, historical
binding inference, alternate executor, or replacement result projection is used.
The runtime/process authorization boundary has separate integration coverage.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from functools import lru_cache
from typing import Literal, cast

from agent.core_result import core_response
from agent.execution.audit_runtime import run_audit
from agent.mapping import spec_from_work
from audit.audits import DEEP_PROTOCOL_REVISION, RoundWork
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import StandardSuite, library_revision, predefined_directory_suites


@lru_cache(maxsize=None)
def customer_suite(customer: str, entry: str) -> StandardSuite:
    return next(suite for suite in predefined_directory_suites() if suite.suite_key == f"{customer}:{entry}")


def leaf_suite(company: str, document_type: str) -> StandardSuite:
    """The company leaf for one document type, e.g. ``leaf_suite("nio", "过程流程图")``."""
    return next(suite for suite in predefined_directory_suites() if suite.source_path.startswith(company + "/") and suite.document_type == document_type)


def bind_suite(suite: StandardSuite, rules: dict, aliases: dict[str, str]) -> StandardSuite:
    """Author an explicit fixture variant; aliases name source clauses (``N05:6`` or ``6``), not indices."""
    resource = ast.literal_eval(suite.files["resources.py"].decode().split("RESOURCE = ", 1)[1])
    sources = {alias: source if source in suite.entrypoints else f"{suite.source_entry}:{source}" for alias, source in aliases.items()}
    if set(sources.values()) - set(suite.entrypoints):
        raise ValueError("fixture_unknown_source_clause")
    resource = {**resource, "methods": {alias: resource["methods"][source] for alias, source in sources.items()}}
    binding = dict.fromkeys(aliases, "checker:create_audit")
    files = {**suite.files, "resources.py": f"RESOURCE = {resource!r}\n".encode(), "rules.json": json.dumps({**rules, "checker_binding": binding}, ensure_ascii=False).encode()}
    return replace(suite, files=files, entrypoints=binding)


def freeze_suite_round(work: RoundWork, blobs: dict[str, bytes], *, suites: dict[str, StandardSuite], document_types: dict[str, str] | None = None):
    """Freeze only caller-selected suites and explicitly identified reference types."""
    targets, inputs, programs, frozen_items = [], [], {}, []
    types = document_types or {}
    for item in work.inputs:
        suite = suites.get(item.file_version_id)
        body = item.rule_body if item.rule_body is not None else json.loads(suite.files["rules.json"]) if suite else None
        # Unavailable originals retain a fixed fixture identity, never empty
        # runtime bytes. Production obtains this digest from the accepted version.
        original = blobs.get(item.file_version_id, b"unavailable fixture original")
        digest = item.sha256 or hashlib.sha256(original).hexdigest()
        frozen_items.append(replace(item, sha256=digest, rule_body=body))
        inputs.append(
            FrozenInput(
                item.file_version_id,
                digest,
                cast(Literal["target", "reference"], item.role),
                item.filename,
                document_type=suite.document_type if suite else types.get(item.file_version_id),
                file_id=item.file_id,
                rule_revision_id=item.rule_version_id or "",
                rules=body,
            )
        )
        if item.role != "target":
            continue
        if suite is None or not item.rule_version_id or body is None:
            raise ValueError("fixture_suite_required")
        # Reordered/custom fixture clauses have their own immutable source digest.
        selected = replace(suite, files={**suite.files, "rules.json": json.dumps(body, ensure_ascii=False).encode()})
        binding = RuleBinding(selected.suite_key, checker_digest(selected.program_files), selected.entrypoints)
        targets.append(FrozenTarget(item.file_version_id, item.rule_version_id, selected.suite_key, selected.revision_digest, body, binding))
        programs[binding.checker_digest] = selected.program_files
    snapshot = ExecutionSnapshot(work.tenant_id, "controlled-regression", tuple(inputs), tuple(targets), "controlled-core", library_revision(), {}, round_id=work.round_id)
    frozen = replace(work, inputs=frozen_items, initiating_user_id=snapshot.initiating_user_id)
    spec = spec_from_work(frozen, "ppap-checker-core", snapshot.runtime_revision, DEEP_PROTOCOL_REVISION, checker_snapshot_digest=snapshot.input_digest)
    return snapshot, programs, spec


def run_suite_round(work: RoundWork, blobs: dict[str, bytes], *, suites: dict[str, StandardSuite], document_types: dict[str, str] | None = None) -> dict:
    snapshot, programs, spec = freeze_suite_round(work, blobs, suites=suites, document_types=document_types)
    unavailable = frozenset(item.file_version_id for item in snapshot.inputs if item.file_version_id not in blobs)
    result = run_audit(snapshot, blobs, programs, unavailable_sources=unavailable)
    projected = core_response(spec, snapshot, result).model_dump(mode="json")
    # The complete SDK receipt remains an independent runtime assertion, not a
    # claim that the product's customer charge projection uses this receipt.
    projected["usage"] = result["usage"]
    return projected
