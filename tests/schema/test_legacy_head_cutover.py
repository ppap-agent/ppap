"""Isolated PostgreSQL proof for legacy carrier heads.

New admission refuses a carrier head. A directory pin still freezes that revision.
Dry-run and failed apply leave every pointer unchanged.
"""

import json
import os
import uuid

import pytest
from audit.standard_library import check_clause
from audit.standard_suites import library_revision
from product.db import connect
from product.legacy_head_cutover import apply_legacy_head_cutover, inspect_legacy_heads, legacy_carrier_program
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore, freeze_selected_targets
from psycopg.types.json import Jsonb


def _files(checker: bytes, entry: str) -> dict:
    rules = {"title": "cutover", "rules": [{"id": "C1"}], "checker_binding": {"C1": entry}}
    return {"checker.py": checker, "rules.json": json.dumps(rules).encode()}


CARRIER = _files(
    b"from audit.standard_library import check_clause, prepare_target\n"
    b"RESOURCES = {'carriers': {'.xlsx': 'audit.checkers.v4.example:Missing'}, 'methods': {'C1': 'check'}}\n"
    b"def check(context):\n    return check_clause(context, RESOURCES)\n",
    "checker:check",
)
DIRECTORY = _files(b"def create_audit(context):\n    return []\n", "checker:create_audit")


@pytest.fixture
def cutover_db(schema_tools, memory_objects_type):
    schema_tools.require_postgres()
    database = "ppap_legacy_head_" + uuid.uuid4().hex[:10]
    schema_tools.require(
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()} TEMPLATE ppap"),
        "create legacy head database",
    )
    settings = Settings(pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")))
    objects = memory_objects_type()
    try:
        yield settings, objects
    finally:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"),
            "drop legacy head database",
        )


def _proof(digest: str) -> dict:
    return {
        "reasons": [],
        "total_samples": 4,
        "passed_samples": 4,
        "new_errors": 0,
        "library_revision": library_revision(),
        "proof_digest": "ab" * 32,
        "report": {"related_revision_ids": []},
        "suite_digest": digest,
    }


def _install(store, key: str, files: dict, source_path: str | None):
    return store.create_revision(tenant_id=None, suite_key=key, files=files, created_by=None, source_path=source_path)


def _validate(conn, revision_id: str, digest: str):
    conn.execute(
        """INSERT INTO product.suite_validations(tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof)
           VALUES (NULL, %s, 'verified', 1, %s, '{}', %s)""",
        (revision_id, digest, Jsonb(_proof(digest))),
    )


def _official_head(conn, key: str, revision_id: str):
    conn.execute(
        """INSERT INTO product.official_suite_heads(suite_key, current_revision_id, revision, validation_revision)
           VALUES (%s, %s, 1, 1)""",
        (key, revision_id),
    )


def _pin(conn, tenant: str, key: str, revision_id: str):
    conn.execute(
        """INSERT INTO product.tenant_suite_heads(tenant_id, suite_key, current_revision_id, revision, force_unverified)
           VALUES (%s, %s, %s, 1, true)""",
        (tenant, key, revision_id),
    )


def _admit(store, conn, tenant: str, key: str, *, preparation: bool):
    return freeze_selected_targets(store, conn, tenant, [{"role": "target", "file_version_id": "file-1", "rule_body": {"suite_key": key}}], preparation=preparation)


def test_legacy_heads_reject_new_admission_until_explicit_directory_publish(cutover_db, monkeypatch):
    settings, objects = cutover_db
    suites = SuiteStore(settings, objects, role="migrator")
    releases = SuiteReleaseStore(settings, objects, role="migrator")
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("legacy resolver")

    monkeypatch.setattr("audit.standard_library.check_clause", forbidden)
    monkeypatch.setattr("audit.standard_suites._carriers", forbidden)
    monkeypatch.setattr("audit.presets.preset_source_bindings", forbidden)
    monkeypatch.setattr("audit.engine.routing.resolve_engine_agent_class", forbidden)
    assert legacy_carrier_program(CARRIER) is True
    assert legacy_carrier_program(DIRECTORY) is False
    assert check_clause.__name__ == "check_clause"

    official_key, pinned_key, directory_key = "OFFICIAL:OLD", "PIN:OLD", "PIN:V5"
    official_carrier = _install(suites, official_key, CARRIER, None)
    official_directory = _install(suites, official_key, DIRECTORY, "ACME/OFF")
    official_unverified = _install(suites, official_key, DIRECTORY, "ACME-v6/OFF")
    pinned_carrier = _install(suites, pinned_key, CARRIER, None)
    pinned_directory = _install(suites, pinned_key, DIRECTORY, "ACME/PIN")
    selected_carrier = _install(suites, directory_key, CARRIER, None)
    selected_directory = _install(suites, directory_key, DIRECTORY, "ACME/SEL")
    tenant_pin, tenant_directory, tenant_open, user, round_id = (str(uuid.uuid4()) for _ in range(5))

    with connect(settings, "migrator") as conn:
        for tenant, label in ((tenant_pin, "pin"), (tenant_directory, "dir"), (tenant_open, "open")):
            conn.execute(
                "INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES(%s,'test',%s,%s)",
                (tenant, tenant, label),
            )
        conn.execute("INSERT INTO product.users(id) VALUES(%s)", (user,))
        for revision in (official_carrier, official_directory, pinned_carrier, pinned_directory, selected_carrier, selected_directory):
            _validate(conn, revision.id, revision.resolved_digest)
        _official_head(conn, official_key, official_carrier.id)
        _official_head(conn, pinned_key, pinned_carrier.id)
        _official_head(conn, directory_key, selected_carrier.id)
        _pin(conn, tenant_pin, pinned_key, pinned_carrier.id)
        _pin(conn, tenant_directory, directory_key, selected_directory.id)
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (round_id, tenant_open))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution)
               VALUES(%s,%s,%s,'legacy-round','fingerprint','queued')""",
            (round_id, tenant_open, user),
        )
        rule_id = str(uuid.uuid4())
        rule_body = {"title": "历史", "rules": [{"id": "C1"}], "suite_key": official_key}
        file_sha256 = "12" * 32
        checker_code = "cd" * 32
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'历史',%s)",
            (rule_id, tenant_open, Jsonb(rule_body)),
        )
        snapshot = {
            "tenant_id": tenant_open,
            "round_id": round_id,
            "targets": [
                {
                    "suite_revision_id": official_carrier.id,
                    "suite_key": official_key,
                    "suite_digest": official_carrier.resolved_digest,
                    "checker_digest": checker_code,
                    "rule_version_id": rule_id,
                    "rule_body": rule_body,
                    "file_sha256": file_sha256,
                }
            ],
        }
        conn.execute(
            """INSERT INTO product.checker_execution_snapshots(round_id,tenant_id,input_digest,body)
               VALUES(%s,%s,%s,%s)""",
            (round_id, tenant_open, "cd" * 32, Jsonb(snapshot)),
        )
        conn.execute(
            "INSERT INTO engine.jobs(id,tenant_id,audit_round_id,kind,status) VALUES(%s,%s,%s,'audit','queued')",
            (str(uuid.uuid4()), tenant_open, round_id),
        )
        before_snapshot = conn.execute("SELECT body FROM product.checker_execution_snapshots WHERE round_id=%s", (round_id,)).fetchone()[0]
        before_carrier = conn.execute("SELECT resolved_digest FROM product.suite_revisions WHERE id=%s", (official_carrier.id,)).fetchone()[0]
        finding_id, usage_id, report_id = (str(uuid.uuid4()) for _ in range(3))
        conn.execute(
            "INSERT INTO product.findings(id,round_id,item_key,classification,item,status,suggestions) VALUES(%s,%s,'C1','规则审核','历史条款','NG','保持')",
            (finding_id, round_id),
        )
        conn.execute(
            "INSERT INTO product.reviews(round_id,item_key,user_id,judgment,rationale,version) VALUES(%s,'C1',%s,'disagree','人工仍为 NG',1)",
            (round_id, user),
        )
        conn.execute(
            """INSERT INTO product.usage_events(id,tenant_id,user_id,ai_item_id,consumed_points,is_free_trial,external_status,round_id)
               VALUES(%s,%s,%s,'legacy-round',3,true,'none',%s)""",
            (usage_id, tenant_open, user, round_id),
        )
        conn.execute(
            """INSERT INTO product.report_exports(id,round_id,tenant_id,created_by,fingerprint,status)
               VALUES(%s,%s,%s,%s,'legacy-report','ready')""",
            (report_id, round_id, tenant_open, user),
        )
        identity_sql = """
            SELECT jsonb_build_object(
                'round', (SELECT to_jsonb(r) - 'created_at' FROM product.audit_rounds r WHERE r.id=%s),
                'finding', (SELECT to_jsonb(f) - 'created_at' - 'updated_at' FROM product.findings f WHERE f.id=%s),
                'review', (SELECT to_jsonb(v) - 'created_at' - 'updated_at' FROM product.reviews v WHERE v.round_id=%s),
                'usage', (SELECT to_jsonb(u) - 'created_at' FROM product.usage_events u WHERE u.id=%s),
                'report', (SELECT to_jsonb(e) - 'created_at' FROM product.report_exports e WHERE e.id=%s),
                'rule', (SELECT to_jsonb(rv) - 'created_at' FROM product.rule_versions rv WHERE rv.id=%s),
                'snapshot', (SELECT body FROM product.checker_execution_snapshots WHERE round_id=%s)
            )
        """
        identity_before = conn.execute(identity_sql, (round_id, finding_id, round_id, usage_id, report_id, rule_id, round_id)).fetchone()[0]

    with connect(settings, "migrator") as conn:
        first = inspect_legacy_heads(releases, conn)
        second = inspect_legacy_heads(releases, conn)
    assert first == second
    by_key = {row["suite_key"]: row for row in first}
    assert by_key[official_key]["status"] == "in_flight"
    assert by_key[official_key]["official_legacy"] is True
    assert by_key[official_key]["in_flight_round_ids"] == [round_id]
    assert by_key[pinned_key]["status"] == "pinned_historical"
    assert by_key[directory_key]["official_legacy"] is True
    assert by_key[directory_key]["tenant_pins"][0]["legacy"] is False
    assert by_key["UAES:U08"]["status"] == "not_seeded"

    with connect(settings, "migrator") as conn, conn.transaction():
        with pytest.raises(ValueError, match="suite_version_confirmation_required"):
            _admit(suites, conn, tenant_open, official_key, preparation=False)
        with pytest.raises(ValueError, match="suite_version_confirmation_required"):
            _admit(suites, conn, tenant_pin, pinned_key, preparation=True)
        selected = _admit(suites, conn, tenant_directory, directory_key, preparation=False)
        from types import SimpleNamespace

        from product.store.prechecks import freeze_task_precheck

        precheck = freeze_task_precheck(
            suites,
            conn,
            SimpleNamespace(tenant_id=tenant_open, user_id=user),
            {
                "id": str(uuid.uuid4()),
                "input_revision": 1,
                "customer_preference": None,
                "analysis_mode": "standard",
                "files": [
                    {
                        "selection": "selected",
                        "file": {"id": "file-1"},
                        "match": {"state": "matched", "rule": {"body": {"suite_key": official_key}}},
                    }
                ],
            },
        )
        assert precheck["files"][0]["suite_target"] is None
        assert precheck["files"][0]["rule_execution"] == {"mode": "unavailable", "reason": "suite_version_confirmation_required"}
    assert calls == []
    assert selected[0].suite_revision_id == selected_directory.id
    assert selected[0].suite_digest == selected_directory.resolved_digest

    with pytest.raises(ValueError, match="suite_version_confirmation_required"):
        releases.publish_official(official_carrier.id, expected_revision=1, expected_validation_revision=1)
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key=%s", (official_key,)).fetchone()[0] == official_carrier.id

    dry = apply_legacy_head_cutover(releases, dry_run=True)
    assert dry["dry_run"] is True and dry["applied"] == []
    assert dry["official_heads_before"] == dry["official_heads_after"]
    assert apply_legacy_head_cutover(releases, dry_run=True)["before"] == dry["before"]

    with pytest.raises(ValueError, match="suite_head_conflict"):
        apply_legacy_head_cutover(releases, [{"suite_key": official_key, "revision_id": official_directory.id, "expected_head_revision": 0, "expected_validation_revision": 1}], dry_run=False)
    unverified = {"suite_key": official_key, "revision_id": official_unverified.id, "expected_head_revision": 1, "expected_validation_revision": 1}
    with pytest.raises(ValueError, match="official_suite_validation_required"):
        apply_legacy_head_cutover(releases, [unverified], dry_run=False)
    pin_request = {
        "suite_key": pinned_key,
        "tenant_id": tenant_pin,
        "revision_id": pinned_directory.id,
        "expected_head_revision": 1,
        "expected_validation_revision": 1,
    }
    with pytest.raises(ValueError, match="tenant_pin_not_moved"):
        apply_legacy_head_cutover(releases, [pin_request], dry_run=False)
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key=%s", (official_key,)).fetchone()[0] == official_carrier.id
        assert conn.execute("SELECT current_revision_id::text FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s", (tenant_pin, pinned_key)).fetchone()[0] == pinned_carrier.id

    published = apply_legacy_head_cutover(
        releases,
        [{"suite_key": official_key, "revision_id": official_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1}],
        dry_run=False,
    )
    assert published["applied"] == [{"suite_key": official_key, "revision_id": official_directory.id, "resolved_digest": official_directory.resolved_digest, "result": "published"}]
    again = apply_legacy_head_cutover(
        releases,
        [{"suite_key": official_key, "revision_id": official_directory.id, "expected_head_revision": 2, "expected_validation_revision": 1}],
        dry_run=False,
    )
    assert again["applied"] == [{"suite_key": official_key, "revision_id": official_directory.id, "result": "unchanged"}]
    with pytest.raises(ValueError, match="suite_head_conflict"):
        apply_legacy_head_cutover(releases, [{"suite_key": official_key, "revision_id": official_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1}], dry_run=False)

    with connect(settings, "migrator") as conn, conn.transaction():
        moved = _admit(suites, conn, tenant_open, official_key, preparation=False)
        with pytest.raises(ValueError, match="suite_version_confirmation_required"):
            _admit(suites, conn, tenant_pin, pinned_key, preparation=False)
        snapshot_after = conn.execute("SELECT body FROM product.checker_execution_snapshots WHERE round_id=%s", (round_id,)).fetchone()[0]
        execution = conn.execute("SELECT execution FROM product.audit_rounds WHERE id=%s", (round_id,)).fetchone()[0]
        carrier_after = conn.execute("SELECT resolved_digest FROM product.suite_revisions WHERE id=%s", (official_carrier.id,)).fetchone()[0]
        pin_after = conn.execute("SELECT current_revision_id::text FROM product.tenant_suite_heads WHERE tenant_id=%s", (tenant_pin,)).fetchone()[0]
    assert calls == []
    assert moved[0].suite_revision_id == official_directory.id
    assert snapshot_after == before_snapshot
    assert execution == "queued"
    assert carrier_after == before_carrier
    assert pin_after == pinned_carrier.id
    assert snapshot_after == identity_before["snapshot"]
    with connect(settings, "migrator") as conn:
        identity_after = conn.execute(identity_sql, (round_id, finding_id, round_id, usage_id, report_id, rule_id, round_id)).fetchone()[0]
    assert identity_after["snapshot"]["targets"][0]["checker_digest"] == checker_code
    assert identity_after["snapshot"]["targets"][0]["file_sha256"] == file_sha256
    assert identity_after["snapshot"]["targets"][0]["suite_digest"] == official_carrier.resolved_digest
    assert identity_after["rule"]["body"] == rule_body
    assert identity_after == identity_before


def _identity(conn, round_id):
    return conn.execute(
        """SELECT jsonb_build_object(
             'finding', (SELECT jsonb_agg(to_jsonb(f) - 'created_at' - 'updated_at') FROM product.findings f WHERE f.round_id=%s),
             'review', (SELECT jsonb_agg(to_jsonb(v) - 'updated_at') FROM product.reviews v WHERE v.round_id=%s)
           )""",
        (round_id, round_id),
    ).fetchone()[0]


def test_remaining_head_states_do_not_move_a_pointer_when_apply_is_interrupted(cutover_db, monkeypatch):
    settings, objects = cutover_db
    suites = SuiteStore(settings, objects, role="migrator")
    releases = SuiteReleaseStore(settings, objects, role="migrator")
    from audit.standard_suites import predefined_directory_suites

    leaves = {suite.suite_key: suite for suite in predefined_directory_suites()}
    ready_leaf, verified_leaf = leaves["理想:L01"], leaves["蔚来:N01"]
    missing_key, ready_key, verified_key, broken_key, dependency_key = "GONE:OLD", ready_leaf.suite_key, verified_leaf.suite_key, "BROKEN:OLD", "DEP:OLD"
    missing_carrier = _install(suites, missing_key, CARRIER, None)
    ready_directory = _install(suites, ready_key, dict(ready_leaf.files), ready_leaf.source_path)
    verified_directory = _install(suites, verified_key, dict(verified_leaf.files), verified_leaf.source_path)
    broken_carrier = _install(suites, broken_key, CARRIER, None)
    broken_directory = _install(suites, broken_key, {**DIRECTORY, "checker.py": DIRECTORY["checker.py"] + b"\n# broken\n"}, "ACME/BROKEN")
    dependency_carrier = _install(suites, dependency_key, CARRIER, None)
    dependency_directory = suites.create_revision(
        tenant_id=None,
        suite_key=dependency_key,
        files=DIRECTORY,
        created_by=None,
        related_suite_keys=("MISSING:DEP",),
        source_path="ACME/DEP",
    )
    first_carrier = _install(suites, "INT:A", CARRIER, None)
    first_directory = _install(suites, "INT:A", DIRECTORY, "ACME/INTA")
    second_carrier = _install(suites, "INT:B", CARRIER, None)
    second_directory = _install(suites, "INT:B", DIRECTORY, "ACME/INTB")
    with connect(settings, "migrator") as conn:
        validated = (
            missing_carrier,
            verified_directory,
            broken_carrier,
            broken_directory,
            dependency_carrier,
            dependency_directory,
            first_carrier,
            first_directory,
            second_carrier,
            second_directory,
        )
        for revision in validated:
            _validate(conn, revision.id, revision.resolved_digest)
        conn.execute(
            "UPDATE product.suite_validations SET related_suite_digests=%s WHERE revision_id=%s",
            (Jsonb({"MISSING:DEP": "ab" * 32}), dependency_directory.id),
        )
        for key, revision in ((missing_key, missing_carrier), (broken_key, broken_carrier), (dependency_key, dependency_carrier), ("INT:A", first_carrier), ("INT:B", second_carrier)):
            _official_head(conn, key, revision.id)
        checker_key = conn.execute(
            """SELECT object_key FROM product.suite_objects
               WHERE owner_scope='official' AND digest = (
                 SELECT overrides->>'checker.py' FROM product.suite_revisions WHERE id=%s
               )""",
            (broken_directory.id,),
        ).fetchone()[0]
        opaque = _install(suites, "OPAQUE:OLD", {**CARRIER, "checker.py": CARRIER["checker.py"] + b"\n# opaque\n"}, None)
        _validate(conn, opaque.id, opaque.resolved_digest)
        _official_head(conn, "OPAQUE:OLD", opaque.id)
        opaque_key = conn.execute(
            """SELECT object_key FROM product.suite_objects
               WHERE owner_scope='official' AND digest = (
                 SELECT overrides->>'checker.py' FROM product.suite_revisions WHERE id=%s
               )""",
            (opaque.id,),
        ).fetchone()[0]
    objects.delete(checker_key)
    objects.delete(opaque_key)
    with connect(settings, "migrator") as conn:
        rows = {row["suite_key"]: row for row in inspect_legacy_heads(releases, conn)}
    assert rows[missing_key]["status"] == "missing"
    assert rows[ready_key]["status"] == "directory_ready"
    assert rows[ready_key]["directory_revisions"][0]["revision_id"] == ready_directory.id
    assert rows[verified_key]["status"] == "verified"
    assert rows[dependency_key]["status"] == "blocked"
    assert rows["OPAQUE:OLD"]["status"] == "blocked"
    assert rows[broken_key]["official_revision_id"] == broken_carrier.id
    with pytest.raises(ValueError):
        apply_legacy_head_cutover(
            releases,
            [{"suite_key": broken_key, "revision_id": broken_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1}],
            dry_run=False,
        )
    with pytest.raises(ValueError):
        apply_legacy_head_cutover(
            releases,
            [{"suite_key": dependency_key, "revision_id": dependency_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1}],
            dry_run=False,
        )
    original = releases.publish_official
    calls = {"count": 0}

    def interrupt(revision_id, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("interrupted")
        return original(revision_id, **kwargs)

    monkeypatch.setattr(releases, "publish_official", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        apply_legacy_head_cutover(
            releases,
            [
                {"suite_key": "INT:A", "revision_id": first_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1},
                {"suite_key": "INT:B", "revision_id": second_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1},
            ],
            dry_run=False,
        )
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key='INT:A'").fetchone()[0] == first_directory.id
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key='INT:B'").fetchone()[0] == second_carrier.id
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key=%s", (broken_key,)).fetchone()[0] == broken_carrier.id
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key=%s", (dependency_key,)).fetchone()[0] == dependency_carrier.id
    monkeypatch.setattr(releases, "publish_official", original)
    again = apply_legacy_head_cutover(
        releases,
        [
            {"suite_key": "INT:A", "revision_id": first_directory.id, "expected_head_revision": 2, "expected_validation_revision": 1},
            {"suite_key": "INT:B", "revision_id": second_directory.id, "expected_head_revision": 1, "expected_validation_revision": 1},
        ],
        dry_run=False,
    )
    assert [row["result"] for row in again["applied"]] == ["unchanged", "published"]


def test_directory_admission_refuses_a_related_carrier_head(cutover_db, monkeypatch):
    from product.identity import Principal

    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("legacy resolver")

    monkeypatch.setattr("audit.standard_library.check_clause", forbidden)
    monkeypatch.setattr("audit.standard_suites._carriers", forbidden)
    monkeypatch.setattr("audit.presets.preset_source_bindings", forbidden)
    monkeypatch.setattr("audit.engine.routing.resolve_engine_agent_class", forbidden)
    settings, objects = cutover_db
    suites = SuiteStore(settings, objects, role="migrator")
    releases = SuiteReleaseStore(settings, objects, role="migrator")
    carrier = _install(suites, "REL:CARRIER", CARRIER, None)
    directory = suites.create_revision(
        tenant_id=None,
        suite_key="REL:DIR",
        files=DIRECTORY,
        related_suite_keys=("REL:CARRIER",),
        created_by=None,
        source_path="ACME/DIR",
    )
    tenant, user = (str(uuid.uuid4()) for _ in range(2))
    with connect(settings, "migrator") as conn:
        conn.execute("INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES(%s,'test',%s,'rel')", (tenant, tenant))
        conn.execute("INSERT INTO product.users(id) VALUES(%s)", (user,))
        conn.execute("INSERT INTO product.memberships(user_id,tenant_id,role) VALUES(%s,%s,'member')", (user, tenant))
        _validate(conn, carrier.id, carrier.resolved_digest)
        _validate(conn, directory.id, directory.resolved_digest)
        conn.execute(
            "UPDATE product.suite_validations SET related_suite_digests=%s WHERE revision_id=%s",
            (Jsonb({"REL:CARRIER": carrier.resolved_digest}), directory.id),
        )
        _official_head(conn, "REL:CARRIER", carrier.id)
    principal = Principal(user, tenant, "test", "test", tenant, user, "member")
    with pytest.raises(ValueError, match="suite_version_confirmation_required"):
        releases.publish(principal, "REL:DIR", directory.id, 0, str(uuid.uuid4()), force_unverified=True)
    with pytest.raises(ValueError, match="suite_version_confirmation_required"):
        releases.publish_official(directory.id, expected_revision=0, expected_validation_revision=1)
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key='REL:CARRIER'").fetchone()[0] == carrier.id
        assert conn.execute("SELECT count(*) FROM product.official_suite_heads WHERE suite_key='REL:DIR'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key='REL:DIR'", (tenant,)).fetchone()[0] == 0
        _pin(conn, tenant, "REL:DIR", directory.id)
        with conn.transaction():
            with pytest.raises(ValueError, match="suite_version_confirmation_required"):
                _admit(suites, conn, tenant, "REL:DIR", preparation=True)
    assert calls == []


def test_concurrent_apply_publishes_one_directory_head(cutover_db):
    import threading

    settings, objects = cutover_db
    suites = SuiteStore(settings, objects, role="migrator")
    releases = SuiteReleaseStore(settings, objects, role="migrator")
    key = "RACE:OLD"
    carrier = _install(suites, key, CARRIER, None)
    directory = _install(suites, key, DIRECTORY, "ACME/RACE")
    with connect(settings, "migrator") as conn:
        _validate(conn, carrier.id, carrier.resolved_digest)
        _validate(conn, directory.id, directory.resolved_digest)
        _official_head(conn, key, carrier.id)
    confirmation = {"suite_key": key, "revision_id": directory.id, "expected_head_revision": 1, "expected_validation_revision": 1}
    barrier = threading.Barrier(2)
    published = []
    refused = []

    def apply_once():
        barrier.wait()
        try:
            published.append(apply_legacy_head_cutover(releases, [confirmation], dry_run=False))
        except ValueError as exc:
            refused.append(str(exc))

    threads = [threading.Thread(target=apply_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(published) == 1 and refused == ["suite_head_conflict"]
    assert published[0]["applied"] == [{"suite_key": key, "revision_id": directory.id, "resolved_digest": directory.resolved_digest, "result": "published"}]
    assert published[0]["tenant_pins"] == []
    with connect(settings, "migrator") as conn:
        head = conn.execute("SELECT current_revision_id::text, revision FROM product.official_suite_heads WHERE suite_key=%s", (key,)).fetchone()
    assert head[0] == directory.id and head[1] == 2
