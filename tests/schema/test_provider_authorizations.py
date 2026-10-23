"""Upgrade real legacy credentials; repeated migrations do not revive old copies."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from ppap_application import build_product_store
from ppap_core.db import connect
from ppap_core.fixtures import APP_ID
from ppap_core.identity import ProviderAuthError, token_hash
from ppap_core.settings import Settings
from psycopg.errors import InsufficientPrivilege


def test_provider_credential_upgrade_preserves_exact_bindings_and_unknown_outcomes(schema_tools, memory_objects_type):
    schema = schema_tools
    schema.require_postgres()
    database = "ppap_provider_" + uuid4().hex[:12]
    schema.require(schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema.migrator_user()}"), "create provider upgrade DB")
    try:
        for migration in schema.migration_files():
            if migration.name >= "0035":
                break
            schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "apply prior provider schema")
        settings = Settings(
            feishu_app_id=APP_ID,
            pg_database=database,
            pg_host=os.environ.get("PGHOST", "127.0.0.1"),
            pg_port=int(os.environ.get("PGPORT", "5432")),
            pg_migrator_user=schema.migrator_user(),
            pg_migrator_password=schema.migrator_password(),
        )
        store = build_product_store(settings, objects=memory_objects_type(), role="migrator")
        store.seed_isolated_dev()
        people = {who: store.principal_for_fixture(who) for who in ("alice", "bob", "carol")}
        with store._conn() as conn:
            for who, p in people.items():
                for n in range(2):
                    conn.execute(
                        """INSERT INTO product.sessions(token_hash,user_id,tenant_id,expires_at,access_token,refresh_token,access_expires_at,refresh_expires_at)
                                 VALUES(%s,%s,%s,now()+interval '1 hour',%s,%s,now()+interval '1 hour',now()+interval '1 day')""",
                        (token_hash(who + str(n)), p.user_id, p.tenant_id, "access-" + who, "refresh-" + who),
                    )
            p = people["bob"]
            conn.execute(
                """INSERT INTO product.identities(id,user_id,provider,app_id,tenant_key,subject)
                         VALUES(gen_random_uuid(),%s,'feishu','other-app',%s,'other-subject')""",
                (p.user_id, p.tenant_key),
            )
            conn.execute("INSERT INTO product.mcp_clients(id,metadata) VALUES ('legacy','{}')")
            for who in ("alice", "carol"):
                p = people[who]
                conn.execute(
                    """INSERT INTO product.mcp_grants(client_id,user_id,tenant_id,provider,app_id,subject,scopes,resource,
                             access_token,refresh_token,expires_at,provider_refresh_state)
                             VALUES('legacy',%s,%s,'feishu',%s,%s,'{identity:read}','https://example.test/mcp',%s,%s,now()+interval '1 day',%s)""",
                    (p.user_id, p.tenant_id, p.app_id, p.subject, "access-" + who, "refresh-" + who, "ready" if who == "alice" else "refreshing"),
                )
        migration = Path(__file__).resolve().parents[2] / "migrations" / "0035_provider_authorizations.sql"
        schema.require(schema.psql(schema.migrator_user(), schema.migrator_password(), database, "-f", str(migration)), "upgrade provider credentials")
        # Same browser credentials share one owner; ambiguous old identity fails closed.
        alice = store.load_session("alice0")
        assert alice.provider_authorization_id == store.load_session("alice1").provider_authorization_id
        assert alice.access_token == "access-alice"
        assert store.load_session("bob0") is None
        with pytest.raises(ProviderAuthError, match="provider_reauthorization_required"):
            store.fresh_provider_session(store.load_session("carol0"), lambda _: pytest.fail("unknown legacy token was replayed"))
        with store._conn() as conn:
            (granted,) = conn.execute("SELECT provider_authorization_id FROM product.mcp_grants WHERE subject=%s", (people["alice"].subject,)).fetchone()
            assert str(granted) == alice.provider_authorization_id
            assert conn.execute("SELECT revoked_at IS NOT NULL FROM product.mcp_grants WHERE subject=%s", (people["carol"].subject,)).fetchone()[0]
            assert conn.execute("SELECT count(*) FROM product.sessions WHERE access_token IS NOT NULL OR refresh_token IS NOT NULL").fetchone()[0] == 0
        schema.require(schema.apply(database), "reapply complete schema")
        assert store.load_session("alice0").provider_authorization_id == alice.provider_authorization_id
        with connect(settings, "worker") as conn, pytest.raises(InsufficientPrivilege):
            conn.execute("SELECT * FROM product.provider_authorizations")
        with connect(settings, "api") as conn:
            assert conn.execute("SELECT count(*) FROM product.provider_authorizations").fetchone()[0] == 2
    finally:
        schema.require(schema.psql(schema.bootstrap_user(), schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"), "drop provider upgrade DB")
