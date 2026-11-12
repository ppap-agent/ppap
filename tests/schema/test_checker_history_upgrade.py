"""Real pre-Core data survives the schema-only half of history import."""

import json
import uuid


def test_pre_core_rounds_and_human_reviews_survive_full_upgrade_and_replay(schema_tools):
    schema_tools.require_postgres()
    database = "ppap_history_upgrade_" + uuid.uuid4().hex[:8]
    tenant, user, file, version, rule, round_id, finding = (str(uuid.uuid4()) for _ in range(7))

    def sql(statement):
        result = schema_tools.psql(schema_tools.migrator_user(), schema_tools.migrator_password(), database, "-Atc", statement)
        schema_tools.require(result, "history upgrade SQL")
        return result.stdout.decode().strip()

    try:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()}"),
            "create pre-Core database",
        )
        for path in schema_tools.migration_files():
            if path.name >= "0040":
                break
            schema_tools.require(schema_tools.psql(schema_tools.migrator_user(), schema_tools.migrator_password(), database, "-f", str(path)), "prepare pre-Core schema")
        sql(f"""
            INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES('{tenant}','feishu','history-upgrade','History');
            INSERT INTO product.users(id) VALUES('{user}');
            INSERT INTO product.memberships(user_id,tenant_id,role) VALUES('{user}','{tenant}','member');
            INSERT INTO product.resources(id,tenant_id,kind) VALUES('{file}','{tenant}','file'),('{round_id}','{tenant}','audit_round');
            INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES('{file}','{user}','read');
            INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES('{file}','{tenant}','{user}','old.xlsx');
            INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
                VALUES('{version}','{file}','{tenant}','old.xlsx','application/octet-stream',3,repeat('a',64),'old-object','ready');
            INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES('{rule}','{tenant}','Old rules','{{"rules":[{{"id":"R1"}}]}}');
            INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt)
                VALUES('{round_id}','{tenant}','{user}','original-operation','original-fingerprint','completed',1);
            INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution)
                VALUES('{round_id}','{version}','target','{rule}','Old rules','{{"rules":[{{"id":"R1"}}]}}','{{"old":"source"}}');
            INSERT INTO product.findings(id,round_id,item_key,classification,item,status)
                VALUES('{finding}','{round_id}','R1','old','Original machine judgment','NG');
            INSERT INTO product.reviews(round_id,item_key,user_id,judgment,rationale,version)
                VALUES('{round_id}','R1','{user}','disagree','Independent human says PASS',1);
        """)
        tables = ("audit_rounds", "round_inputs", "findings", "reviews", "resource_grants", "file_versions")

        def read():
            return {
                table: json.loads(sql(f"SELECT jsonb_agg(to_jsonb(t) - 'checker_history' - 'review_document_pairs' - 'review_hint_action' - 'analysis_mode') FROM product.{table} t"))
                for table in tables
            }

        original = read()
        schema_tools.require(schema_tools.apply(database), "upgrade actual pre-Core history")
        assert read() == original
        assert sql("SELECT review_document_pairs FROM product.audit_rounds") == "[]"
        assert sql("SELECT review_hint_action IS NULL FROM product.findings") == "t"
        assert sql("SELECT count(*) FROM product.round_inputs WHERE checker_history IS NOT NULL") == "0"
        assert sql("SELECT count(*) FROM product.checker_execution_snapshots") == "0"
        assert sql("SELECT count(*) FROM product.suite_revisions") == "0"
        schema_tools.require(schema_tools.apply(database), "replay upgraded history schema")
        assert read() == original
        assert sql("SELECT review_document_pairs FROM product.audit_rounds") == "[]"
        assert sql("SELECT review_hint_action IS NULL FROM product.findings") == "t"
    finally:
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
