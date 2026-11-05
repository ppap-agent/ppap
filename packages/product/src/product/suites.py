"""Immutable, tenant-scoped Suite content. Publication pointers live elsewhere."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

SUITE_PATHS = frozenset({"rules.json", "appendix.json", "checker.py", "resources.py", "templates.json", "tests.json"})
REQUIRED_PATHS = frozenset({"rules.json", "checker.py"})
MAX_SUITE_DEPTH = 16
MAX_OBJECT_BYTES = 8 * 1024 * 1024
DIGEST = re.compile(r"^[a-f0-9]{64}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def content_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def resolved_digest(suite_key: str, related_suite_keys: tuple[str, ...], objects: Mapping[str, str]) -> str:
    return content_digest(canonical_json({"suite_key": suite_key, "related_suite_keys": sorted(related_suite_keys), "objects": dict(objects)}))


@dataclass(frozen=True)
class SuiteRevision:
    id: str
    tenant_id: str | None
    suite_key: str
    related_suite_keys: tuple[str, ...]
    base_revision_id: str | None
    overrides: Mapping[str, str]
    resolved_digest: str
    created_by: str | None
    revision_number: int = 1

    def __post_init__(self) -> None:
        if self.revision_number < 1:
            raise ValueError("invalid_revision_number")
        if not self.id or not self.suite_key or self.suite_key.strip() != self.suite_key:
            raise ValueError("invalid_suite_identity")
        related = tuple(sorted(self.related_suite_keys))
        if self.suite_key in related or len(set(related)) != len(related) or any(not key or key.strip() != key for key in related):
            raise ValueError("invalid_related_suite_keys")
        if not set(self.overrides) <= SUITE_PATHS or any(not DIGEST.fullmatch(value) for value in self.overrides.values()):
            raise ValueError("invalid_suite_objects")
        if not DIGEST.fullmatch(self.resolved_digest):
            raise ValueError("invalid_suite_digest")
        object.__setattr__(self, "related_suite_keys", related)
        object.__setattr__(self, "overrides", MappingProxyType(dict(self.overrides)))


@dataclass(frozen=True)
class ResolvedSuite:
    revision: SuiteRevision
    files: Mapping[str, bytes]
    object_digests: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))
        object.__setattr__(self, "object_digests", MappingProxyType(dict(self.object_digests)))


def validate_content(files: Mapping[str, bytes], *, official: bool) -> None:
    if not REQUIRED_PATHS <= files.keys() or not set(files) <= SUITE_PATHS:
        raise ValueError("suite_required_content_missing")
    if any(not isinstance(body, bytes) or not body or len(body) > MAX_OBJECT_BYTES for body in files.values()):
        raise ValueError("invalid_suite_object_bytes")
    rules = json.loads(files["rules.json"])
    if not isinstance(rules, dict) or not isinstance(rules.get("rules"), list):
        raise ValueError("invalid_suite_rules")
    ids = [row.get("id") if isinstance(row, dict) else None for row in rules["rules"]]
    if any(not isinstance(value, str) or not value.strip() for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid_suite_clause_ids")
    for path in ("appendix.json", "tests.json", "templates.json"):
        if path in files:
            value = json.loads(files[path])
            if not isinstance(value, list):
                raise ValueError("invalid_suite_resource")
            if path == "templates.json":
                for template in value:
                    if not isinstance(template, dict) or template.get("kind") not in {"structure", "licensed_blank"}:
                        raise ValueError("invalid_template_reference")
                    if template["kind"] == "licensed_blank" and (not template.get("license") or not template.get("uri")):
                        raise ValueError("template_license_required")
                    if official and template["kind"] == "licensed_blank" and template.get("distributable") is not True:
                        raise ValueError("template_distribution_not_authorized")


def resolve_suite(
    revision_id: str,
    tenant_id: str,
    *,
    load_revision: Callable[[str], SuiteRevision | None],
    load_object: Callable[[str | None, str], bytes],
    max_depth: int = MAX_SUITE_DEPTH,
) -> ResolvedSuite:
    """Every ancestor and byte is checked, including overridden ancestor bytes."""
    if not tenant_id or max_depth < 1 or max_depth > MAX_SUITE_DEPTH:
        raise ValueError("invalid_suite_resolution")
    chain: list[SuiteRevision] = []
    seen: set[str] = set()
    current: str | None = revision_id
    while current is not None:
        if current in seen or len(chain) >= max_depth:
            raise ValueError("suite_cycle_or_depth")
        revision = load_revision(current)
        if revision is None or revision.id != current or revision.tenant_id not in (None, tenant_id):
            raise KeyError("suite_missing")
        if chain and (revision.suite_key != chain[0].suite_key or chain[-1].tenant_id is None and revision.tenant_id is not None):
            raise ValueError("invalid_suite_base")
        seen.add(current)
        chain.append(revision)
        current = revision.base_revision_id
    files: dict[str, bytes] = {}
    objects: dict[str, str] = {}
    for revision in reversed(chain):
        for path, digest in revision.overrides.items():
            body = load_object(revision.tenant_id, digest)
            if not isinstance(body, bytes) or len(body) > MAX_OBJECT_BYTES or content_digest(body) != digest:
                raise ValueError("suite_object_digest_mismatch")
            files[path], objects[path] = body, digest
        validate_content(files, official=revision.tenant_id is None)
        if resolved_digest(revision.suite_key, revision.related_suite_keys, objects) != revision.resolved_digest:
            raise ValueError("suite_resolved_digest_mismatch")
    return ResolvedSuite(chain[0], files, objects)
