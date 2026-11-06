"""An idempotent metadata upgrade cannot move existing personal credentials."""

import uuid


def test_endpoint_upgrade_preserves_ciphertext_and_generates_stable_configuration_identity(schema_tools):
    schema = schema_tools
    schema.require_postgres()
    database = "ppap_provider_upgrade_" + uuid.uuid4().hex[:8]
    tenant, user = (str(uuid.uuid4()) for _ in range(2))

    def sql(statement):
        result = schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-Atc", statement)
        schema.require(result, "personal model upgrade SQL")
        return result.stdout.strip().decode()

    try:
        schema.require(
            schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema.migrator_user()}"), "create model upgrade database"
        )
        for path in schema.migration_files():
            if path.name >= "0054":
                break
            schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(path)), "prepare previous schema")
        sql(f"""
            INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES('{tenant}','test','provider-upgrade','Upgrade');
            INSERT INTO product.users(id) VALUES('{user}');
            INSERT INTO product.memberships(tenant_id,user_id,role) VALUES('{tenant}','{user}','member');
            INSERT INTO product.personal_model_credentials(tenant_id,user_id,key_ciphertext) VALUES('{tenant}','{user}','opaque-legacy-ciphertext');
        """)
        migration = schema.MIGRATIONS / "0054_personal_model_endpoints.sql"
        schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "upgrade model metadata")
        query = "SELECT provider || ':' || model || ':' || base_url || ':' || revision::text || ':' || key_ciphertext FROM product.personal_model_credentials"
        before = sql(query)
        assert before.startswith("deepseek:deepseek-flash:https://api.deepseek.com:")
        assert before.endswith(":opaque-legacy-ciphertext")
        schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "repeat model metadata upgrade")
        assert sql(query) == before
        sql("UPDATE product.personal_model_credentials SET provider='responses-compatible',model='gpt-6.1-sol',base_url='https://models.company.test/v1'")
        assert sql("SELECT has_table_privilege('ppap_worker','product.personal_model_credentials','UPDATE')") == "f"
        assert sql("SELECT has_table_privilege('ppap_worker','product.personal_model_credentials','SELECT')") == "t"
        assert sql("SELECT count(*) FROM product.schema_migrations WHERE version='0054_personal_model_endpoints.sql'") == "1"
    finally:
        schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
