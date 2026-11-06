"""Upgrade receipts and one-time notices survive idempotent schema application."""

import uuid


def test_official_upgrade_migration_preserves_pins_receipts_and_least_privilege(schema_tools):
    schema = schema_tools
    schema.require_postgres()
    database = "ppap_suite_upgrade_" + uuid.uuid4().hex[:8]
    tenant, user, official, source, candidate, operation = (str(uuid.uuid4()) for _ in range(6))

    def sql(statement):
        result = schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-Atc", statement)
        schema.require(result, "official upgrade schema SQL")
        return result.stdout.strip().decode()

    try:
        schema.require(schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema.migrator_user()}"), "create upgrade database")
        for migration in schema.migration_files():
            if migration.name >= "0053_suite_official_upgrades.sql":
                break
            schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "prepare previous schema")
        sql(f"""
            INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES('{tenant}','test','suite-upgrade','Upgrade');
            INSERT INTO product.users(id) VALUES('{user}');
            INSERT INTO product.memberships(tenant_id,user_id,role) VALUES('{tenant}','{user}','member');
            INSERT INTO product.suite_revisions(id,tenant_id,revision_number,suite_key,base_revision_id,overrides,resolved_digest,created_by) VALUES
                ('{official}',NULL,1,'cp',NULL,'{{}}','{"a" * 64}',NULL),
                ('{source}','{tenant}',1,'cp','{official}','{{}}','{"b" * 64}','{user}'),
                ('{candidate}','{tenant}',2,'cp','{official}','{{}}','{"c" * 64}','{user}');
            INSERT INTO product.tenant_suite_heads(tenant_id,suite_key,current_revision_id,revision)
                VALUES('{tenant}','cp','{source}',7);
        """)
        migration = schema.MIGRATIONS / "0053_suite_official_upgrades.sql"
        schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "upgrade schema")
        sql(f"""
            INSERT INTO product.suite_upgrade_operations(tenant_id,operation_id,suite_key,source_revision_id,target_revision_id,revision_id,expected_revision,request_fingerprint,created_by)
                VALUES('{tenant}','{operation}','cp','{source}','{official}','{candidate}',7,'{"a" * 64}','{user}');
            INSERT INTO product.suite_official_update_views(tenant_id,user_id,suite_key,official_revision_id)
                VALUES('{tenant}','{user}','cp','{official}');
        """)
        for _ in range(2):
            schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "repeat migration")
        assert sql("SELECT current_revision_id::text || ':' || revision FROM product.tenant_suite_heads") == source + ":7"
        assert sql("SELECT revision_id::text FROM product.suite_upgrade_operations") == candidate
        assert sql("SELECT count(*) FROM product.suite_official_update_views") == "1"
        assert sql("SELECT count(*) FROM product.suite_validations") == "0"
        assert sql("SELECT count(*) FROM product.schema_migrations WHERE version='0053_suite_official_upgrades.sql'") == "1"
        for table in ("suite_upgrade_operations", "suite_official_update_views"):
            for privilege in ("SELECT", "INSERT"):
                assert sql(f"SELECT has_table_privilege('ppap_api','product.{table}','{privilege}')") == "t"
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert sql(f"SELECT has_table_privilege('ppap_api','product.{table}','{privilege}')") == "f"
        assert sql("SELECT has_table_privilege('ppap_worker','product.suite_upgrade_operations','INSERT')") == "f"
    finally:
        schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
