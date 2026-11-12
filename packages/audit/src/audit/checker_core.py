"""Suite-level coverage and immutable execution contracts, without Python MRO routing."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from audit.contracts import AuditResultItem

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_ENTRYPOINT = re.compile(r"^[a-zA-Z_]\w*:[a-zA-Z_]\w*$")

# The non-secret identity of the models an operation froze. Historical rows carry the
# first four keys; current ones also name their source and their vision and coding models.
LEGACY_MODEL_IDENTITY_KEYS = frozenset({"provider", "model", "revision", "deployment"})
MODEL_IDENTITY_KEYS = LEGACY_MODEL_IDENTITY_KEYS | {"source", "vision_model", "coding_model"}


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def rule_digest(rules: Mapping[str, Any], appendix: Sequence[Mapping[str, Any]] = ()) -> str:
    """IDs alone are insufficient: rule text and confirmed appendices are authority."""
    return _digest({"rules": _thaw(rules), "appendix": _thaw(tuple(appendix))})


def runtime_revision(policy_revision: str, library_revision: str) -> str:
    """One shared identity for runtime policy, deployment and installed library."""
    if not policy_revision or not library_revision:
        raise ValueError("runtime_identity_required")
    return "checker-core-v1:" + _digest({"policy_revision": policy_revision, "library_revision": library_revision})


def checker_digest(files: Mapping[str, bytes]) -> str:
    hasher = hashlib.sha256()
    for name in sorted(files):
        hasher.update(name.encode())
        hasher.update(b"\0")
        hasher.update(files[name])
        hasher.update(b"\0")
    return hasher.hexdigest()


@dataclass(frozen=True)
class RuleBinding:
    suite_key: str
    checker_digest: str
    entrypoints: Mapping[str, str]
    validated_rule_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.suite_key or not _DIGEST.fullmatch(self.checker_digest):
            raise ValueError("invalid_rule_binding")
        if self.validated_rule_digest is not None and not _DIGEST.fullmatch(self.validated_rule_digest):
            raise ValueError("invalid_validated_rule_digest")
        if any(not clause or not _ENTRYPOINT.fullmatch(entrypoint) for clause, entrypoint in self.entrypoints.items()):
            raise ValueError("invalid_rule_entrypoint")
        object.__setattr__(self, "entrypoints", MappingProxyType(dict(self.entrypoints)))

    def to_dict(self) -> dict:
        return {"suite_key": self.suite_key, "checker_digest": self.checker_digest, "entrypoints": dict(self.entrypoints), "validated_rule_digest": self.validated_rule_digest}


@dataclass(frozen=True)
class ValidationRecord:
    suite_digest: str
    rule_digest: str
    checker_digest: str
    related_suite_digests: Mapping[str, str]
    library_revision: str

    def __post_init__(self) -> None:
        if not self.library_revision or any(not _DIGEST.fullmatch(value) for value in (self.suite_digest, self.rule_digest, self.checker_digest, *self.related_suite_digests.values())):
            raise ValueError("invalid_validation_identity")
        object.__setattr__(self, "related_suite_digests", MappingProxyType(dict(self.related_suite_digests)))

    def status(self, *, suite_digest: str, rule_digest: str, checker_digest: str, related_suite_digests: Mapping[str, str], library_revision: str) -> dict:
        # Compare the direct dependency's content, never its validation status.
        valid = (self.suite_digest, self.rule_digest, self.checker_digest, dict(self.related_suite_digests)) == (suite_digest, rule_digest, checker_digest, dict(related_suite_digests))
        return {"state": "verified" if valid else "stale", "samples_not_rerun": self.library_revision != library_revision}


@dataclass(frozen=True)
class FrozenInput:
    file_version_id: str
    sha256: str
    role: Literal["target", "reference"]
    filename: str
    document_type: str | None = None
    file_id: str = ""
    customer: str | None = None
    model: str | None = None
    part: str | None = None
    project_id: str = "default"
    rule_revision_id: str = ""
    rules: Mapping[str, Any] | None = None
    appendix: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.file_version_id or not _DIGEST.fullmatch(self.sha256) or self.role not in {"target", "reference"} or not self.filename:
            raise ValueError("invalid_frozen_input")
        if bool(self.rule_revision_id) != (self.rules is not None) or (self.appendix and self.rules is None):
            raise ValueError("incomplete_frozen_source_rule")
        object.__setattr__(self, "rules", _freeze(self.rules))
        object.__setattr__(self, "appendix", _freeze(self.appendix))

    def to_dict(self) -> dict:
        return {
            "file_version_id": self.file_version_id,
            "sha256": self.sha256,
            "role": self.role,
            "filename": self.filename,
            "document_type": self.document_type,
            "file_id": self.file_id,
            "customer": self.customer,
            "model": self.model,
            "part": self.part,
            "project_id": self.project_id,
            "rule_revision_id": self.rule_revision_id,
            "rules": _thaw(self.rules),
            "appendix": _thaw(self.appendix),
        }


@dataclass(frozen=True)
class FrozenTarget:
    file_version_id: str
    suite_revision_id: str
    suite_key: str
    suite_digest: str
    rules: Mapping[str, Any]
    binding: RuleBinding
    appendix: tuple[Mapping[str, Any], ...] = ()
    verification: Literal["verified", "unverified"] = "unverified"
    related_suite_digests: Mapping[str, str] = field(default_factory=dict)
    samples_not_rerun: bool = False

    def __post_init__(self) -> None:
        if not self.file_version_id or not self.suite_revision_id or not _DIGEST.fullmatch(self.suite_digest) or self.binding.suite_key != self.suite_key:
            raise ValueError("invalid_frozen_target")
        if self.verification not in {"verified", "unverified"}:
            raise ValueError("invalid_verification_state")
        ids = clause_ids(self.rules)
        if not set(self.binding.entrypoints) <= set(ids):
            raise ValueError("binding_unknown_clause")
        if self.verification == "verified" and self.binding.validated_rule_digest != rule_digest(self.rules, self.appendix):
            raise ValueError("binding_rule_digest_mismatch")
        object.__setattr__(self, "rules", _freeze(self.rules))
        object.__setattr__(self, "appendix", _freeze(self.appendix))
        object.__setattr__(self, "related_suite_digests", MappingProxyType(dict(self.related_suite_digests)))

    def to_dict(self) -> dict:
        return {
            "file_version_id": self.file_version_id,
            "suite_revision_id": self.suite_revision_id,
            "suite_key": self.suite_key,
            "suite_digest": self.suite_digest,
            "rules": _thaw(self.rules),
            "appendix": _thaw(self.appendix),
            "binding": self.binding.to_dict(),
            "verification": self.verification,
            "related_suite_digests": dict(self.related_suite_digests),
            "samples_not_rerun": self.samples_not_rerun,
        }


@dataclass(frozen=True)
class ExecutionSnapshot:
    tenant_id: str
    initiating_user_id: str
    inputs: tuple[FrozenInput, ...]
    targets: tuple[FrozenTarget, ...]
    runtime_revision: str
    library_revision: str
    model_identity: Mapping[str, str]
    round_id: str = ""
    schema_version: Literal["1"] = "1"

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.initiating_user_id or not self.runtime_revision or not self.library_revision:
            raise ValueError("incomplete_execution_identity")
        # Identity is an allowlist, never a copy of Settings or provider config.
        if not set(self.model_identity) <= MODEL_IDENTITY_KEYS or any(not isinstance(value, str) for value in self.model_identity.values()):
            raise ValueError("invalid_model_identity")
        inputs, targets = tuple(self.inputs), tuple(self.targets)
        by_id = {item.file_version_id: item for item in inputs}
        if len(by_id) != len(inputs) or len({target.file_version_id for target in targets}) != len(targets):
            raise ValueError("duplicate_snapshot_file")
        if not targets or {target.file_version_id for target in targets} != {item.file_version_id for item in inputs if item.role == "target"}:
            raise ValueError("snapshot_target_mismatch")
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "model_identity", MappingProxyType(dict(self.model_identity)))

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "initiating_user_id": self.initiating_user_id,
            "round_id": self.round_id,
            "inputs": [item.to_dict() for item in self.inputs],
            "targets": [target.to_dict() for target in self.targets],
            "runtime_revision": self.runtime_revision,
            "library_revision": self.library_revision,
            "model_identity": dict(self.model_identity),
        }

    @property
    def input_digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ExecutionSnapshot:
        data = dict(raw)
        data["inputs"] = tuple(FrozenInput(**row) for row in data["inputs"])
        data["targets"] = tuple(FrozenTarget(**{**row, "binding": RuleBinding(**row["binding"])}) for row in data["targets"])
        return cls(**data)

    def validate_sources(self, sources: Mapping[str, bytes], *, unavailable: frozenset[str] = frozenset()) -> None:
        # Availability belongs to this execution attempt, never to the frozen
        # source identity. Only an explicit, disjoint disposition can omit bytes.
        if set(sources) & unavailable or set(sources) | unavailable != {item.file_version_id for item in self.inputs}:
            raise ValueError("snapshot_sources_mismatch")
        for item in self.inputs:
            if item.file_version_id in unavailable:
                continue
            if hashlib.sha256(sources[item.file_version_id]).hexdigest() != item.sha256:
                raise ValueError("snapshot_source_digest_mismatch")


def clause_ids(rules: Mapping[str, Any]) -> tuple[str, ...]:
    rows = rules.get("rules")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("invalid_rule_items")
    ids = tuple(row.get("id") if isinstance(row, Mapping) else None for row in rows)
    if any(not isinstance(clause, str) or not clause.strip() for clause in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid_clause_ids")
    return ids  # type: ignore[return-value]


def clause_rejections(rules: Mapping[str, Any], clause_id: str) -> tuple[Mapping[str, Any], ...]:
    """Parts of one clause a person rejected, in its confirmed snapshot, because they give no checkable criterion."""
    specs = rules.get("formalizations")
    item = specs.get(clause_id) if isinstance(specs, Mapping) else None
    found = item.get("rejections") if isinstance(item, Mapping) and item.get("clause_id") == clause_id else None
    return tuple(entry for entry in found or () if isinstance(entry, Mapping)) if isinstance(found, (list, tuple)) else ()


def rejected_whole(rejections: Sequence[Mapping[str, Any]]) -> bool:
    return any(item.get("scope") == "clause" for item in rejections)


def rejection_basis(rejections: Sequence[Mapping[str, Any]]) -> str:
    """The basis an UNABLE carries: what was rejected, why, and that a person must review it."""
    stated = []
    for item in rejections:
        parts = "；".join(str(part) for part in item.get("parts") or ()) or ("整条条款" if item.get("scope") == "clause" else "部分内容")
        stated.append(f"{parts}（原因：{item.get('reason') or '未写明'}）")
    return "已按用户决定拒绝自动判定，无可程序化判定标准：" + "；".join(stated) + "。请人工复核。"


def without_rejected_pass(result: dict, rejections: Sequence[Mapping[str, Any]]) -> dict:
    """A rejected part was never checked, so the clause cannot pass. A defect in the defined parts stays NG."""
    if not rejections or result.get("status") != "PASS":
        return result
    checked = str(result.get("suggestions") or "").strip()
    basis = rejection_basis(rejections)
    return {**result, "status": "UNABLE", "suggestions": f"已定义部分：{checked}\n{basis}" if checked else basis}


SEVERITIES = ("A", "B", "C")
# A reason must state the actual consequence, not a stance.
_EMPTY_REASONS = {"按经验", "经验判断", "综合判断", "综合考虑", "酌情", "视情况"}


def _graded(checked: dict, rule: Mapping[str, Any]) -> dict:
    """Final level = the rule default, or a checker override with a factual reason.

    Only an NG has a level. The level never changes the verdict.
    """
    default = rule.get("severity") if rule.get("severity") in SEVERITIES else None
    assigned, reason = checked.pop("severity", None), (checked.pop("severity_reason", None) or "").strip()
    checked.update(severity=None, severity_override=None)
    if checked["status"] != "NG":
        return checked
    if assigned and assigned != default:
        if len(reason) < 4 or reason.strip("。.，, ") in _EMPTY_REASONS:
            return {**checked, "status": "ERR", "suggestions": "级别覆盖缺少可核实的理由，未取得业务结论。"}
        checked["severity_override"] = {"default": default, "level": assigned, "reason": reason}
    checked["severity"] = assigned or default
    return checked


def validate_results(results: Sequence[AuditResultItem | dict], *, target: FrozenTarget, inputs: Sequence[FrozenInput]) -> dict:
    """Results are attributed by explicit clause ID, never output position."""
    input_ids = {item.file_version_id for item in inputs}
    if target.file_version_id not in {item.file_version_id for item in inputs if item.role == "target"}:
        raise ValueError("result_target_missing")
    by_clause: dict[str, dict] = {}
    for raw in results:
        data = raw.model_dump(mode="json") if isinstance(raw, AuditResultItem) else dict(raw)
        clause = data.get("clause_id")
        if not isinstance(clause, str) or clause not in target.binding.entrypoints:
            raise ValueError("result_unknown_clause")
        if clause in by_clause:
            raise ValueError("duplicate_clause_result")
        if data.get("status") not in {"PASS", "NG", "UNABLE", "ERR", "NA"}:
            raise ValueError("invalid_result_status")
        if data.get("file_version_id", target.file_version_id) != target.file_version_id:
            raise ValueError("result_wrong_target")
        references = data.get("evidence_file_version_ids", [target.file_version_id])
        if not isinstance(references, (list, tuple)) or not references or any(value not in input_ids for value in references) or len(references) != len(set(references)):
            raise ValueError("result_invalid_file_reference")
        # Validate the existing product evidence format and retain its source refs.
        override = data.pop("severity_override", None)
        if isinstance(override, Mapping) and not data.get("severity_reason"):
            # Re-validating an already graded result reproduces the same override.
            data["severity_reason"] = override.get("reason")
        checked = AuditResultItem.model_validate(data).model_dump(mode="json")
        for source in (checked.get("evidence") or {}).get("sources") or []:
            locator = source["locator"]
            try:
                location = json.loads(locator)
            except (ValueError, TypeError):
                continue  # Existing non-JSON cell locators refer to the target.
            if isinstance(location, dict):
                referenced = location.get("file_version_id") or location.get("document_id")
                if referenced is not None and referenced not in references:
                    raise ValueError("result_invalid_file_reference")
        rule = next(row for row in target.rules["rules"] if row.get("id") == clause)
        by_clause[clause] = {**_graded(checked, rule), "file_version_id": target.file_version_id, "evidence_file_version_ids": list(references)}
    findings, coverage = [], []
    for clause in clause_ids(target.rules):
        mapped = clause in target.binding.entrypoints
        found = by_clause.get(clause)
        coverage.append({"file_version_id": target.file_version_id, "clause_id": clause, "coverage": "mapped" if mapped else "unsupported"})
        if found is not None:
            findings.append(found)
        elif mapped:
            findings.append(
                {
                    "file_version_id": target.file_version_id,
                    "clause_id": clause,
                    "status": "ERR",
                    "classification": "",
                    "item": "",
                    "priority": "",
                    "suggestions": "Checker returned no result.",
                    "evidence_file_version_ids": [target.file_version_id],
                }
            )
    return {"findings": findings, "coverage": coverage}
