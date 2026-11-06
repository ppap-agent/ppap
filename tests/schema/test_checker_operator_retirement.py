"""PostgreSQL operator retirement is scoped, transactional and replayable."""

import os
from uuid import uuid4

import pytest
from product.db import connect
from product.settings import Settings
from psycopg.types.json import Jsonb


@pytest.fixture
def retirement_db(schema_tools):
    schema_tools.require_postgres()
    database = "ppap_retirement_" + uuid4().hex[:10]
    schema_tools.require(
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()} TEMPLATE ppap"),
        "create retirement database",
    )
    settings = Settings(pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")))
    try:
        yield settings, database
    finally:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"), "drop retirement database"
        )


def seed(conn, user, tenant, *, membership=True):
    suite, job, operation = [uuid4() for _ in range(3)]
    conn.execute("INSERT INTO product.users(id) VALUES (%s) ON CONFLICT DO NOTHING", (user,))
    conn.execute("INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES (%s,'test',%s,'test')", (tenant, str(tenant)))
    if membership:
        conn.execute("INSERT INTO product.memberships(user_id,tenant_id,role) VALUES (%s,%s,'member')", (user, tenant))
    conn.execute(
        "INSERT INTO product.suite_revisions(id,tenant_id,suite_key,revision_number,overrides,resolved_digest,created_by) VALUES (%s,%s,'test',1,'{}',%s,%s)",
        (suite, tenant, "a" * 64, user),
    )
    conn.execute(
        """INSERT INTO engine.jobs(id,tenant_id,kind,status,attempt,lease_owner,lease_until)
           VALUES (%s,%s,'checker_optimization','leased',1,'test-worker',now()+interval '1 minute')""",
        (job, tenant),
    )
    conn.execute("INSERT INTO engine.attempts(id,job_id,attempt,worker_id) VALUES (%s,%s,1,'test-worker')", (uuid4(), job))
    conn.execute(
        """INSERT INTO product.checker_optimizations(operation_id,tenant_id,initiating_user_id,suite_revision_id,action,request_fingerprint,frozen,job_id,state,model_started_at)
           VALUES (%s,%s,%s,%s,'run_tests','test',%s,%s,'running',now())""",
        (operation, tenant, user, suite, Jsonb({"consume_model": True}), job),
    )
    return operation, job


def test_retirement_is_tenant_scoped_and_rollback_preserves_live_fence(retirement_db):
    settings, _ = retirement_db
    user, first, second = [uuid4() for _ in range(3)]
    with connect(settings, "migrator") as conn:
        first_op, first_job = seed(conn, user, first)
        second_op, second_job = seed(conn, user, second)
        with conn.transaction(force_rollback=True):
            conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (user, first))
            assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (first_op,)).fetchone() == ("unknown",)
        assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (first_op,)).fetchone() == ("running",)
        assert conn.execute("SELECT status FROM engine.jobs WHERE id=%s", (first_job,)).fetchone() == ("leased",)
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (user, first))
        assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (first_op,)).fetchone() == ("unknown",)
        assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (second_op,)).fetchone() == ("running",)
        assert conn.execute("SELECT status FROM engine.jobs WHERE id=%s", (second_job,)).fetchone() == ("leased",)


def test_physical_delete_without_membership_also_tombstones_and_fences(retirement_db):
    settings, _ = retirement_db
    user, tenant = uuid4(), uuid4()
    with connect(settings, "migrator") as conn:
        operation, job = seed(conn, user, tenant, membership=False)
        conn.execute("DELETE FROM product.users WHERE id=%s", (user,))
        assert conn.execute("SELECT initiating_user_id,state,cancel_requested FROM product.checker_optimizations WHERE operation_id=%s", (operation,)).fetchone() == (None, "unknown", True)
        assert conn.execute("SELECT status,lease_until FROM engine.jobs WHERE id=%s", (job,)).fetchone() == ("cancelled", None)
        assert conn.execute("SELECT created_by FROM product.suite_revisions WHERE tenant_id=%s", (tenant,)).fetchone() == (None,)


def test_retirement_migration_replays_and_closes_preexisting_orphans(retirement_db, schema_tools):
    settings, database = retirement_db
    user, tenant = uuid4(), uuid4()
    with connect(settings, "migrator") as conn:
        operation, job = seed(conn, user, tenant, membership=False)
    for _ in range(2):
        schema_tools.require(
            schema_tools.psql(schema_tools.migrator_user(), schema_tools.migrator_password(), database, "-f", "migrations/0049_checker_operator_retirement.sql"), "replay retirement migration"
        )
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT state,usage FROM product.checker_optimizations WHERE operation_id=%s", (operation,)).fetchone() == ("unknown", {"uncertain": True})
        assert conn.execute("SELECT status FROM engine.jobs WHERE id=%s", (job,)).fetchone() == ("cancelled",)
        assert conn.execute("SELECT count(*) FROM product.schema_migrations WHERE version='0049_checker_operator_retirement.sql'").fetchone() == (1,)
