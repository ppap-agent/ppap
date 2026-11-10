"""Rows that existed before formalization stay byte-identical after 0060."""

import json
import uuid


def test_pre_formalization_rows_survive_0060_and_replay(schema_tools):
    schema_tools.require_postgres()
    database = "ppap_formal_upgrade_" + uuid.uuid4().hex[:8]
    tenant, user, file_id, version, rule, suite, round_id, report = (str(uuid.uuid4()) for _ in range(8))
    digest = "a" * 64
    marker = "PRE-0060-RULE-BYTES"

    def sql(statement):
        result = schema_tools.psql(schema_tools.migrator_user(), schema_tools.migrator_password(), database, "-Atc", statement)
        schema_tools.require(result, "formalization upgrade SQL")
        return result.stdout.decode().strip()

    def read():
        tables = (
            "rule_versions",
            "suite_objects",
            "suite_revisions",
            "round_inputs",
            "checker_execution_snapshots",
            "report_exports",
        )
        return {table: json.loads(sql(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) FROM product.{table} t")) for table in tables}

    try:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()}"),
            "create pre-formalization database",
        )
        for path in schema_tools.migration_files():
            if path.name >= "0060":
                break
            schema_tools.require(schema_tools.psql(schema_tools.migrator_user(), schema_tools.migrator_password(), database, "-f", str(path)), f"prepare {path.name}")
        assert sql("SELECT to_regclass('product.rule_formalizations') IS NULL") == "t"
        sql(f"""
            INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES('{tenant}','feishu','formal-upgrade','Formal');
            INSERT INTO product.users(id) VALUES('{user}');
            INSERT INTO product.resources(id,tenant_id,kind) VALUES('{file_id}','{tenant}','file'),('{round_id}','{tenant}','audit_round');
            INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES('{file_id}','{tenant}','{user}','old.xlsx');
            INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
                VALUES('{version}','{file_id}','{tenant}','old.xlsx','application/octet-stream',3,'{digest}','old-object','ready');
            INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by)
                VALUES('{rule}','{tenant}','Old rules','{{"rules":[{{"id":"c1","rule_to_check":"{marker}"}}]}}','{user}');
            INSERT INTO product.suite_objects(owner_scope,digest,object_key)
                VALUES('{tenant}','{digest}','suites/{tenant}/objects/{digest}');
            INSERT INTO product.suite_revisions(id,tenant_id,suite_key,revision_number,overrides,resolved_digest,created_by)
                VALUES('{suite}','{tenant}','custom:old',1,'{{"rules.json":"{digest}"}}','{"b" * 64}','{user}');
            INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt)
                VALUES('{round_id}','{tenant}','{user}','formal-upgrade-op','formal-upgrade-fp','completed',1);
            INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution)
                VALUES('{round_id}','{version}','target','{rule}','Old rules','{{"rules":[{{"id":"c1","rule_to_check":"{marker}"}}]}}','{{"old":"source"}}');
            INSERT INTO product.checker_execution_snapshots(round_id,tenant_id,input_digest,body)
                VALUES('{round_id}','{tenant}','{"c" * 64}','{{"tenant_id":"{tenant}","round_id":"{round_id}","marker":"{marker}"}}');
            INSERT INTO product.report_exports(id,round_id,tenant_id,created_by,fingerprint,status)
                VALUES('{report}','{round_id}','{tenant}','{user}','{marker}','ready');
        """)
        original = read()
        schema_tools.require(schema_tools.apply(database), "upgrade through formalization schema")
        assert read() == original
        assert sql("SELECT count(*) FROM product.schema_migrations WHERE version='0060_rule_formalizations.sql'") == "1"
        assert sql("SELECT count(*) FROM product.rule_formalizations") == "0"
        assert sql("SELECT count(*) FROM product.rule_formalization_confirmations") == "0"
        assert marker in sql("SELECT body::text FROM product.rule_versions")
        assert marker in sql("SELECT rule_body::text FROM product.round_inputs")
        assert marker in sql("SELECT body::text FROM product.checker_execution_snapshots")
        assert marker in sql("SELECT fingerprint FROM product.report_exports")
        schema_tools.require(schema_tools.apply(database), "replay formalization schema")
        assert read() == original
        assert sql("SELECT count(*) FROM product.rule_formalizations") == "0"
    finally:
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
