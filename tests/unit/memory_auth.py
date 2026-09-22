from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from ppap_core.audits import (
    ANALYSIS_DEEP,
    ANALYSIS_STANDARD,
    ClaimedJob,
    DRAFT_ACCEPTED,
    DRAFT_OPEN,
    EXEC_COMPLETED,
    EXEC_FAILED,
    EXEC_QUEUED,
    EXEC_RUNNING,
    RoundInput,
    RoundWork,
    job_payload,
    normalize_analysis_mode,
    normalize_idempotency_key,
    normalize_review,
    normalize_role,
    request_fingerprint,
    require_targets,
)
from ppap_core.feishu import UserTokens
from ppap_core.files import (
    FAILED,
    MAX_UPLOAD_BYTES,
    READY,
    STAGING,
    FileVersion,
    ObjectExists,
    ObjectMissing,
    original_filename,
    safe_content_type,
    sha256_bytes,
    version_object_key,
)
from ppap_core.fixtures import SUBJECTS
from ppap_core.identity import Principal, Resource, Session, can_read, identity_key, new_token, token_hash
from ppap_core.settings import Settings
from ppap_core.rules import normalize_rule_body, rule_title, rule_parse_request, rule_parse_view
from ppap_core.writeback_targets import normalize_external_ref, public_target


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        self.sessions: dict[str, Session] = {}
        self.revoked: set[str] = set()
        self.identities: dict[tuple[str, str, str, str], Principal] = {}
        self.resources: dict[str, Resource] = {}
        self.grant_table: dict[tuple[str, str], set[str]] = {}
        self.files: dict[str, dict[str, str]] = {}
        self.versions: dict[tuple[str, str], FileVersion] = {}
        self.versions_by_id: dict[str, FileVersion] = {}
        self.rule_versions: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.draft_items: dict[str, list[dict[str, str]]] = {}
        self.rounds: dict[str, dict] = {}
        self.round_inputs: dict[str, list[dict[str, str]]] = {}
        self.findings: dict[str, dict[str, dict]] = {}
        self.reviews: dict[tuple[str, str], dict] = {}
        self.jobs: dict[str, dict] = {}
        self.attempts: dict[tuple[str, int], dict] = {}
        self.idempotency: dict[tuple[str, str], str] = {}
        self.usage_events: list[dict] = []
        self.file_sources: dict[str, dict[str, str]] = {}
        self.draft_writebacks: dict[str, dict[str, str]] = {}
        self.round_writebacks: dict[str, dict[str, str]] = {}
        self.trial_baselines: dict[tuple[str, str], int] = {}

    def save_state(self, raw_state: str, return_to: str, ttl_seconds: int = 600) -> None:
        self.states[token_hash(raw_state)] = (return_to, _now() + timedelta(seconds=ttl_seconds))

    def consume_state(self, raw_state: str) -> str | None:
        key = token_hash(raw_state)
        row = self.states.pop(key, None)
        if not row or row[1] <= _now():
            return None
        return row[0]

    def save_app_ticket(self, app_id: str, ticket: str) -> None:
        self.tickets[app_id] = ticket

    def has_app_ticket(self, app_id: str) -> bool:
        return app_id in self.tickets

    def app_ticket(self, app_id: str) -> str | None:
        ticket = self.tickets.get(app_id)
        return str(ticket) if ticket else None

    def upsert_feishu_identity(self, tenant_key: str, subject: str, display_name: str = "") -> Principal:
        key = identity_key("feishu", self.settings.feishu_app_id, tenant_key, subject)
        existing = self.identities.get(key)
        if existing:
            return existing
        principal = Principal(new_token(), new_token(), *key, "member", display_name)
        self.identities[key] = principal
        return principal

    def create_session(self, principal: Principal, tokens: UserTokens | None = None, ttl_seconds: int = 43200) -> str:
        raw = new_token()
        now = _now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        access_token = refresh_token = ""
        access_expires_at = refresh_expires_at = None
        if tokens is not None:
            access_token = tokens.access_token
            refresh_token = tokens.refresh_token
            access_expires_at = tokens.access_expires_at
            refresh_expires_at = tokens.refresh_expires_at
            bound = tokens.refresh_expires_at if tokens.refresh_token else tokens.access_expires_at
            if bound < expires_at:
                expires_at = bound
        self.sessions[token_hash(raw)] = Session(
            principal, expires_at, access_token, refresh_token, access_expires_at, refresh_expires_at
        )
        return raw

    def load_session(self, raw: str) -> Session | None:
        digest = token_hash(raw)
        if digest in self.revoked:
            return None
        session = self.sessions.get(digest)
        if not session or session.expires_at <= _now():
            return None
        return session

    def principal_from_token(self, raw: str) -> Principal | None:
        session = self.load_session(raw)
        return session.principal if session else None

    def update_provider_auth(self, raw: str, tokens: UserTokens) -> None:
        digest = token_hash(raw)
        session = self.sessions.get(digest)
        if not session or digest in self.revoked:
            return
        self.sessions[digest] = Session(
            session.principal,
            session.expires_at,
            tokens.access_token,
            tokens.refresh_token,
            tokens.access_expires_at,
            tokens.refresh_expires_at,
        )

    def revoke_session(self, raw: str) -> None:
        self.revoked.add(token_hash(raw))

    def resource(self, resource_id: str) -> Resource | None:
        return self.resources.get(resource_id)

    def grants(self, resource_id: str, user_id: str) -> set[str]:
        return set(self.grant_table.get((resource_id, user_id), set()))

    def seed_isolated_dev(self) -> None:
        from ppap_core.fixtures import (
            PRESENCE_RULE_BODY,
            RESOURCE_A,
            RESOURCE_B,
            RULE_A,
            RULE_B,
            TENANT_A,
            TENANT_B,
            USER_ALICE,
            USER_BOB,
            USER_CAROL,
        )

        self.resources[RESOURCE_A] = Resource(RESOURCE_A, TENANT_A, "fixture")
        self.resources[RESOURCE_B] = Resource(RESOURCE_B, TENANT_B, "fixture")
        self.grant_table[(RESOURCE_A, USER_ALICE)] = {"read", "review"}
        self.grant_table[(RESOURCE_A, USER_BOB)] = {"read"}
        self.grant_table[(RESOURCE_B, USER_CAROL)] = {"read", "review"}
        self.rule_versions[RULE_A] = {"id": RULE_A, "tenant_id": TENANT_A, "title": "资料就绪", "body": dict(PRESENCE_RULE_BODY)}
        self.rule_versions[RULE_B] = {"id": RULE_B, "tenant_id": TENANT_B, "title": "资料就绪", "body": dict(PRESENCE_RULE_BODY)}

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
        ctype = safe_content_type(content_type)
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
        self.versions_by_id[version_id] = version
        return version

    def get_version(self, file_id: str, version_id: str) -> FileVersion | None:
        return self.versions.get((file_id, version_id))

    def put_blob(self, version: FileVersion, body: bytes) -> FileVersion:
        current = self.versions.get((version.file_id, version.id))
        if current is None or current.status != STAGING:
            raise ObjectExists()
        if len(body) > MAX_UPLOAD_BYTES:
            raise ValueError("invalid_size")
        self.objects.put_exclusive(current.object_key, body, current.content_type)
        claimed = self.versions.get((version.file_id, version.id))
        if claimed is None or claimed.status != STAGING:
            if claimed is None or claimed.status != READY:
                self.objects.delete(current.object_key)
            raise ObjectExists()
        updated = FileVersion(
            claimed.id, claimed.file_id, claimed.tenant_id, claimed.original_filename,
            claimed.content_type, len(body), claimed.object_key, STAGING, claimed.sha256,
        )
        self.versions[(claimed.file_id, claimed.id)] = updated
        self.versions_by_id[claimed.id] = updated
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
            self.versions_by_id[current.id] = failed
            raise ValueError("sha256_mismatch")
        ready = FileVersion(
            current.id, current.file_id, current.tenant_id, current.original_filename,
            current.content_type, len(body), current.object_key, READY, digest,
        )
        self.versions[(current.file_id, current.id)] = ready
        self.versions_by_id[current.id] = ready
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
            failed = FileVersion(
                version.id, version.file_id, version.tenant_id, version.original_filename,
                version.content_type, version.byte_size, version.object_key, FAILED, version.sha256,
            )
            self.versions[key] = failed
            self.versions_by_id[version.id] = failed
            self.objects.delete(version.object_key)
            count += 1
        return count

    def accept_rule_parse(self, principal, request_id, source_file_id, source_file_version_id, document_type=""):
        return self._accept_file_job(principal, request_id, source_file_id, source_file_version_id, "rule_parse", document_type)

    def accept_file_analysis(self, principal, request_id, source_file_id, source_file_version_id):
        return self._accept_file_job(principal, request_id, source_file_id, source_file_version_id, "file_analysis")

    def _accept_file_job(self, principal, request_id, source_file_id, source_file_version_id, kind, document_type=""):
        job_id, payload = rule_parse_request(request_id, source_file_id, source_file_version_id, document_type)
        prepared = self._prepared_items(principal, [{"file_version_id": payload["source_file_version_id"], "role": "reference"}])
        if prepared[0]["file_id"] != payload["source_file_id"]:
            raise KeyError("missing")
        previous = self.jobs.get(job_id)
        if previous:
            if previous["tenant_id"] != principal.tenant_id or previous["kind"] != kind:
                raise KeyError("missing")
            if previous["payload"] != payload:
                raise ValueError("rule_parse_conflict")
        else:
            self.jobs[job_id] = {"id": job_id, "tenant_id": principal.tenant_id, "kind": kind, "payload": payload, "status": "queued", "attempt": 0, "created": len(self.jobs), "lease_until": None}
        return self._get_file_job(principal, job_id, kind)

    def get_rule_parse(self, principal, job_id):
        return self._get_file_job(principal, job_id, "rule_parse")

    def get_file_analysis(self, principal, job_id):
        return self._get_file_job(principal, job_id, "file_analysis")

    def latest_file_analysis(self, principal, file_id, version_id):
        prepared = self._prepared_items(principal, [{"file_version_id": version_id, "role": "reference"}])
        if prepared[0]["file_id"] != file_id:
            raise KeyError("missing")
        jobs = [job for job in self.jobs.values() if job["tenant_id"] == principal.tenant_id and job["kind"] == "file_analysis" and job["payload"]["source_file_version_id"] == version_id]
        return self.get_file_analysis(principal, max(jobs, key=lambda job: job["created"])["id"]) if jobs else None

    def _get_file_job(self, principal, job_id, kind):
        job = self.jobs.get(job_id)
        if not job or job["tenant_id"] != principal.tenant_id or job["kind"] != kind:
            raise KeyError("missing")
        self._prepared_items(principal, [{"file_version_id": job["payload"]["source_file_version_id"], "role": "reference"}])
        return rule_parse_view(job_id, job["payload"], job["status"], job.get("result"), bool(job.get("lease_until") and job["lease_until"] < _now()))

    def list_rules(self, tenant_id: str) -> list[dict[str, str]]:
        rows = [row for row in self.rule_versions.values() if row["tenant_id"] == tenant_id]
        rows.sort(key=lambda row: (row.get("created_at", ""), row["id"]), reverse=True)
        return [{"id": row["id"], "title": row["title"], "created_at": row.get("created_at"), "predecessor_id": row.get("predecessor_id"), "kind": str(row["body"].get("kind") or "engine"),
                 **{field: row["body"].get(field) for field in ("type", "rule_profile", "customers", "models", "parts")}} for row in rows]

    def get_rule(self, tenant_id: str, rule_version_id: str) -> dict | None:
        import copy

        row = self.rule_versions.get(rule_version_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return copy.deepcopy({key: value for key, value in row.items() if key != "tenant_id"})

    def publish_rule(self, principal, title, body, source_file_version_id, predecessor_id=None):
        title, body = rule_title(title), normalize_rule_body(body)
        items = self._prepared_items(principal, [{"file_version_id": source_file_version_id, "role": "reference"}])
        if predecessor_id and self.get_rule(principal.tenant_id, predecessor_id) is None:
            raise KeyError("missing")
        rule_id = str(uuid.uuid4())
        self.rule_versions[rule_id] = {
            "id": rule_id, "tenant_id": principal.tenant_id, "title": title, "body": body,
            "source_file_version_id": source_file_version_id, "source_file_id": items[0]["file_id"],
            "predecessor_id": predecessor_id, "created_by": principal.user_id, "created_at": _now().isoformat(),
        }
        return self.get_rule(principal.tenant_id, rule_id)

    def create_draft(self, principal: Principal, predecessor_id: str | None = None) -> dict:
        if predecessor_id:
            resource = self.resources.get(predecessor_id)
            if resource is None or resource.kind != 'audit_round' or not can_read(principal, resource, self.grants(predecessor_id, principal.user_id)):
                raise KeyError('missing')
        mode = ANALYSIS_STANDARD
        if predecessor_id:
            pred = self.rounds.get(predecessor_id) or {}
            mode = self._offered_analysis_mode(pred.get("analysis_mode"), allow_reject=False)
        draft_id = str(uuid.uuid4())
        self.resources[draft_id] = Resource(draft_id, principal.tenant_id, "audit_draft")
        self.grant_table.setdefault((draft_id, principal.user_id), set()).update({"read", "review"})
        self.drafts[draft_id] = {
            "id": draft_id,
            "tenant_id": principal.tenant_id,
            "created_by": principal.user_id,
            "status": DRAFT_OPEN,
            "updated_at": _now().isoformat(),
            "predecessor_id": predecessor_id,
            "analysis_mode": mode,
        }
        self.draft_items[draft_id] = []
        if predecessor_id and predecessor_id in self.round_writebacks:
            self.draft_writebacks[draft_id] = dict(self.round_writebacks[predecessor_id])
        return self.get_draft(draft_id)

    def list_drafts(self, principal: Principal, limit: int = 20, offset: int = 0) -> list[dict]:
        rows = [self._draft_public(draft['id']) for draft in self.drafts.values()
                if draft['status'] == DRAFT_OPEN and self.draft_items.get(draft['id'])
                and can_read(principal, self.resources[draft['id']], self.grants(draft['id'], principal.user_id))]
        rows.sort(key=lambda row: (row['updated_at'], row['id']), reverse=True)
        return rows[offset:offset + limit]

    def get_draft(self, draft_id: str) -> dict | None:
        draft = self.drafts.get(draft_id)
        if not draft:
            return None
        return self._draft_public(draft_id)

    def _offered_analysis_mode(self, value, allow_reject: bool = True) -> str:
        mode = normalize_analysis_mode(value)
        if mode != ANALYSIS_DEEP:
            return mode
        if self.settings and self.settings.deep_offered():
            return mode
        if allow_reject:
            raise ValueError("deep_not_available")
        return ANALYSIS_STANDARD

    def save_draft(
        self, principal: Principal, draft_id: str, items: list[dict] | None, analysis_mode: str | None = None,
        writeback_target=None,
    ) -> dict:
        draft = self.drafts.get(draft_id)
        if not draft or draft["tenant_id"] != principal.tenant_id:
            raise KeyError("missing")
        if draft["status"] != DRAFT_OPEN:
            raise ValueError("draft_already_accepted")
        if items is not None:
            self.draft_items[draft_id] = self._bound_audit_items(principal, items, require_rules=False)
            draft['updated_at'] = _now().isoformat()
        if analysis_mode is not None:
            draft["analysis_mode"] = self._offered_analysis_mode(analysis_mode)
            draft['updated_at'] = _now().isoformat()
        if writeback_target is not None:
            target = normalize_external_ref(writeback_target) if writeback_target else None
            if target:
                self.draft_writebacks[draft_id] = target
            else:
                self.draft_writebacks.pop(draft_id, None)
            draft['updated_at'] = _now().isoformat()
        return self._draft_public(draft_id)

    def accept_draft(self, principal: Principal, draft_id: str, idempotency_key: str, predecessor_id: str | None = None) -> dict:
        draft = self.drafts.get(draft_id)
        if draft and draft.get('predecessor_id'):
            if predecessor_id and predecessor_id != draft['predecessor_id']:
                raise ValueError('idempotency_conflict')
            predecessor_id = draft['predecessor_id']
        key = normalize_idempotency_key(idempotency_key)
        if predecessor_id:
            resource = self.resources.get(predecessor_id)
            if resource is None or resource.kind != 'audit_round' or not can_read(principal, resource, self.grants(predecessor_id, principal.user_id)):
                raise KeyError('missing')
        return self._accept(principal, key, source_draft_id=draft_id, predecessor_id=predecessor_id, items=None)

    def resubmit_round(
        self,
        principal: Principal,
        round_id: str,
        idempotency_key: str,
        items: list[dict] | None,
    ) -> dict:
        key = normalize_idempotency_key(idempotency_key)
        current = self.rounds.get(round_id)
        if not current or current["tenant_id"] != principal.tenant_id:
            raise KeyError("missing")
        if items is None:
            items = list(self.round_inputs.get(round_id, []))
        return self._accept(
            principal,
            key,
            source_draft_id=None,
            predecessor_id=round_id,
            items=items,
        )

    def get_round(self, round_id: str) -> dict | None:
        if round_id not in self.rounds:
            return None
        return self._round_public(round_id)

    def list_rounds(self, principal: Principal, limit: int = 50, offset: int = 0, q: str = "", execution: str = "", unreviewed: bool = False) -> list[dict]:
        try:
            cap = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            cap = 50
        items: list[dict] = []
        for round_id in reversed(list(self.rounds)):
            resource = self.resources.get(round_id)
            if resource is None or not can_read(principal, resource, self.grants(round_id, principal.user_id)):
                continue
            body = self._round_public(round_id)
            if execution and body["execution"] != execution:
                continue
            if unreviewed and not any(not finding.get("review") for finding in body.get("findings", [])):
                continue
            hay = " ".join(item["original_filename"] + " " + (item.get("rule") or {}).get("title", "") for item in body["inputs"])
            if q.lower() not in hay.lower():
                continue
            result_counts = {"PASS": 0, "NG": 0, "ERR": 0}
            for finding in body.get("findings") or []:
                if finding["status"] in result_counts:
                    result_counts[finding["status"]] += 1
            items.append(
                {
                    "id": body["id"],
                    "execution": body["execution"],
                    "predecessor_id": body.get("predecessor_id"),
                    "created_at": body.get("created_at"),
                    "analysis_mode": body.get("analysis_mode"),
                    "inputs": [
                        {
                            "role": item["role"],
                            "original_filename": item["original_filename"],
                            "file_version_id": item["file_version_id"],
                            "rule": item["rule"],
                        }
                        for item in body.get("inputs") or []
                    ],
                    "result_counts": result_counts,
                    "reviewed_count": sum(bool(f.get("review")) for f in body.get("findings", [])),
                }
            )
        return items[offset:offset + limit]

    def round_input_version(self, round_id: str, file_id: str, version_id: str) -> FileVersion | None:
        if round_id not in self.rounds:
            return None
        if not any(row["file_version_id"] == version_id for row in self.round_inputs.get(round_id, [])):
            return None
        version = self.versions.get((file_id, version_id))
        return version

    def round_bundle(self, round_id: str):
        from zipfile import ZIP_DEFLATED, ZipFile
        import tempfile

        from ppap_core.bundles import archive_member_names

        work = self.round_work(round_id)
        if work is None:
            return None
        names = archive_member_names([(item.file_version_id, item.filename) for item in work.inputs])
        versions = []
        total = 0
        for item in work.inputs:
            version = self.get_version(item.file_id, item.file_version_id)
            if version is None:
                raise ValueError("bundle_source_missing")
            total += int(version.byte_size or 0)
            versions.append(version)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("bundle_too_large")
        tmp = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        with ZipFile(tmp, "w", compression=ZIP_DEFLATED) as archive:
            for version, name in zip(versions, names):
                archive.writestr(name, self.version_bytes(version))
        tmp.seek(0)

        def chunks():
            try:
                while True:
                    block = tmp.read(65536)
                    if not block:
                        break
                    yield block
            finally:
                tmp.close()

        return f"ppap-round-{work.round_id[:8]}.zip", chunks()

    def save_review(
        self, principal: Principal, round_id: str, item_key: str, judgment: str, rationale: str, next_step: str, version: int
    ) -> dict:
        text, why, nxt, seen = normalize_review(judgment, rationale, next_step, version)
        key = str(item_key or "").strip()
        if round_id not in self.rounds or not key or key not in self.findings.get(round_id, {}):
            raise KeyError("missing")
        current = self.reviews.get((round_id, key))
        if seen == 0:
            if current is not None:
                raise ValueError("review_conflict")
            self.reviews[(round_id, key)] = {
                "judgment": text,
                "rationale": why,
                "next_step": nxt,
                "version": 1,
                "user_id": principal.user_id,
            }
        else:
            if current is None or current["version"] != seen:
                raise ValueError("review_conflict")
            current.update({"judgment": text, "rationale": why, "next_step": nxt, "version": seen + 1, "user_id": principal.user_id})
        return self._round_public(round_id)

    def round_work(self, round_id: str) -> RoundWork | None:
        row = self.rounds.get(round_id)
        if not row:
            return None
        inputs = []
        for item in self.round_inputs.get(round_id, []):
            version = self.versions_by_id.get(item["file_version_id"])
            if version is None:
                continue
            inputs.append(
                RoundInput(
                    item["role"],
                    version.file_id,
                    version.id,
                    version.original_filename,
                    version.status,
                    version.object_key,
                    version.content_type,
                    item.get("rule_version_id"),
                    item.get("rule_title"),
                    item.get("rule_body"),
                    version.sha256,
                    item.get("rule_execution"),
                )
            )
        return RoundWork(
            round_id,
            row["tenant_id"],
            inputs,
            row.get("result_attempt"),
            normalize_analysis_mode(row.get("analysis_mode")),
        )

    def claim_job(self, worker_ident: str) -> ClaimedJob | None:
        now = _now()
        eligible = [
            job
            for job in self.jobs.values()
            if job["status"] == "queued"
            or (
                job["status"] == "leased"
                and job["kind"] not in {"rule_parse", "file_analysis"}
                and job.get("lease_until") is not None
                and job["lease_until"] < now
            )
        ]
        eligible.sort(key=lambda job: job["created"])
        if not eligible:
            return None
        job = eligible[0]
        job["status"] = "leased"
        job["attempt"] += 1
        job["lease_owner"] = worker_ident
        job["lease_until"] = now + timedelta(seconds=30)
        payload = job["payload"]
        round_id = payload.get("round_id") if isinstance(payload, dict) else None
        if round_id and round_id in self.rounds and self.rounds[round_id]["execution"] in {EXEC_QUEUED, EXEC_RUNNING, EXEC_FAILED}:
            self.rounds[round_id]["execution"] = EXEC_RUNNING
        self.attempts[(job["id"], job["attempt"])] = {"worker_id": worker_ident, "diagnostic": {}}
        return ClaimedJob(job["id"], job["kind"], payload, job["attempt"], round_id, job.get("tenant_id"))

    def renew_job_lease(self, claimed, worker_ident):
        job = self.jobs.get(claimed.id)
        if not job or job["attempt"] != claimed.attempt or job.get("lease_owner") != worker_ident or job["status"] != "leased":
            return False
        job["lease_until"] = _now() + timedelta(seconds=30)
        return True

    def complete_job(self, claimed: ClaimedJob, worker_ident: str, result: dict, outcome: str = "succeeded") -> bool:
        job = self.jobs.get(claimed.id)
        if not job or job["attempt"] != claimed.attempt or job.get("lease_owner") != worker_ident or job["status"] != "leased":
            return False
        job["status"] = "succeeded" if outcome == "succeeded" else "failed"
        job["lease_until"] = None
        if claimed.kind in {"rule_parse", "file_analysis"}:
            job["result"] = result
        findings = result.get("findings") or []
        self.attempts[(claimed.id, claimed.attempt)] = {
            "worker_id": worker_ident,
            "diagnostic": {
                "note": result.get("note") or "round_audit",
                "finding_count": len(findings),
                **(
                    {
                        "executor": claimed.payload.get("executor"),
                        "executor_revision": claimed.payload.get("executor_revision"),
                    }
                    if isinstance(claimed.payload, dict) and claimed.payload.get("executor")
                    else {}
                ),
            },
            "outcome": outcome,
        }
        if not claimed.round_id:
            return True
        if claimed.round_id not in self.rounds:
            return False
        current = self.rounds[claimed.round_id]
        previous = current.get("result_attempt")
        if previous is not None and previous >= claimed.attempt:
            return False
        current["execution"] = EXEC_COMPLETED if outcome == "succeeded" else EXEC_FAILED
        current["result_attempt"] = claimed.attempt
        current["coverage"] = result.get("coverage") or {}
        bucket = self.findings.setdefault(claimed.round_id, {})
        for row in findings:
            bucket[row["item_key"]] = dict(row)
        return True

    def _prepared_items(self, principal: Principal, items: list[dict]) -> list[dict[str, str]]:
        if not isinstance(items, list):
            raise ValueError("invalid_items")
        prepared: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise ValueError("invalid_items")
            vid = str(raw.get("file_version_id") or "")
            role = normalize_role(raw.get("role"))
            if not vid:
                raise ValueError("invalid_items")
            if vid in seen:
                raise ValueError("duplicate_item")
            seen.add(vid)
            version = self.versions_by_id.get(vid)
            if version is None or version.tenant_id != principal.tenant_id:
                raise KeyError("missing")
            if version.status != READY:
                raise ValueError("file_not_ready")
            resource = self.resource(version.file_id)
            grants = self.grants(version.file_id, principal.user_id)
            if resource is None or resource.kind != "file" or not can_read(principal, resource, grants):
                raise KeyError("missing")
            prepared.append({"file_version_id": version.id, "role": role, "file_id": version.file_id, "original_filename": version.original_filename})
        return prepared

    def _bound_audit_items(self, principal, items, *, require_rules=True):
        prepared = self._prepared_items(principal, items)
        for row, raw in zip(prepared, items):
            rule_id = raw.get("rule_version_id")
            rule = None
            if row["role"] == "reference" and rule_id:
                raise ValueError("reference_rule_forbidden")
            if row["role"] == "target":
                if not rule_id and require_rules:
                    raise ValueError("rule_required")
                if rule_id:
                    rule = self.rule_versions.get(rule_id)
                    if not rule or rule["tenant_id"] != principal.tenant_id:
                        raise ValueError("unknown_rule")
            execution = None
            if rule:
                from ppap_excel_audit_engine.checkers.rule_declarations import freeze_target_rule_execution
                execution = freeze_target_rule_execution(rule["body"], row.get("original_filename") or "")
            row.update(
                rule_version_id=rule["id"] if rule else None,
                rule_title=rule["title"] if rule else None,
                rule_body=deepcopy(rule["body"]) if rule else None,
                rule_execution=deepcopy(execution) if execution is not None else None,
            )
        return prepared

    def _accept(
        self,
        principal: Principal,
        key: str,
        source_draft_id: str | None,
        predecessor_id: str | None,
        items: list[dict] | None,
    ) -> dict:
        existing_id = self.idempotency.get((principal.tenant_id, key))
        draft = self.drafts.get(source_draft_id) if source_draft_id else None
        if source_draft_id:
            if not draft or draft["tenant_id"] != principal.tenant_id:
                raise KeyError("missing")
            if items is None:
                items = list(self.draft_items.get(source_draft_id, []))
        if items is None:
            items = []
        prepared = self._bound_audit_items(principal, items)
        require_targets(prepared)
        fingerprint = request_fingerprint(prepared)
        if source_draft_id:
            analysis_mode = normalize_analysis_mode(draft.get("analysis_mode"))
        elif predecessor_id:
            analysis_mode = normalize_analysis_mode((self.rounds.get(predecessor_id) or {}).get("analysis_mode"))
        else:
            analysis_mode = ANALYSIS_STANDARD
        if existing_id:
            current = self.rounds[existing_id]
            resource = self.resources.get(existing_id)
            if resource is None or not can_read(principal, resource, self.grants(existing_id, principal.user_id)):
                raise KeyError("missing")
            if (
                current["request_fingerprint"] != fingerprint
                or current['predecessor_id'] != predecessor_id
                or current.get("analysis_mode") != analysis_mode
            ):
                raise ValueError("idempotency_conflict")
            return self._round_public(existing_id)
        analysis_mode = self._offered_analysis_mode(analysis_mode)
        if source_draft_id and draft["status"] == DRAFT_ACCEPTED:
            raise ValueError("draft_already_accepted")
        round_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        self.resources[round_id] = Resource(round_id, principal.tenant_id, "audit_round")
        self.grant_table.setdefault((round_id, principal.user_id), set()).update({"read", "review"})
        self.rounds[round_id] = {
            "id": round_id,
            "tenant_id": principal.tenant_id,
            "created_by": principal.user_id,
            "source_draft_id": source_draft_id,
            "predecessor_id": predecessor_id,
            "idempotency_key": key,
            "request_fingerprint": fingerprint,
            "execution": EXEC_QUEUED,
            "result_attempt": None,
            "coverage": {},
            "created_at": "now",
            "analysis_mode": analysis_mode,
        }
        self.round_inputs[round_id] = prepared
        self.findings[round_id] = {}
        self.jobs[job_id] = {
            "id": job_id,
            "kind": "audit",
            "payload": job_payload(
                round_id,
                analysis_mode,
                self.settings.deep_executor_revision if self.settings else "",
            ),
            "status": "queued",
            "attempt": 0,
            "created": len(self.jobs),
            "lease_owner": None,
            "lease_until": None,
            "tenant_id": principal.tenant_id,
        }
        self.idempotency[(principal.tenant_id, key)] = round_id
        if source_draft_id:
            draft["status"] = DRAFT_ACCEPTED
        target = self.draft_writebacks.get(source_draft_id) if source_draft_id else None
        if target is None and predecessor_id:
            target = self.round_writebacks.get(predecessor_id)
        if target:
            self.round_writebacks[round_id] = dict(target)
        if self.settings and self.settings.usage_charge_enabled and self.settings.feishu_ai_item_id and principal.provider == "feishu":
            from ppap_core.feishu_usage import audit_usage_points, remaining_trial_points, split_trial_and_partner
            item = self.settings.feishu_ai_item_id
            remaining = remaining_trial_points(self.settings.feishu_free_trial_quota_points, self.trial_consumed_points(principal.tenant_id, item))
            for points, trial in split_trial_and_partner(remaining, audit_usage_points(sum(x["role"] == "target" for x in prepared))):
                event = self.record_usage_event(principal, item, points, trial, trace_id=round_id + (":trial" if trial else ":partner"))
                next(x for x in self.usage_events if x["id"] == event["id"])["round_id"] = round_id
        return self._round_public(round_id)

    def _draft_public(self, draft_id: str) -> dict:
        draft = self.drafts[draft_id]
        items = []
        for item in self.draft_items.get(draft_id, []):
            version = self.versions_by_id[item["file_version_id"]]
            items.append(
                {
                    "role": item["role"],
                    "file_version_id": version.id,
                    "file_id": version.file_id,
                    "original_filename": version.original_filename,
                    "status": version.status,
                    "rule_version_id": item.get("rule_version_id"),
                    "rule": {"id": item["rule_version_id"], "title": item["rule_title"], "body": item["rule_body"]} if item.get("rule_version_id") else None,
                }
            )
        return {
            "id": draft_id,
            "status": draft["status"],
            "updated_at": draft.get("updated_at"),
            "predecessor_id": draft.get("predecessor_id"),
            "analysis_mode": normalize_analysis_mode(draft.get("analysis_mode")),
            "writeback": public_target(self.draft_writebacks.get(draft_id)),
            "items": items,
        }

    def _round_public(self, round_id: str) -> dict:
        row = self.rounds[round_id]
        inputs = []
        for item in self.round_inputs.get(round_id, []):
            version = self.versions_by_id[item["file_version_id"]]
            inputs.append(
                {
                    "role": item["role"],
                    "file_version_id": version.id,
                    "file_id": version.file_id,
                    "original_filename": version.original_filename,
                    "status": version.status,
                    "rule_version_id": item.get("rule_version_id"),
                    "rule": {"id": item["rule_version_id"], "title": item["rule_title"], "body": item["rule_body"]} if item.get("rule_version_id") else None,
                }
            )
        findings = []
        for item_key, finding in sorted(self.findings.get(round_id, {}).items()):
            review = self.reviews.get((round_id, item_key))
            findings.append(
                {
                    "item_key": finding["item_key"],
                    "classification": finding.get("classification") or "",
                    "item": finding.get("item") or "",
                    "priority": finding.get("priority") or "",
                    "status": finding["status"],
                    "suggestions": finding.get("suggestions") or "",
                    "locator": finding.get("locator"),
                    "review": None
                    if review is None
                    else {
                        "judgment": review["judgment"],
                        "rationale": review["rationale"],
                        "next_step": review["next_step"],
                        "version": review["version"],
                    },
                }
            )
        payload = {}
        for job in self.jobs.values():
            if job.get("kind") == "audit" and isinstance(job.get("payload"), dict) and job["payload"].get("round_id") == round_id:
                payload = job["payload"]
                break
        extra = {}
        if payload.get("executor"):
            extra["executor"] = payload["executor"]
        if payload.get("executor_revision"):
            extra["executor_revision"] = payload["executor_revision"]
        return {
            "id": round_id,
            "execution": row["execution"],
            "coverage": row.get("coverage") or {},
            "predecessor_id": row.get("predecessor_id"),
            "source_draft_id": row.get("source_draft_id"),
            "result_attempt": row.get("result_attempt"),
            "created_at": row.get("created_at"),
            "analysis_mode": normalize_analysis_mode(row.get("analysis_mode")),
            "writeback": public_target(self.round_writebacks.get(round_id)),
            **extra,
            "inputs": inputs,
            "findings": findings,
        }

    def record_usage_event(
        self,
        principal: Principal,
        ai_item_id: str,
        consumed_points: int,
        is_free_trial: bool,
        *,
        trace_id: str | None = None,
    ) -> dict:
        item = str(ai_item_id or "").strip()
        if not item:
            raise ValueError("feishu_ai_item_missing")
        points = int(consumed_points)
        if points < 0:
            raise ValueError("invalid_usage_points")
        tid = str(trace_id or "").strip() or None
        if tid:
            for row in self.usage_events:
                if row["tenant_id"] == principal.tenant_id and row["trace_id"] == tid:
                    return dict(row)
        event_id = str(uuid.uuid4())
        row = {
            "id": event_id,
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "ai_item_id": item,
            "consumed_points": points,
            "is_free_trial": bool(is_free_trial),
            "external_status": "none" if is_free_trial else "pending",
            "external_record_id": None,
            "trace_id": tid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.usage_events.append(row)
        return dict(row)

    def save_file_source(self, file_version_id: str, object_kind: str, object_ref: str) -> None:
        self.file_sources[file_version_id] = {
            "provider": "feishu",
            "object_kind": str(object_kind or "file"),
            "object_ref": str(object_ref),
        }

    def file_source(self, file_version_id: str) -> dict | None:
        row = self.file_sources.get(file_version_id)
        return dict(row) if row else None

    def round_writeback(self, round_id: str) -> dict | None:
        row = self.round_writebacks.get(round_id)
        return dict(row) if row else None

    def trial_baseline_points(self, tenant_id: str, ai_item_id: str) -> int:
        item = str(ai_item_id or "").strip()
        direct = int(self.trial_baselines.get((tenant_id, item), 0))
        if direct:
            return direct
        keys = {principal.tenant_key for principal in self.identities.values() if principal.tenant_id == tenant_id}
        from ppap_core.fixtures import SUBJECTS
        keys |= {spec["tenant_key"] for spec in SUBJECTS.values() if spec["tenant_id"] == tenant_id}
        return sum(int(self.trial_baselines.get((key, item), 0)) for key in keys)

    def trial_consumed_points(self, tenant_id: str, ai_item_id: str) -> int:
        item = str(ai_item_id or "").strip()
        total = 0
        for row in self.usage_events:
            if (
                row["tenant_id"] == tenant_id
                and row["ai_item_id"] == item
                and row["is_free_trial"]
                and row["external_status"] == "none"
            ):
                total += int(row["consumed_points"])
        return total + self.trial_baseline_points(tenant_id, item)

    def usage_event_public(self, row: dict) -> dict:
        from ppap_api.store import AuthStore
        return AuthStore.usage_event_public(self, row)

    def list_usage_events(self, principal, limit=50, offset=0, since=None, until=None):
        return [self.usage_event_public(row) for row in reversed(self.usage_events)
                if row["tenant_id"] == principal.tenant_id
                and (not since or row["created_at"][:10] >= str(since))
                and (not until or row["created_at"][:10] <= str(until))][offset:offset + limit]

    def usage_event(self, principal, event_id):
        return next((dict(row) for row in self.usage_events
                     if row["id"] == event_id and row["tenant_id"] == principal.tenant_id), None)

    def claim_usage_report(self, principal, event_id):
        row = self.usage_event(principal, event_id)
        if not row or row["user_id"] != principal.user_id or row["external_status"] not in {"pending", "unknown"} or row["is_free_trial"]:
            return None
        claim = str(uuid.uuid4())
        for item in self.usage_events:
            if item["id"] == event_id:
                item.update(external_status="reporting", report_claim=claim, reporting_at=_now())
        return claim

    def settle_usage_external(self, principal, event_id, claim, status, record_id=None):
        for item in self.usage_events:
            if (item["id"] == event_id and item["tenant_id"] == principal.tenant_id
                    and item["user_id"] == principal.user_id and item.get("report_claim") == claim
                    and item["external_status"] == "reporting"):
                item.update(external_status=status, external_record_id=record_id)
                return dict(item)
        raise KeyError("usage_claim_lost")
