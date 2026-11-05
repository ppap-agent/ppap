"""Upgrade preserves explicit pins without inventing an official publication."""

import uuid


def test_previous_schema_upgrade_does_not_publish_existing_sources_or_proofs(schema_tools):
    schema = schema_tools
    schema.require_postgres()
    database = "ppap_official_upgrade_" + uuid.uuid4().hex[:8]
    tenant, revision = str(uuid.uuid4()), str(uuid.uuid4())

    def sql(statement):
        result = schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-Atc", statement)
        schema.require(result, "official publication upgrade SQL")
        return result.stdout.strip().decode()

    try:
        schema.require(schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema.migrator_user()}"), "create upgrade database")
        for migration in schema.migration_files():
            if migration.name >= "0048_official_suite_heads.sql":
                break
            schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "prepare previous schema")
        sql(f"""
            INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES('{tenant}','test','official-upgrade','Upgrade');
            INSERT INTO product.suite_revisions(id,tenant_id,revision_number,suite_key,overrides,resolved_digest)
                VALUES('{revision}',NULL,1,'control-plan','{{}}','{"a" * 64}');
            INSERT INTO product.suite_validations(tenant_id,revision_id,status,revision,suite_digest,proof)
                VALUES(NULL,'{revision}','verified',3,'{"a" * 64}','{{}}');
            INSERT INTO product.tenant_suite_heads(tenant_id,suite_key,current_revision_id,revision)
                VALUES('{tenant}','control-plan','{revision}',7);
        """)
        migration = schema.MIGRATIONS / "0048_official_suite_heads.sql"
        for _ in range(2):
            schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "apply official head migration")
        assert sql("SELECT count(*) FROM product.official_suite_heads") == "0"
        assert sql("SELECT current_revision_id::text || ':' || revision FROM product.tenant_suite_heads") == revision + ":7"
        assert sql("SELECT status || ':' || revision FROM product.suite_validations") == "verified:3"
        assert sql("SELECT count(*) FROM product.schema_migrations WHERE version='0048_official_suite_heads.sql'") == "1"
        for role in ("ppap_api", "ppap_worker"):
            assert sql(f"SELECT has_table_privilege('{role}','product.official_suite_heads','SELECT')") == "t"
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert sql(f"SELECT has_table_privilege('{role}','product.official_suite_heads','{privilege}')") == "f"
    finally:
        schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
