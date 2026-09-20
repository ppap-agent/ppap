from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ppap_core.audits import (
    ClaimedJob,
    DRAFT_ACCEPTED,
    DRAFT_OPEN,
    EXEC_COMPLETED,
    EXEC_FAILED,
    EXEC_QUEUED,
    EXEC_RUNNING,
    RoundInput,
    RoundWork,
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
        job_id, payload = rule_parse_request(request_id, source_file_id, source_file_version_id, document_type)
        prepared = self._prepared_items(principal, [{"file_version_id": payload["source_file_version_id"], "role": "reference"}])
        if prepared[0]["file_id"] != payload["source_file_id"]:
            raise KeyError("missing")
        previous = self.jobs.get(job_id)
        if previous:
            if previous["tenant_id"] != principal.tenant_id or previous["kind"] != "rule_parse":
                raise KeyError("missing")
            if previous["payload"] != payload:
                raise ValueError("rule_parse_conflict")
        else:
            self.jobs[job_id] = {"id": job_id, "tenant_id": principal.tenant_id, "kind": "rule_parse", "payload": payload, "status": "queued", "attempt": 0, "created": len(self.jobs), "lease_until": None}
        return self.get_rule_parse(principal, job_id)

    def get_rule_parse(self, principal, job_id):
        job = self.jobs.get(job_id)
        if not job or job["tenant_id"] != principal.tenant_id or job["kind"] != "rule_parse":
            raise KeyError("missing")
        self._prepared_items(principal, [{"file_version_id": job["payload"]["source_file_version_id"], "role": "reference"}])
        return rule_parse_view(job_id, job["payload"], job["status"], job.get("result"), bool(job.get("lease_until") and job["lease_until"] < _now()))

    def list_rules(self, tenant_id: str) -> list[dict[str, str]]:
        rows = [row for row in self.rule_versions.values() if row["tenant_id"] == tenant_id]
        rows.sort(key=lambda row: (row.get("created_at", ""), row["id"]), reverse=True)
        return [{"id": row["id"], "title": row["title"], "created_at": row.get("created_at"), "kind": str(row["body"].get("kind") or "engine"),
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

    def create_draft(self, principal: Principal) -> dict:
        draft_id = str(uuid.uuid4())
        self.resources[draft_id] = Resource(draft_id, principal.tenant_id, "audit_draft")
        self.grant_table.setdefault((draft_id, principal.user_id), set()).update({"read", "review"})
        self.drafts[draft_id] = {
            "id": draft_id,
            "tenant_id": principal.tenant_id,
            "created_by": principal.user_id,
            "rule_version_id": None,
            "status": DRAFT_OPEN,
        }
        self.draft_items[draft_id] = []
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict | None:
        draft = self.drafts.get(draft_id)
        if not draft:
            return None
        return self._draft_public(draft_id)

    def save_draft(self, principal: Principal, draft_id: str, rule_version_id: str | None, items: list[dict] | None) -> dict:
        draft = self.drafts.get(draft_id)
        if not draft or draft["tenant_id"] != principal.tenant_id:
            raise KeyError("missing")
        if draft["status"] != DRAFT_OPEN:
            raise ValueError("draft_already_accepted")
        if rule_version_id is not None:
            rule = self.rule_versions.get(rule_version_id)
            if not rule or rule["tenant_id"] != principal.tenant_id:
                raise ValueError("unknown_rule")
            draft["rule_version_id"] = rule_version_id
        if items is not None:
            self.draft_items[draft_id] = self._prepared_items(principal, items)
        return self._draft_public(draft_id)

    def accept_draft(self, principal: Principal, draft_id: str, idempotency_key: str) -> dict:
        key = normalize_idempotency_key(idempotency_key)
        return self._accept(principal, key, source_draft_id=draft_id, predecessor_id=None, rule_version_id=None, items=None)

    def round_by_idempotency(self, principal: Principal, idempotency_key: str) -> dict | None:
        key = normalize_idempotency_key(idempotency_key)
        existing_id = self.idempotency.get((principal.tenant_id, key))
        if not existing_id:
            return None
        return self._round_public(existing_id)

    def resubmit_round(
        self,
        principal: Principal,
        round_id: str,
        idempotency_key: str,
        rule_version_id: str | None,
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
            rule_version_id=rule_version_id or current["rule_version_id"],
            items=items,
        )

    def get_round(self, round_id: str) -> dict | None:
        if round_id not in self.rounds:
            return None
        return self._round_public(round_id)

    def list_rounds(self, principal: Principal, limit: int = 50) -> list[dict]:
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
            result_counts = {"PASS": 0, "NG": 0, "ERR": 0}
            for finding in body.get("findings") or []:
                if finding["status"] in result_counts:
                    result_counts[finding["status"]] += 1
            items.append(
                {
                    "id": body["id"],
                    "execution": body["execution"],
                    "rule": body["rule"],
                    "predecessor_id": body.get("predecessor_id"),
                    "created_at": body.get("created_at"),
                    "inputs": [
                        {
                            "role": item["role"],
                            "original_filename": item["original_filename"],
                            "file_version_id": item["file_version_id"],
                        }
                        for item in body.get("inputs") or []
                    ],
                    "result_counts": result_counts,
                }
            )
            if len(items) >= cap:
                break
        return items

    def round_input_version(self, round_id: str, file_id: str, version_id: str) -> FileVersion | None:
        if round_id not in self.rounds:
            return None
        if not any(row["file_version_id"] == version_id for row in self.round_inputs.get(round_id, [])):
            return None
        version = self.versions.get((file_id, version_id))
        return version

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
                )
            )
        return RoundWork(round_id, row["tenant_id"], row["rule_title"], row["rule_body"], inputs, row.get("result_attempt"))

    def claim_job(self, worker_ident: str) -> ClaimedJob | None:
        now = _now()
        eligible = [
            job
            for job in self.jobs.values()
            if job["status"] == "queued"
            or (
                job["status"] == "leased"
                and job["kind"] != "rule_parse"
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
        if claimed.kind == "rule_parse":
            job["result"] = result
        findings = result.get("findings") or []
        self.attempts[(claimed.id, claimed.attempt)] = {
            "worker_id": worker_ident,
            "diagnostic": {"note": result.get("note") or "round_audit", "finding_count": len(findings)},
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
            prepared.append({"file_version_id": version.id, "role": role, "file_id": version.file_id})
        return prepared

    def _accept(
        self,
        principal: Principal,
        key: str,
        source_draft_id: str | None,
        predecessor_id: str | None,
        rule_version_id: str | None,
        items: list[dict] | None,
    ) -> dict:
        existing_id = self.idempotency.get((principal.tenant_id, key))
        draft = self.drafts.get(source_draft_id) if source_draft_id else None
        if source_draft_id:
            if not draft or draft["tenant_id"] != principal.tenant_id:
                raise KeyError("missing")
            if items is None:
                items = list(self.draft_items.get(source_draft_id, []))
            if rule_version_id is None:
                rule_version_id = draft["rule_version_id"]
        if items is None:
            items = []
        if not rule_version_id:
            raise ValueError("rule_required")
        rule = self.rule_versions.get(rule_version_id)
        if not rule or rule["tenant_id"] != principal.tenant_id:
            raise ValueError("unknown_rule")
        prepared = self._prepared_items(principal, items)
        require_targets(prepared)
        fingerprint = request_fingerprint(rule["id"], prepared)
        if existing_id:
            current = self.rounds[existing_id]
            if current["request_fingerprint"] != fingerprint:
                raise ValueError("idempotency_conflict")
            return self._round_public(existing_id)
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
            "rule_version_id": rule["id"],
            "rule_title": rule["title"],
            "rule_body": dict(rule["body"]),
            "idempotency_key": key,
            "request_fingerprint": fingerprint,
            "execution": EXEC_QUEUED,
            "result_attempt": None,
            "coverage": {},
            "created_at": "now",
        }
        self.round_inputs[round_id] = prepared
        self.findings[round_id] = {}
        self.jobs[job_id] = {
            "id": job_id,
            "kind": "audit",
            "payload": {"round_id": round_id},
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
        return self._round_public(round_id)

    def _draft_public(self, draft_id: str) -> dict:
        draft = self.drafts[draft_id]
        rule = self.rule_versions.get(draft["rule_version_id"]) if draft["rule_version_id"] else None
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
                }
            )
        return {
            "id": draft_id,
            "status": draft["status"],
            "rule": {"id": rule["id"], "title": rule["title"]} if rule else None,
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
        return {
            "id": round_id,
            "execution": row["execution"],
            "rule": {"id": row["rule_version_id"], "title": row["rule_title"]},
            "coverage": row.get("coverage") or {},
            "predecessor_id": row.get("predecessor_id"),
            "source_draft_id": row.get("source_draft_id"),
            "result_attempt": row.get("result_attempt"),
            "created_at": row.get("created_at"),
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
        return total

    def usage_event_public(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "ai_item_id": row["ai_item_id"],
            "consumed_points": row["consumed_points"],
            "is_free_trial": row["is_free_trial"],
            "external_status": row["external_status"],
            "external_record_id": row.get("external_record_id"),
            "trace_id": row["trace_id"],
            "created_at": row.get("created_at"),
        }

    def list_usage_events(self, principal: Principal, limit: int = 50) -> list[dict]:
        try:
            cap = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            cap = 50
        items = [
            self.usage_event_public(row)
            for row in reversed(self.usage_events)
            if row["tenant_id"] == principal.tenant_id and row["user_id"] == principal.user_id
        ]
        return items[:cap]

    def usage_event(self, principal: Principal, event_id: str) -> dict | None:
        for row in self.usage_events:
            if row["id"] == event_id and row["tenant_id"] == principal.tenant_id:
                return dict(row)
        return None

    def ensure_usage_trace_id(self, principal: Principal, event_id: str) -> dict:
        row = self.usage_event(principal, event_id)
        if row is None:
            raise KeyError("access_denied")
        if row.get("trace_id"):
            return row
        trace = uuid.uuid4().hex
        for item in self.usage_events:
            if item["id"] == event_id and item["tenant_id"] == principal.tenant_id:
                item["trace_id"] = trace
                return dict(item)
        raise KeyError("access_denied")

    def settle_usage_external(
        self, principal: Principal, event_id: str, status: str, record_id: str | None = None
    ) -> dict:
        if status not in {"pending", "confirmed", "failed"}:
            raise ValueError("invalid_external_status")
        for item in self.usage_events:
            if item["id"] == event_id and item["tenant_id"] == principal.tenant_id and not item["is_free_trial"]:
                item["external_status"] = status
                item["external_record_id"] = record_id
                return dict(item)
        raise KeyError("access_denied")
