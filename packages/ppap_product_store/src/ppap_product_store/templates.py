"""Immutable template bytes share FileVersion, but never permanent reader grants."""

import json
import uuid

from ppap_core.files import MAX_UPLOAD_BYTES, READY, FileVersion, ObjectExists, original_filename, safe_content_type, sha256_bytes, version_object_key

from ppap_product_store.share_delivery import ShareDeliveryStore


class TemplateShareStore(ShareDeliveryStore):
    def __init__(self, store):
        super().__init__(store, "template_share_request")

    def latest_for_customer(self, principal, customer_id):
        with self.store._conn() as conn:
            row = conn.execute(
                """SELECT id FROM engine.jobs WHERE tenant_id = %s AND kind = 'template_share_request'
                AND payload->>'created_by' = %s AND payload->>'app_id' = %s AND payload->>'organization_id' = %s
                AND payload->'scope'->>'customer_id' = %s ORDER BY created_at DESC, id DESC LIMIT 1""",
                (principal.tenant_id, principal.user_id, principal.app_id, principal.tenant_key, customer_id),
            ).fetchone()
        return str(row[0]) if row else None


def template_file_id(tenant_id: str, customer: str, folder: str, remote_file: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ppap-template:" + json.dumps([tenant_id, customer, folder, remote_file], ensure_ascii=False)))


def template_folder_id(tenant_id: str, customer: str, folder: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([tenant_id, customer, folder])))


def freeze_template(store, principal, file_id: str, provider_revision: str, name: str, content_type: str, body: bytes) -> FileVersion:
    if not body or len(body) > MAX_UPLOAD_BYTES or original_filename(name) != name:
        raise ValueError("invalid_template_file")
    ctype, digest = safe_content_type(content_type), sha256_bytes(body)
    version_id = str(uuid.uuid5(uuid.UUID(file_id), json.dumps([provider_revision, name, ctype, digest], ensure_ascii=False)))
    key = version_object_key(principal.tenant_id, file_id, version_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("template:" + file_id,))
        conn.execute("INSERT INTO product.resources (id, tenant_id, kind) VALUES (%s, %s, 'template') ON CONFLICT DO NOTHING", (file_id, principal.tenant_id))
        resource = conn.execute("SELECT tenant_id, kind FROM product.resources WHERE id = %s", (file_id,)).fetchone()
        if resource is None or str(resource[0]) != principal.tenant_id or resource[1] != "template":
            raise ValueError("template_identity_conflict")
        conn.execute(
            "INSERT INTO product.files (id, tenant_id, created_by, original_filename) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (file_id, principal.tenant_id, principal.user_id, name),
        )
        try:
            store.objects.put_exclusive(key, body, ctype)
        except ObjectExists:
            if sha256_bytes(store.objects.get(key)) != digest:
                raise ValueError("template_cache_corrupt") from None
        conn.execute(
            """INSERT INTO product.file_versions
               (id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status, finalized_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', now()) ON CONFLICT DO NOTHING""",
            (version_id, file_id, principal.tenant_id, name, ctype, len(body), digest, key),
        )
        stored = conn.execute(
            """SELECT id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status
               FROM product.file_versions WHERE id = %s""",
            (version_id,),
        ).fetchone()
        if stored is None or (str(stored[1]), str(stored[2]), *stored[3:]) != (file_id, principal.tenant_id, name, ctype, len(body), digest, key, READY):
            raise ValueError("template_identity_conflict")
    # Intentionally no resource_grants: every reader must recheck Feishu access.
    return FileVersion(version_id, file_id, principal.tenant_id, name, ctype, len(body), key, READY, digest)
