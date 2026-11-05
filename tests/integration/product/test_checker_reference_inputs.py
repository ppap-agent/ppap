"""Reference classification is a frozen authorized fact, not a rule binding."""

import pytest
from product.settings import Settings
from product.store.suites import freeze_authorized_inputs


def test_reference_without_rule_keeps_confirmed_document_type(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="frozen-reference"))
    principal = store.upsert_feishu_identity("frozen-reference", "owner")
    version = store.create_staging(principal, "unhelpful-name.xlsx", "application/octet-stream", 3)
    version = store.finalize_version(store.put_blob(version, b"raw"))
    with store._conn() as conn:
        prepared = store._bound_audit_items(conn, principal, [{"file_version_id": version.id, "role": "reference"}])
        assert prepared[0]["rule_body"] is None
        prepared[0]["document_type"] = "过程流程图"  # The server's confirmed recognition result.
        frozen = freeze_authorized_inputs(conn, principal, prepared)
        assert frozen[0].document_type == "过程流程图"
        assert frozen[0].role == "reference"
        assert frozen[0].rules is None
        prepared[0]["document_type"] = None
        assert freeze_authorized_inputs(conn, principal, prepared)[0].document_type is None
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (version.file_id, principal.user_id))
        with pytest.raises(KeyError, match="snapshot_file_missing"):
            freeze_authorized_inputs(conn, principal, prepared)
