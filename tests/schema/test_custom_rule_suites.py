"""SQL permits rules-only publication only for byte-identical inert starters."""

import os
import uuid

import psycopg
import pytest
from product.db import connect
from product.settings import Settings
from product.store.suites import SuiteStore
from product.suites import UNIMPLEMENTED_CHECKER_SOURCE


@pytest.fixture
def custom_suite_db(schema_tools, memory_objects_type):
    schema_tools.require_postgres()
    database = "ppap_custom_" + uuid.uuid4().hex[:10]
    schema_tools.require(
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()} TEMPLATE ppap"),
        "create custom Suite database",
    )
    settings = Settings(pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")))
    tenant, author = str(uuid.uuid4()), str(uuid.uuid4())
    with connect(settings, "migrator") as conn:
        conn.execute("INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES(%s,'test',%s,'test')", (tenant, tenant))
        conn.execute("INSERT INTO product.users(id) VALUES(%s)", (author,))
    try:
        yield settings, SuiteStore(settings, memory_objects_type()), tenant, author
    finally:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"), "drop custom Suite database"
        )


def test_unverified_sql_publication_checks_inherited_code_and_resources(custom_suite_db):
    settings, suites, tenant, author = custom_suite_db
    first = suites.create_revision(
        tenant_id=tenant,
        suite_key="custom",
        created_by=author,
        files={"rules.json": b'{"rules":[{"id":"R1"}],"checker_binding":{}}', "checker.py": UNIMPLEMENTED_CHECKER_SOURCE},
    )
    inherited = suites.create_revision(tenant_id=tenant, suite_key="custom", created_by=author, base_revision_id=first.id, files={"appendix.json": b"[]"})
    code = suites.create_revision(tenant_id=tenant, suite_key="custom", created_by=author, base_revision_id=first.id, files={"checker.py": b"raise RuntimeError('side effect')\n"})
    resources = suites.create_revision(tenant_id=tenant, suite_key="custom", created_by=author, base_revision_id=inherited.id, files={"resources.py": b"raise RuntimeError('side effect')\n"})
    inherited_resources = suites.create_revision(tenant_id=tenant, suite_key="custom", created_by=author, base_revision_id=resources.id, files={})
    removed_resources = suites.create_revision(tenant_id=tenant, suite_key="custom", created_by=author, base_revision_id=inherited_resources.id, files={}, deleted_paths=("resources.py",))
    with connect(settings, "api") as conn:
        for ordinal, (revision, allowed) in enumerate(((first, True), (inherited, True), (code, False), (resources, False), (inherited_resources, False), (removed_resources, True)), start=1):
            assert conn.execute("SELECT product.suite_has_no_executable_checker(%s)", (revision.id,)).fetchone()[0] is allowed

            def insert():
                conn.execute(
                    """INSERT INTO product.suite_publications
                    (tenant_id,operation_id,suite_key,revision_id,expected_revision,revision,request_fingerprint,verification_status,force_unverified,published_by)
                    VALUES (%s,%s,'custom',%s,%s,%s,'test','unverified',false,%s)""",
                    (tenant, str(uuid.uuid4()), revision.id, ordinal - 1, ordinal, author),
                )

            if allowed:
                insert()
            else:
                with pytest.raises(psycopg.errors.CheckViolation, match="suite_publications_execution_authorized"):
                    insert()
        assert conn.execute("SELECT product.suite_has_no_executable_checker(%s)", (str(uuid.uuid4()),)).fetchone()[0] is False
