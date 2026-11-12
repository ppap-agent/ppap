"""Customer-offered templates ("补充模板") for the platform team.

The files travel through the ordinary tenant upload pipeline; this records which
ready versions were offered. Operators read `product.template_submissions`.
"""

from __future__ import annotations

import uuid

from product.files import READY

MAX_SUBMISSION_FILES = 50


def _uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def submit_templates(store, principal, request_id, customer, files) -> dict:
    rid = _uuid(request_id)
    if not rid:
        raise ValueError("invalid_template_submission")
    label = customer.strip() if isinstance(customer, str) and customer.strip() else None
    if label is not None and len(label) > 100:
        raise ValueError("invalid_template_submission")
    if not isinstance(files, list) or not 0 < len(files) <= MAX_SUBMISSION_FILES or not all(isinstance(item, dict) for item in files):
        raise ValueError("invalid_template_submission")
    versions: list[str] = []
    for item in files:
        # Tenant and object authorization of every offered version; KeyError when not readable.
        version = store.read_file_version(principal, str(item.get("file_id") or ""), str(item.get("version_id") or ""))
        if version.status != READY:
            raise ValueError("file_not_ready")
        if version.id not in versions:
            versions.append(version.id)
    with store._conn() as conn, conn.transaction():
        created = conn.execute(
            """INSERT INTO product.template_submissions (id, tenant_id, user_id, customer)
            VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING created_at""",
            (rid, principal.tenant_id, principal.user_id, label),
        ).fetchone()
        if created:
            for ordinal, version_id in enumerate(versions):
                conn.execute(
                    "INSERT INTO product.template_submission_files (submission_id, ordinal, file_version_id) VALUES (%s, %s, %s)",
                    (rid, ordinal, version_id),
                )
            created_at = created[0]
        else:
            row = conn.execute(
                "SELECT tenant_id, user_id, customer, created_at FROM product.template_submissions WHERE id = %s",
                (rid,),
            ).fetchone()
            recorded = [
                str(value[0])
                for value in conn.execute(
                    "SELECT file_version_id FROM product.template_submission_files WHERE submission_id = %s ORDER BY ordinal",
                    (rid,),
                ).fetchall()
            ]
            # The same request id is the same submission; anything else is not ours to reveal.
            if not row or (str(row[0]), str(row[1]), row[2], recorded) != (principal.tenant_id, principal.user_id, label, versions):
                raise ValueError("template_submission_conflict")
            created_at = row[3]
    return {"request_id": rid, "state": "received", "file_count": len(versions), "created_at": created_at.isoformat()}
