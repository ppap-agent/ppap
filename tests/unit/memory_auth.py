from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ppap_core.drafts import DRAFT_OPEN, IDENT_PENDING, ROLES
from ppap_core.files import (
    FAILED,
    MAX_UPLOAD_BYTES,
    READY,
    STAGING,
    FileVersion,
    ObjectExists,
    ObjectMissing,
    original_filename,
    sha256_bytes,
    version_object_key,
)
from ppap_core.fixtures import CATALOG_PROJECT, SUBJECTS
from ppap_core.identity import Principal, Resource, identity_key, new_token, token_hash
from ppap_core.rules import catalog_snapshots, snapshot_hash
from ppap_core.settings import Settings


class MemoryObjects:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key: str) -> bytes:
        if key not in self.data:
            raise ObjectMissing()
        return self.data[key]

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


class MemoryAuthStore:
    def __init__(self, settings: Settings | None = None, objects=None) -> None:
        self.settings = settings
        self.objects = objects or MemoryObjects()
        self.states: dict[str, tuple[str, datetime]] = {}
        self.tickets: dict[str, str] = {}
        self.sessions: dict[str, Principal] = {}
        self.revoked: set[str] = set()
        self.identities: dict[tuple[str, str, str, str], Principal] = {}
        self.resources: dict[str, Resource] = {}
        self.grant_table: dict[tuple[str, str], set[str]] = {}
        self.files: dict[str, dict[str, str]] = {}
        self.versions: dict[tuple[str, str], FileVersion] = {}
        self.jobs: dict[str, dict] = {}
        self.rule_versions: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.draft_items: dict[str, list[dict]] = {}

    def save_state(self, raw_state: str, return_to: str, ttl_seconds: int = 600) -> None:
        self.states[token_hash(raw_state)] = (return_to, datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds))

    def consume_state(self, raw_state: str) -> str | None:
        key = token_hash(raw_state)
        row = self.states.pop(key, None)
        if not row or row[1] <= datetime.now(timezone.utc):
            return None
        return row[0]

    def save_app_ticket(self, app_id: str, ticket: str) -> None:
        self.tickets[app_id] = ticket

    def has_app_ticket(self, app_id: str) -> bool:
        return app_id in self.tickets

    def upsert_feishu_identity(self, tenant_key: str, subject: str, display_name: str = "") -> Principal:
        key = identity_key("feishu", self.settings.feishu_app_id, tenant_key, subject)
        existing = self.identities.get(key)
        if existing:
            return existing
        principal = Principal(new_token(), new_token(), *key, "member", display_name)
        self.identities[key] = principal
        return principal

    def create_session(self, principal: Principal, ttl_seconds: int = 43200) -> str:
        raw = new_token()
        self.sessions[token_hash(raw)] = principal
        return raw

    def principal_from_token(self, raw: str) -> Principal | None:
        digest = token_hash(raw)
        if digest in self.revoked:
            return None
        return self.sessions.get(digest)

    def revoke_session(self, raw: str) -> None:
        self.revoked.add(token_hash(raw))

    def resource(self, resource_id: str) -> Resource | None:
        return self.resources.get(resource_id)

    def grants(self, resource_id: str, user_id: str) -> set[str]:
        return set(self.grant_table.get((resource_id, user_id), set()))

    def seed_isolated_dev(self) -> None:
        from ppap_core.fixtures import RESOURCE_A, RESOURCE_B, TENANT_A, TENANT_B, USER_ALICE, USER_BOB, USER_CAROL

        self.resources[RESOURCE_A] = Resource(RESOURCE_A, TENANT_A, "fixture")
        self.resources[RESOURCE_B] = Resource(RESOURCE_B, TENANT_B, "fixture")
        self.grant_table[(RESOURCE_A, USER_ALICE)] = {"read", "review"}
        self.grant_table[(RESOURCE_A, USER_BOB)] = {"read"}
        self.grant_table[(RESOURCE_B, USER_CAROL)] = {"read", "review"}
        self.seed_rule_catalog(TENANT_A, CATALOG_PROJECT)
        self.seed_rule_catalog(TENANT_B, CATALOG_PROJECT)

    def principal_for_fixture(self, name: str) -> Principal:
        spec = SUBJECTS[name]
        return Principal(
            spec["user_id"], spec["tenant_id"], "feishu", "isolated-dev-feishu-app",
            spec["tenant_key"], spec["subject"], "member", name,
        )

    def create_staging(
        self,
        principal: Principal,
        filename: str,
        content_type: str,
        declared_size: int,
        file_id: str | None = None,
    ) -> FileVersion:
        name = original_filename(filename)
        declared = int(declared_size)
        if declared < 0 or declared > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        ctype = (content_type or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
        if file_id:
            resource = self.resource(file_id)
            if resource is None or resource.kind != "file" or resource.tenant_id != principal.tenant_id:
                raise KeyError("missing")
            fid = file_id
        else:
            fid = str(uuid.uuid4())
            self.resources[fid] = Resource(fid, principal.tenant_id, "file")
            self.grant_table.setdefault((fid, principal.user_id), set()).add("read")
            self.files[fid] = {"tenant_id": principal.tenant_id, "created_by": principal.user_id, "original_filename": name}
        version_id = str(uuid.uuid4())
        key = version_object_key(principal.tenant_id, fid, version_id)
        version = FileVersion(version_id, fid, principal.tenant_id, name, ctype, declared, key, STAGING)
        self.versions[(fid, version_id)] = version
        return version

    def get_version(self, file_id: str, version_id: str) -> FileVersion | None:
        return self.versions.get((file_id, version_id))

    def put_blob(self, version: FileVersion, body: bytes) -> FileVersion:
        if version.status != STAGING:
            raise ObjectExists()
        if len(body) > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        self.objects.put_exclusive(version.object_key, body, version.content_type)
        updated = FileVersion(
            version.id, version.file_id, version.tenant_id, version.original_filename,
            version.content_type, len(body), version.object_key, STAGING, version.sha256,
        )
        self.versions[(version.file_id, version.id)] = updated
        return updated

    def finalize_version(self, version: FileVersion, expected_sha256: str | None = None) -> FileVersion:
        current = self.versions[(version.file_id, version.id)]
        if current.status == READY:
            if expected_sha256 and current.sha256 and expected_sha256 != current.sha256:
                raise ValueError("sha256_conflict")
            return current
        if current.status != STAGING:
            raise ValueError("not_staging")
        body = self.objects.get(current.object_key)
        digest = sha256_bytes(body)
        if expected_sha256 and expected_sha256 != digest:
            failed = FileVersion(
                current.id, current.file_id, current.tenant_id, current.original_filename,
                current.content_type, len(body), current.object_key, FAILED, digest,
            )
            self.versions[(current.file_id, current.id)] = failed
            raise ValueError("sha256_mismatch")
        ready = FileVersion(
            current.id, current.file_id, current.tenant_id, current.original_filename,
            current.content_type, len(body), current.object_key, READY, digest,
        )
        self.versions[(current.file_id, current.id)] = ready
        return ready

    def version_bytes(self, version: FileVersion) -> bytes:
        current = self.versions[(version.file_id, version.id)]
        if current.status != READY or not current.sha256:
            raise ValueError("not_ready")
        body = self.objects.get(current.object_key)
        if sha256_bytes(body) != current.sha256:
            raise ValueError("object_mutated")
        return body

    def reap_staging(self, max_age_seconds: int) -> int:
        if max_age_seconds < 0:
            raise ValueError("invalid_age")
        count = 0
        for key, version in list(self.versions.items()):
            if version.status != STAGING:
                continue
            if max_age_seconds > 0:
                continue
            self.versions[key] = FileVersion(
                version.id, version.file_id, version.tenant_id, version.original_filename,
                version.content_type, version.byte_size, version.object_key, FAILED, version.sha256,
            )
            self.objects.delete(version.object_key)
            count += 1
        return count

    def enqueue_audit(
        self,
        tenant_id: str,
        version: FileVersion,
        *,
        document_type: str,
        rule_profile: str,
        product_rule_version_id: str,
        engine_rule_id: str,
        rules: list | None = None,
    ) -> dict:
        if version.status != READY or not version.sha256:
            raise ValueError("not_ready")
        if version.tenant_id != tenant_id:
            raise ValueError("access_denied")
        job_id = str(uuid.uuid4())
        payload = {
            "file_id": version.file_id,
            "file_version_id": version.id,
            "sha256": version.sha256,
            "original_filename": version.original_filename,
            "document_type": document_type,
            "rule_profile": rule_profile,
            "product_rule_version_id": product_rule_version_id,
            "engine_rule_id": engine_rule_id,
            "rules": rules or [],
        }
        job = {
            "id": job_id,
            "tenant_id": tenant_id,
            "kind": "audit.execute",
            "payload": payload,
            "status": "queued",
            "attempt": 0,
            "result": {},
        }
        self.jobs[job_id] = job
        return {"id": job_id, "status": "queued", "payload": payload}

    def get_job(self, tenant_id: str, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.get("tenant_id") and job["tenant_id"] != tenant_id:
            raise ValueError("access_denied")
        payload = job["payload"]
        version = self.versions.get((payload["file_id"], payload["file_version_id"]))
        if version is None or version.tenant_id != tenant_id:
            raise ValueError("access_denied")
        return job

    def seed_rule_catalog(self, tenant_id: str, project_id: str) -> list[dict]:
        return [self.publish_rule_version(tenant_id, snapshot) for snapshot in catalog_snapshots(project_id)]

    def publish_rule_version(self, tenant_id: str, snapshot: dict) -> dict:
        content = snapshot["content"]
        digest = snapshot.get("content_sha256") or snapshot_hash(content)
        for existing in self.rule_versions.values():
            if (
                existing["tenant_id"] == tenant_id
                and existing["project_id"] == snapshot["project_id"]
                and existing["engine_rule_id"] == snapshot["engine_rule_id"]
                and existing["content_sha256"] == digest
            ):
                return existing
        checks = content.get("checks") if isinstance(content.get("checks"), list) else []
        record = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "project_id": snapshot["project_id"],
            "engine_rule_id": snapshot["engine_rule_id"],
            "document_type": snapshot["document_type"],
            "label": snapshot.get("label") or snapshot["engine_rule_id"],
            "content": content,
            "content_sha256": digest,
            "check_count": len(checks),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.rule_versions[record["id"]] = record
        return record

    def list_rule_versions(self, tenant_id: str, project_id: str | None = None) -> list[dict]:
        items = [item for item in self.rule_versions.values() if item["tenant_id"] == tenant_id]
        if project_id:
            items = [item for item in items if item["project_id"] == project_id]
        return sorted(items, key=lambda item: (item["project_id"], item["document_type"], item["created_at"]))

    def get_rule_version(self, tenant_id: str, version_id: str) -> dict:
        record = self.rule_versions.get(version_id)
        if record is None:
            raise KeyError("invalid_rule_version")
        if record["tenant_id"] != tenant_id:
            raise ValueError("access_denied")
        return record

    def _project_exists(self, tenant_id: str, project_id: str) -> bool:
        return any(item["tenant_id"] == tenant_id and item["project_id"] == project_id for item in self.rule_versions.values())

    def _require_known_project(self, tenant_id: str, project_id: str | None) -> str | None:
        value = (project_id or "").strip() or None
        if value and not self._project_exists(tenant_id, value):
            raise ValueError("unknown_project")
        return value

    def create_draft(self, principal: Principal, *, label: str = "", project_id: str | None = None) -> dict:
        known_project = self._require_known_project(principal.tenant_id, project_id)
        draft_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self.resources[draft_id] = Resource(draft_id, principal.tenant_id, "draft")
        self.grant_table.setdefault((draft_id, principal.user_id), set()).add("read")
        self.drafts[draft_id] = {
            "id": draft_id,
            "tenant_id": principal.tenant_id,
            "status": DRAFT_OPEN,
            "project_id": known_project,
            "label": (label or "")[:255],
            "accepted": False,
            "created_at": now,
            "updated_at": now,
        }
        self.draft_items[draft_id] = []
        return self.get_draft(principal, draft_id)

    def get_draft(self, principal: Principal, draft_id: str) -> dict:
        resource = self.resource(draft_id)
        grants = self.grants(draft_id, principal.user_id) if resource else set()
        if resource is None or resource.kind != "draft":
            raise KeyError("missing")
        if resource.tenant_id != principal.tenant_id or not (grants & {"read", "review"}):
            raise ValueError("access_denied")
        draft = self.drafts[draft_id]
        items = []
        for item in self.draft_items.get(draft_id, []):
            version = self.versions[(item["file_id"], item["file_version_id"])]
            items.append(
                {
                    **item,
                    "sha256": version.sha256,
                    "original_filename": version.original_filename,
                }
            )
        return {**draft, "items": items}

    def replace_draft(self, principal: Principal, draft_id: str, patch: dict) -> dict:
        current = self.get_draft(principal, draft_id)
        label = current["label"] if "label" not in patch else str(patch.get("label") or "")[:255]
        if "project_id" in patch:
            project_id = self._require_known_project(principal.tenant_id, patch.get("project_id"))
        else:
            project_id = current["project_id"]
        if "items" in patch:
            if not isinstance(patch["items"], list):
                raise ValueError("invalid_items")
            prepared = self._prepare_items(principal, current, project_id, patch["items"])
            self.draft_items[draft_id] = prepared
        elif project_id != current["project_id"]:
            for item in current["items"]:
                if item["product_rule_version_id"]:
                    bound = self.get_rule_version(principal.tenant_id, item["product_rule_version_id"])
                    if bound["project_id"] != project_id:
                        raise ValueError("project_conflict")
        self.drafts[draft_id] = {
            **self.drafts[draft_id],
            "label": label,
            "project_id": project_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.get_draft(principal, draft_id)

    def _prepare_items(self, principal: Principal, current: dict, project_id: str | None, incoming: list) -> list[dict]:
        previous = {(item["file_version_id"], item["role"]): item for item in current["items"]}
        seen: set[str] = set()
        prepared: list[dict] = []
        for raw in incoming:
            if not isinstance(raw, dict):
                raise ValueError("invalid_items")
            role = str(raw.get("role") or "")
            if role not in ROLES:
                raise ValueError("invalid_role")
            version = self.get_version(str(raw.get("file_id") or ""), str(raw.get("file_version_id") or ""))
            if version is None:
                raise KeyError("missing_file")
            if version.tenant_id != principal.tenant_id:
                raise ValueError("access_denied")
            resource = self.resource(version.file_id)
            grants = self.grants(version.file_id, principal.user_id) if resource else set()
            if resource is None or resource.kind != "file" or not (grants & {"read", "review"}):
                raise ValueError("access_denied")
            if version.status != READY or not version.sha256:
                raise ValueError("not_ready")
            if version.id in seen:
                raise ValueError("duplicate_version")
            seen.add(version.id)
            kept = previous.get((version.id, role))
            if "product_rule_version_id" in raw:
                bound_id = raw.get("product_rule_version_id") or None
            else:
                bound_id = kept["product_rule_version_id"] if kept else None
            engine_rule_id = None
            bound_sha = None
            bound_count = None
            if bound_id:
                rule = self.get_rule_version(principal.tenant_id, str(bound_id))
                if project_id and rule["project_id"] != project_id:
                    raise ValueError("project_conflict")
                engine_rule_id = rule["engine_rule_id"]
                bound_sha = rule["content_sha256"]
                bound_count = rule["check_count"]
                bound_id = rule["id"]
            if kept:
                item = {
                    "id": kept["id"],
                    "identification_status": kept["identification_status"],
                    "identification_job_id": kept["identification_job_id"],
                    "matched_document_type": kept["matched_document_type"],
                    "match_evidence": kept["match_evidence"],
                }
            else:
                item = {
                    "id": str(uuid.uuid4()),
                    "identification_status": IDENT_PENDING,
                    "identification_job_id": None,
                    "matched_document_type": None,
                    "match_evidence": {},
                }
            prepared.append(
                {
                    **item,
                    "file_id": version.file_id,
                    "file_version_id": version.id,
                    "role": role,
                    "product_rule_version_id": bound_id,
                    "engine_rule_id": engine_rule_id,
                    "bound_content_sha256": bound_sha,
                    "bound_check_count": bound_count,
                }
            )
        return prepared

    def enqueue_identify(self, principal: Principal, draft_id: str) -> dict:
        draft = self.get_draft(principal, draft_id)
        if draft["project_id"] and not self._project_exists(principal.tenant_id, draft["project_id"]):
            raise ValueError("unknown_project")
        jobs = []
        updated = []
        for item in draft["items"]:
            job_id = str(uuid.uuid4())
            payload = {
                "draft_id": draft["id"],
                "item_id": item["id"],
                "file_id": item["file_id"],
                "file_version_id": item["file_version_id"],
                "sha256": item["sha256"],
                "original_filename": item["original_filename"],
                "role": item["role"],
                "project_id": draft["project_id"],
                "preserve_product_rule_version_id": item["product_rule_version_id"],
            }
            self.jobs[job_id] = {
                "id": job_id,
                "tenant_id": principal.tenant_id,
                "kind": "draft.identify",
                "payload": payload,
                "status": "queued",
                "attempt": 0,
                "result": {},
            }
            updated.append({**item, "identification_job_id": job_id, "identification_status": IDENT_PENDING})
            jobs.append({"item_id": item["id"], "id": job_id, "status": "queued", "kind": "draft.identify"})
        self.draft_items[draft_id] = [
            {k: v for k, v in item.items() if k not in {"sha256", "original_filename"}} for item in updated
        ]
        return {"draft_id": draft["id"], "jobs": jobs, "accepted": False}

    def apply_identification(
        self,
        tenant_id: str,
        item_id: str,
        job_id: str,
        *,
        status: str,
        matched_document_type: str | None,
        evidence: dict,
    ) -> None:
        for draft_id, items in self.draft_items.items():
            draft = self.drafts.get(draft_id)
            if not draft or draft["tenant_id"] != tenant_id:
                continue
            for item in items:
                if item["id"] != item_id:
                    continue
                item["identification_status"] = status
                item["matched_document_type"] = matched_document_type
                item["match_evidence"] = evidence
                item["identification_job_id"] = job_id
                return
