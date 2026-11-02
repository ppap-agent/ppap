"""Host contract for developing a checker and executing only the fixed program."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput
from audit.kinds import cross_file_doc_type_name
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.checker_spec import CheckerArtifact, CheckerJobSpec
from agent.client import DeepError
from agent.execution.checker_guest import CheckerProgramError, ProgramRunner, develop_checker, execute_fixed_checker
from agent.execution.guest_model import ModelProtocolError
from agent.protocol import ExecuteRequest

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024


class Objects(Protocol):
    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None: ...


class CheckerUsage(BaseModel):
    """Confirmed model usage for one checker attempt. A replay must not invent a second charge."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    attempt: int = Field(ge=1, strict=True)
    usage_source: Literal["unknown", "provider", "controlled"]
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    completed_tool_calls: int = Field(default=0, ge=0)
    compactions: int = Field(default=0, ge=0)
    model_requests: int = Field(default=0, ge=0)

    def as_usage(self) -> dict[str, int]:
        names = ("input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions", "model_requests")
        return {name: int(getattr(self, name)) for name in names}


class CheckerRecovery(BaseModel):
    """One attempt whose model consumption must be read back before another call."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    attempt: int = Field(ge=1, strict=True)
    uncertain: bool
    code: str = Field(min_length=1)


class CheckerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str = Field(pattern=r"^(adapt-checker|execute-checker)$")
    spec: CheckerJobSpec
    document: str = "document"
    audit_id: str = Field(min_length=1)
    rule: dict[str, Any]
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_version_id: str = ""
    role: str = Field(min_length=1)
    appendix: list[dict[str, Any]] = Field(default_factory=list)
    group_context: dict[str, Any] | None = None
    related_documents: dict[str, str] = Field(default_factory=dict)
    prior_files: dict[str, str] | None = None
    artifact: CheckerArtifact | None = None


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _clauses(rule: RuleInput) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    for raw in rule.rules:
        if isinstance(raw, dict):
            clauses.append(
                {
                    "id": str(raw.get("id") or "").strip(),
                    "category": str(raw.get("category") or ""),
                    "priority": raw.get("priority"),
                    "rule_to_check": str(raw.get("rule_to_check") or ""),
                    "type_of_rule": str(raw.get("type_of_rule") or ""),
                }
            )
            continue
        item = raw.model_dump(mode="json") if isinstance(raw, BaseModel) else dict(raw)
        clauses.append(
            {
                "id": str(item.get("id") or "").strip(),
                "category": str(item.get("category") or ""),
                "priority": item.get("priority"),
                "rule_to_check": str(item.get("rule_to_check") or ""),
                "type_of_rule": str(item.get("type_of_rule") or ""),
            }
        )
    return clauses


def _context_items(group_context: GroupContextInput) -> dict[str, AuditInput]:
    """One identity per audit ID. by_audit_id wins when both maps carry the same ID."""
    found: dict[str, AuditInput] = {}
    ordered = list(group_context.by_audit_id.values())
    for rows in group_context.by_doc_type.values():
        ordered.extend(rows)
    for item in ordered:
        current = found.get(item.audit_id)
        if current is None:
            found[item.audit_id] = item
            continue
        if (current.file_metadata or {}) != (item.file_metadata or {}):
            raise DeepError("deep_input_invalid")
    return found


def _related(group_context: GroupContextInput | None) -> list[dict[str, Any]]:
    if group_context is None:
        return []
    items = _context_items(group_context)
    related: list[dict[str, Any]] = []
    for audit_id in sorted(items):
        item = items[audit_id]
        metadata = item.file_metadata or {}
        entry: dict[str, Any] = {
            "audit_id": audit_id,
            "file_version_id": str(metadata.get("file_version_id") or ""),
            "sha256": str(metadata.get("sha256") or ""),
            "role": str(metadata.get("role") or ""),
        }
        rule = group_context.rules_by_id.get(audit_id)
        if rule is not None and (rule.rules or rule.appendix):
            entry["rule_id"] = rule.rule_id
            entry["rules"] = _clauses(rule)
            entry["appendix"] = list(rule.appendix)
        related.append(entry)
    return related


def group_context_for_spec(spec: ExecuteRequest) -> GroupContextInput | None:
    """Reference files visible to every fixed checker. Host paths are not included."""
    references = [item for item in spec.files if item.role == "reference"]
    if not references:
        return None
    target_ids = {item.file_version_id for item in spec.files if item.role == "target"}
    if len({item.file_version_id for item in references}) != len(references) or any(item.file_version_id in target_ids for item in references):
        raise DeepError("deep_input_invalid")
    by_audit_id: dict[str, AuditInput] = {}
    by_doc_type: dict[str, list[AuditInput]] = {}
    rules_by_id: dict[str, RuleInput] = {}
    for item in references:
        body = item.rule_body if isinstance(item.rule_body, dict) else {}
        rule_type = body.get("type")
        audit = AuditInput(
            audit_id=item.file_version_id,
            file_url=item.filename,
            file_metadata={
                "file_version_id": item.file_version_id,
                "sha256": item.sha256,
                "role": item.role,
                "original_filename": item.filename,
            },
            project_id=spec.tenant_id or "default",
        )
        by_audit_id[item.file_version_id] = audit
        type_name = cross_file_doc_type_name(filename=item.filename, rule_type=rule_type if isinstance(rule_type, str) and rule_type.strip() else None)
        if type_name:
            by_doc_type.setdefault(type_name, []).append(audit)
        clauses = [clause for clause in body.get("rules", []) if isinstance(clause, dict)] if isinstance(body.get("rules"), list) else []
        clause_ids = {str(clause.get("id") or "").strip() for clause in clauses}
        raw_appendix = body.get("appendix") if isinstance(body.get("appendix"), list) else []
        appendix = applicable_appendix(raw_appendix, file_version_id=item.file_version_id, clause_ids=clause_ids)
        if clauses or appendix:
            rules_by_id[item.file_version_id] = RuleInput(
                rule_id=item.rule_version_id or item.file_version_id,
                rule_profile=str(body.get("rule_profile") or "v3"),
                type=body.get("type") if isinstance(body.get("type"), str) else None,
                rules=clauses,
                appendix=appendix,
            )
    return GroupContextInput(task_group_id=spec.round_id, by_audit_id=by_audit_id, by_doc_type=by_doc_type, rules_by_id=rules_by_id)


def verified_related_bytes(spec: ExecuteRequest, files: dict[str, bytes]) -> dict[str, bytes]:
    context = group_context_for_spec(spec)
    if context is None:
        return {}
    payload: dict[str, bytes] = {}
    for audit_id, item in _context_items(context).items():
        raw = files.get(audit_id)
        expected = str((item.file_metadata or {}).get("sha256") or "")
        if raw is None or not expected or hashlib.sha256(raw).hexdigest() != expected:
            raise DeepError("deep_integrity_mismatch")
        payload[audit_id] = raw
    return payload


def applicable_appendix(appendix: list[dict[str, Any]] | None, *, file_version_id: str, clause_ids: set[str]) -> list[dict[str, Any]]:
    """Keep confirmed appendix entries that apply to this file and these clauses."""
    selected: list[dict[str, Any]] = []
    for item in appendix or []:
        if not isinstance(item, dict):
            continue
        scope = item.get("scope")
        if not isinstance(scope, dict):
            selected.append(item)
            continue
        if _appendix_scope_applies(scope, file_version_id, clause_ids):
            selected.append(item)
    return selected


def _appendix_scope_applies(scope: dict[str, Any], file_version_id: str, clause_ids: set[str]) -> bool:
    """A partial scope still restricts. It must not leak to another file or clause."""
    targets = scope.get("rule_targets")
    if isinstance(targets, list):
        return any(
            isinstance(target, dict)
            and str(target.get("document_id") or "") == file_version_id
            and str(target.get("rule_id") or "") in clause_ids
            for target in targets
        )
    restricted = False
    document_ok = True
    rule_ok = True
    documents = scope.get("document_ids")
    rules = scope.get("rule_ids")
    if isinstance(documents, list):
        restricted = True
        document_ok = file_version_id in {str(item) for item in documents}
    if isinstance(rules, list):
        restricted = True
        rule_ok = bool(clause_ids.intersection(str(item) for item in rules))
    if not restricted:
        return True
    return document_ok and rule_ok


def input_digest(
    audit_file: AuditInput,
    rule: RuleInput,
    *,
    file_sha256: str,
    role: str,
    appendix: list[dict[str, Any]] | None = None,
    group_context: GroupContextInput | None = None,
) -> str:
    """Identity of this execution input. A rule ID alone is not enough."""
    payload = {
        "appendix": appendix or [],
        "file_sha256": file_sha256,
        "file_version_id": str((audit_file.file_metadata or {}).get("file_version_id") or ""),
        "related": _related(group_context),
        "role": role,
        "rule_id": rule.rule_id,
        "rule_profile": rule.rule_profile,
        "rules": _clauses(rule),
        "type": rule.type,
    }
    return hashlib.sha256(encoded(payload)).hexdigest()


def checker_program_key(tenant_id: str, executor_revision: str, input_digest: str) -> str:
    """Locate the program fixed for one exact input. A rule ID is not this key."""
    tenant = hashlib.sha256(tenant_id.encode()).hexdigest()
    return f"deep/{tenant}/{executor_revision}/checker-programs/{input_digest}.json"


MAX_START_GENERATIONS = 1024


def checker_start_key(tenant_id: str, executor_revision: str, rule: RuleInput) -> str:
    """Legacy single object for the same rule content. This is not an execution key."""
    return checker_start_prefix(tenant_id, executor_revision, rule) + ".json"


def checker_start_prefix(tenant_id: str, executor_revision: str, rule: RuleInput) -> str:
    """Directory of start programs for the same rule content. A newer file must recheck the latest one."""
    tenant = hashlib.sha256(tenant_id.encode()).hexdigest()
    identity = {
        "clauses": _clauses(rule),
        "executor_revision": executor_revision,
        "profile": rule.rule_profile,
        "rule_version_id": rule.rule_id,
        "type": rule.type,
    }
    digest = hashlib.sha256(encoded(identity)).hexdigest()
    return f"deep/{tenant}/{executor_revision}/checker-starts/{digest}"


def checker_type_start_key(tenant_id: str, executor_revision: str, rule: RuleInput) -> str:
    """Legacy single object for the same document type. This is not an execution key."""
    return checker_type_start_prefix(tenant_id, executor_revision, rule) + ".json"


def checker_type_start_prefix(tenant_id: str, executor_revision: str, rule: RuleInput) -> str:
    """Directory of start programs for the same document type. This is not an execution key."""
    tenant = hashlib.sha256(tenant_id.encode()).hexdigest()
    identity = {
        "executor_revision": executor_revision,
        "profile": rule.rule_profile,
        "type": rule.type,
    }
    digest = hashlib.sha256(encoded(identity)).hexdigest()
    return f"deep/{tenant}/{executor_revision}/checker-type-starts/{digest}"


def start_generation_key(prefix: str, generation: int) -> str:
    """One append-only start program. Generation 1 is the oldest program in the directory."""
    if type(generation) is not int or generation < 1:
        raise ValueError("invalid start generation")
    return f"{prefix}/{generation}.json"


def _checked_digest(
    audit_file: AuditInput,
    rule: RuleInput,
    file_bytes: bytes,
    *,
    file_sha256: str,
    role: str,
    appendix: list[dict[str, Any]] | None,
    group_context: GroupContextInput | None,
) -> str:
    if not role.strip():
        raise DeepError("deep_input_invalid")
    if hashlib.sha256(file_bytes).hexdigest() != file_sha256:
        raise DeepError("deep_integrity_mismatch")
    return input_digest(audit_file, rule, file_sha256=file_sha256, role=role, appendix=appendix, group_context=group_context)


def store_observation(objects: Objects, key: str, observations: list[str]) -> str:
    """Store one observation. A recompute is a new calculation and does not replace the previous one."""
    from audit.storage_errors import ObjectExists

    try:
        objects.put_exclusive(key, encoded(observations), "application/json")
        return key
    except ObjectExists:
        computation_id = uuid4().hex
        if key.endswith(".trials.json"):
            alt = key[: -len(".trials.json")] + f".observations/{computation_id}.json"
        else:
            alt = f"{key}.{computation_id}.json"
        objects.put_exclusive(
            alt,
            encoded({"computation_id": computation_id, "observations": observations, "recomputed": True}),
            "application/json",
        )
        return alt


def _store(objects: Objects | None, artifact_prefix: str | None, artifact: CheckerArtifact, trials: list[str]) -> None:
    if objects is None or not artifact_prefix:
        return
    objects.put_exclusive(
        f"{artifact_prefix}/checkers/{artifact.input_digest}.json",
        encoded(artifact.model_dump(mode="json")),
        "application/json",
    )
    if trials:
        store_observation(objects, f"{artifact_prefix}/checkers/{artifact.input_digest}.trials.json", trials)


def adapt(
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
    *,
    file_bytes: bytes,
    file_sha256: str,
    role: str,
    executor_revision: str,
    runner: ProgramRunner,
    work: Path,
    deadline: float,
    appendix: list[dict[str, Any]] | None = None,
    objects: Objects | None = None,
    artifact_prefix: str | None = None,
    prior: CheckerArtifact | None = None,
) -> CheckerArtifact:
    digest = _checked_digest(audit_file, rule, file_bytes, file_sha256=file_sha256, role=role, appendix=appendix, group_context=group_context)
    prior_files = {name: content.decode("utf-8") for name, content in prior.files.items()} if prior is not None else None
    observations: list[str] = []
    try:
        artifact = develop_checker(
            audit_file=audit_file,
            rule=rule,
            group_context=group_context,
            digest=digest,
            executor_revision=executor_revision,
            runner=runner,
            work=work,
            deadline=deadline,
            prior_files=prior_files,
            appendix=appendix,
            observations=observations,
        )
    except ModelProtocolError as exc:
        raise DeepError("deep_model_protocol") from exc
    except TimeoutError as exc:
        raise DeepError("deep_timeout") from exc
    except CheckerProgramError as exc:
        raise DeepError("deep_output_invalid") from exc
    if artifact.input_digest != digest or artifact.executor_revision != executor_revision:
        raise DeepError("deep_output_identity_mismatch")
    _store(objects, artifact_prefix, artifact, observations)
    return artifact


def execute(
    artifact: CheckerArtifact,
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
    *,
    file_bytes: bytes,
    file_sha256: str,
    role: str,
    executor_revision: str,
    work: Path,
    appendix: list[dict[str, Any]] | None = None,
) -> list[AuditResultItem]:
    digest = _checked_digest(audit_file, rule, file_bytes, file_sha256=file_sha256, role=role, appendix=appendix, group_context=group_context)
    if artifact.input_digest != digest or artifact.executor_revision != executor_revision:
        raise DeepError("deep_output_identity_mismatch")
    try:
        return execute_fixed_checker(
            artifact,
            audit_file=audit_file,
            rule=rule,
            group_context=group_context,
            work=work,
            digest=digest,
            executor_revision=executor_revision,
        )
    except CheckerProgramError as exc:
        raise DeepError("deep_output_invalid") from exc


_STAGED_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".docx", ".pdf"}


def staged_document_name(original_name: str) -> str:
    """Keep the original suffix so PDF conversion can see a PDF path."""
    suffix = Path(original_name).suffix.lower()
    if suffix not in _STAGED_SUFFIXES:
        suffix = ".bin"
    return f"target{suffix}"


def _related_name(audit_id: str, original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    if suffix not in _STAGED_SUFFIXES:
        suffix = ".bin"
    token = hashlib.sha256(audit_id.encode()).hexdigest()[:32]
    return f"related-{token}{suffix}"


def _staged_manifest(manifest: CheckerManifest, file_bytes: bytes, related_bytes: dict[str, bytes]) -> CheckerManifest:
    if manifest.group_context is None:
        if manifest.related_documents:
            raise CheckerProgramError("关联文件清单与上下文不一致")
        return manifest
    context = GroupContextInput.model_validate(manifest.group_context)
    items = _context_items(context)
    names: dict[str, str] = {}
    used = {manifest.document, "manifest.json"}
    for audit_id, item in items.items():
        metadata = item.file_metadata or {}
        expected = str(metadata.get("sha256") or "")
        if audit_id == manifest.audit_id:
            raw = file_bytes
            name = manifest.document
        else:
            raw = related_bytes.get(audit_id)
            name = _related_name(audit_id, str(metadata.get("original_filename") or ""))
        actual = hashlib.sha256(raw).hexdigest() if raw is not None else ""
        collision = name in used and not (audit_id == manifest.audit_id and name == manifest.document)
        if raw is None or not expected or actual != expected or collision or name in names.values():
            raise CheckerProgramError("关联文件与冻结摘要不一致")
        names[audit_id] = name
        used.add(name)
    dumped = context.model_dump(mode="json")
    for audit_id, name in names.items():
        if audit_id in dumped["by_audit_id"]:
            dumped["by_audit_id"][audit_id]["file_url"] = name
        for rows in dumped["by_doc_type"].values():
            for row in rows:
                if row.get("audit_id") == audit_id:
                    row["file_url"] = name
    return manifest.model_copy(update={"group_context": dumped, "related_documents": names})


def _bound_group_context(inputs: Path, manifest: CheckerManifest) -> GroupContextInput | None:
    from agent.execution.secure_files import read_regular

    if manifest.group_context is None:
        if manifest.related_documents:
            raise CheckerProgramError("关联文件清单与上下文不一致")
        return None
    context = GroupContextInput.model_validate(manifest.group_context)
    dumped = context.model_dump(mode="json")
    ids = set(_context_items(context))
    if ids != set(manifest.related_documents):
        raise CheckerProgramError("关联文件清单与上下文不一致")
    root = inputs.resolve()
    for audit_id, name in manifest.related_documents.items():
        raw = read_regular(inputs, (name,), MAX_DOCUMENT_BYTES)
        metadata = (_context_items(context)[audit_id].file_metadata or {})
        expected = str(metadata.get("sha256") or "")
        path = (inputs / name).resolve()
        if path.parent != root or not expected or hashlib.sha256(raw).hexdigest() != expected:
            raise CheckerProgramError("关联文件与冻结摘要不一致")
        guest_url = str(path)
        if audit_id in dumped["by_audit_id"]:
            dumped["by_audit_id"][audit_id]["file_url"] = guest_url
        for rows in dumped["by_doc_type"].values():
            for row in rows:
                if row.get("audit_id") == audit_id:
                    row["file_url"] = guest_url
    return GroupContextInput.model_validate(dumped)


def write_manifest(inputs: Path, manifest: CheckerManifest, file_bytes: bytes, *, related_bytes: dict[str, bytes] | None = None) -> None:
    staged = _staged_manifest(manifest, file_bytes, related_bytes or {})
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / staged.document).write_bytes(file_bytes)
    supplied = related_bytes or {}
    for audit_id, name in staged.related_documents.items():
        if name == staged.document:
            continue
        (inputs / name).write_bytes(supplied[audit_id])
    (inputs / "manifest.json").write_text(staged.model_dump_json())


def load_manifest(inputs: Path) -> tuple[CheckerManifest, AuditInput, RuleInput, GroupContextInput | None, bytes]:
    from agent.execution.secure_files import read_json, read_regular

    payload = read_json(inputs, ("manifest.json",), 1024 * 1024)
    try:
        manifest = CheckerManifest.model_validate(payload)
    except ValidationError as exc:
        raise CheckerProgramError("checker manifest is invalid") from exc
    raw = read_regular(inputs, (manifest.document,), MAX_DOCUMENT_BYTES)
    if hashlib.sha256(raw).hexdigest() != manifest.file_sha256:
        raise CheckerProgramError("文件版本与冻结字节不一致")
    audit_file = AuditInput(
        audit_id=manifest.audit_id,
        file_url=str(inputs / manifest.document),
        file_metadata={"file_version_id": manifest.file_version_id, "sha256": manifest.file_sha256, "role": manifest.role},
    )
    rule = RuleInput.model_validate(manifest.rule)
    if manifest.appendix and rule.appendix != manifest.appendix:
        if rule.appendix:
            raise CheckerProgramError("附录与规则不一致")
        rule = rule.model_copy(update={"appendix": manifest.appendix})
    group_context = _bound_group_context(inputs, manifest)
    return manifest, audit_file, rule, group_context, raw


def run_adapt_manifest(inputs: Path, work: Path, runner: ProgramRunner, deadline: float) -> CheckerArtifact:
    manifest, audit_file, rule, group_context, _raw = load_manifest(inputs)
    if manifest.operation != "adapt-checker" or manifest.spec.operation != "adapt-checker":
        raise CheckerProgramError("adapt manifest operation mismatch")
    digest = input_digest(
        audit_file,
        rule,
        file_sha256=manifest.file_sha256,
        role=manifest.role,
        appendix=manifest.appendix,
        group_context=group_context,
    )
    if digest != manifest.spec.input_digest:
        raise CheckerProgramError("adapt manifest digest mismatch")
    return develop_checker(
        audit_file=audit_file,
        rule=rule,
        group_context=group_context,
        digest=digest,
        executor_revision=manifest.spec.executor_revision,
        runner=runner,
        work=work,
        deadline=deadline,
        prior_files=manifest.prior_files,
        appendix=manifest.appendix,
    )


def run_execute_manifest(inputs: Path, work: Path) -> list[AuditResultItem]:
    manifest, audit_file, rule, group_context, _raw = load_manifest(inputs)
    if manifest.operation != "execute-checker" or manifest.artifact is None:
        raise CheckerProgramError("execute manifest has no fixed program")
    digest = input_digest(
        audit_file,
        rule,
        file_sha256=manifest.file_sha256,
        role=manifest.role,
        appendix=manifest.appendix,
        group_context=group_context,
    )
    if digest != manifest.spec.input_digest or manifest.artifact.input_digest != digest:
        raise CheckerProgramError("固定程序与本次输入不一致")
    return execute_fixed_checker(
        manifest.artifact,
        audit_file=audit_file,
        rule=rule,
        group_context=group_context,
        work=work,
        digest=digest,
        executor_revision=manifest.spec.executor_revision,
    )
