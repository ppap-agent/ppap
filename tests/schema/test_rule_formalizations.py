"""Confirmed formalizations stay immutable, tenant-scoped, and readable after replay."""

import uuid


def test_formalization_constraints_survive_replay(schema_tools):
    schema_tools.require_postgres()
    database = "ppap_formal_" + uuid.uuid4().hex[:8]
    tenant, other, user, rule, candidate, confirmation, operation = (str(uuid.uuid4()) for _ in range(7))

    def execute(statement):
        return schema_tools.psql(
            schema_tools.migrator_user(),
            schema_tools.migrator_password(),
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            statement,
        )

    def sql(statement):
        result = execute(statement)
        schema_tools.require(result, "formalization schema SQL")
        return result.stdout.decode().strip()

    try:
        schema_tools.require(
            schema_tools.psql(
                schema_tools.bootstrap_user(),
                schema_tools.bootstrap_password(),
                "postgres",
                "-c",
                f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()}",
            ),
            "create formalization database",
        )
        schema_tools.require(schema_tools.apply(database), "apply formalization schema")
        sql(f"""
            INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES
              ('{tenant}','feishu','formal-a','A'), ('{other}','feishu','formal-b','B');
            INSERT INTO product.users(id) VALUES('{user}');
            INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by)
              VALUES('{rule}','{tenant}','列','{{"rules":[{{"id":"c1"}},{{"id":"c2"}}]}}','{user}');
            INSERT INTO product.rule_formalizations(
              id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
              owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
              formal_digest,display_digest,created_by)
            VALUES(
              '{candidate}','{tenant}','{rule}','c1',1,repeat('a',64),'owl2-dl-v1','Class: Row',
              '{{}}','[]','[]','[]','complete','每一行都有值','cnl-v1',repeat('b',64),repeat('c',64),'{user}');
            INSERT INTO product.rule_formalization_confirmations(
              id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
              source_rule_digest,display_digest,confirmed_by,current)
            VALUES(
              '{confirmation}','{operation}','{tenant}','{rule}','c1','{candidate}',1,
              repeat('a',64),repeat('c',64),'{user}',true);
        """)
        before_rounds = sql("SELECT count(*) FROM product.audit_rounds")
        before_rules = sql("SELECT count(*) FROM product.rule_versions")
        denied = execute(f"""
            INSERT INTO product.rule_formalizations(
              id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
              owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
              formal_digest,display_digest,created_by)
            VALUES(
              '{uuid.uuid4()}','{other}','{rule}','c1',1,repeat('a',64),'owl2-dl-v1','Class: Row',
              '{{}}','[]','[]','[]','complete','另一租户','cnl-v1',repeat('d',64),repeat('e',64),'{user}')
        """)
        assert denied.returncode != 0
        assert b"formalization_rule_tenant" in denied.stderr
        changed = execute(f"UPDATE product.rule_formalizations SET cnl_text='被改写' WHERE id='{candidate}'")
        assert changed.returncode != 0
        assert b"formalization_immutable" in changed.stderr
        second = execute(f"""
            INSERT INTO product.rule_formalization_confirmations(
              id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
              source_rule_digest,display_digest,confirmed_by,current)
            VALUES(
              '{uuid.uuid4()}','{uuid.uuid4()}','{tenant}','{rule}','c1','{candidate}',1,
              repeat('a',64),repeat('c',64),'{user}',true)
        """)
        assert second.returncode != 0
        assert b"rule_formalization_one_current_confirmation" in second.stderr
        schema_tools.require(schema_tools.apply(database), "replay formalization schema")
        assert sql(f"SELECT cnl_text FROM product.rule_formalizations WHERE id='{candidate}'") == "每一行都有值"
        assert sql(f"SELECT current FROM product.rule_formalization_confirmations WHERE id='{confirmation}'") == "t"
        assert sql("SELECT count(*) FROM product.audit_rounds") == before_rounds
        assert sql("SELECT count(*) FROM product.rule_versions") == before_rules
        assert sql("SELECT count(*) FROM product.schema_migrations WHERE version='0060_rule_formalizations.sql'") == "1"
        duplicate = execute(f"""
            INSERT INTO product.rule_formalizations(
              id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
              owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
              formal_digest,display_digest,created_by)
            VALUES(
              '{uuid.uuid4()}','{tenant}','{rule}','c1',1,repeat('a',64),'owl2-dl-v1','Class: Row',
              '{{}}','[]','[]','[]','complete','重复修订','cnl-v1',repeat('f',64),repeat('1',64),'{user}')
        """)
        assert duplicate.returncode != 0
        assert b"duplicate key" in duplicate.stderr
        missing_clause = execute(f"""
            INSERT INTO product.rule_formalizations(
              id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
              owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
              formal_digest,display_digest,created_by)
            VALUES(
              '{uuid.uuid4()}','{tenant}','{rule}','c9',1,repeat('a',64),'owl2-dl-v1','Class: Row',
              '{{}}','[]','[]','[]','complete','没有的条款','cnl-v1',repeat('2',64),repeat('3',64),'{user}')
        """)
        assert missing_clause.returncode != 0
        assert b"formalization_clause_missing" in missing_clause.stderr
        complete_gap = execute(f"""
            INSERT INTO product.rule_formalizations(
              id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
              owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
              formal_digest,display_digest,created_by)
            VALUES(
              '{uuid.uuid4()}','{tenant}','{rule}','c2',1,repeat('a',64),'owl2-dl-v1','Class: Row',
              '{{}}','[]','[]','[{{"id":"open"}}]','complete','仍有未决','cnl-v1',repeat('4',64),repeat('5',64),'{user}')
        """)
        assert complete_gap.returncode != 0
        assert b"check constraint" in complete_gap.stderr
    finally:
        schema_tools.psql(
            schema_tools.bootstrap_user(),
            schema_tools.bootstrap_password(),
            "postgres",
            "-c",
            f"DROP DATABASE IF EXISTS {database} WITH (FORCE)",
        )
