"""Freeze preparation against the same adopted Suite as formal acceptance."""

from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, runtime_revision
from audit.standard_suites import library_revision

from product.prechecks import frozen_precheck_input
from product.standard_policy import config_digest
from product.store.suites import freeze_selected_targets


def preparation_runtime_revision(settings):
    # Preparation grants no model capability. Freeze the applicable host policy,
    # including isolation, without inventing a paid Deep model configuration.
    policy = {"execution": settings.standard_execution_policy().model_dump(mode="json"), "sandbox_profile": settings.deep_sandbox_profile}
    return runtime_revision("precheck:" + config_digest(policy), library_revision())


def freeze_task_precheck(store, conn, principal, task):
    targets = {}
    for row in task["files"]:
        match = row.get("match") or {}
        if row["selection"] != "selected" or match.get("state") != "matched" or row.get("reference_only"):
            continue
        prepared = [{"role": "target", "file_version_id": row["file"]["id"], "rule_body": (match.get("rule") or {}).get("body") or {}}]
        try:
            (target,) = freeze_selected_targets(store, conn, principal.tenant_id, prepared, preparation=True)
            targets[target.file_version_id] = target
        except (KeyError, ValueError):
            # Missing/invalid adopted source is a visible preparation gap. Never
            # import a legacy checker or make recognition disappear on this path.
            pass
    payload = frozen_precheck_input(task, principal.tenant_id, store.settings.standard_execution_policy().model_dump(mode="json"), targets=targets, initiating_user_id=principal.user_id)
    payload["runtime_revision"] = preparation_runtime_revision(store.settings)
    payload["library_revision"] = library_revision()
    return payload


def precheck_snapshot(payload):
    """Build only from persisted task inputs. Does not resolve the current head."""
    targets = tuple(FrozenTarget(**{**row["suite_target"], "binding": RuleBinding(**row["suite_target"]["binding"])}) for row in payload["files"] if row.get("suite_target"))
    if not targets:
        return None
    target_ids = {target.file_version_id for target in targets}
    inputs = []
    for row in payload["files"]:
        if row["selection"] != "selected":
            continue
        file, metadata = row["file"], row.get("metadata") or {}
        customers = ((row.get("rule") or {}).get("body") or {}).get("customers") or []
        inputs.append(
            FrozenInput(
                file["id"],
                file["sha256"],
                "target" if file["id"] in target_ids else "reference",
                file["original_filename"],
                row.get("document_type"),
                file["file_id"],
                metadata.get("customer") or (customers[0] if len(customers) == 1 else None),
                metadata.get("model"),
                metadata.get("part"),
            )
        )
    return ExecutionSnapshot(payload["tenant_id"], payload["initiating_user_id"], tuple(inputs), targets, payload["runtime_revision"], payload["library_revision"], {}, round_id="")


def precheck_suites_current(store, conn, payload):
    """A later publication cannot present an old preparation as current."""
    from product.store.suite_releases import current_selection
    from product.store.suites import SuiteStore

    if payload.get("library_revision") != library_revision():
        return False
    suites = SuiteStore(store.settings, store.objects, store.role)
    for row in payload["files"]:
        target = row.get("suite_target")
        if target is None:
            continue
        try:
            selection = current_selection(conn, payload["tenant_id"], target["suite_key"])
            if selection["revision_id"] != target["suite_revision_id"] or selection["resolved_digest"] != target["suite_digest"]:
                return False
            related = {key: suites.get_revision(identifier, payload["tenant_id"], conn=conn) for key, identifier in selection["related_revision_ids"].items()}
            if any(revision is None for revision in related.values()) or {key: revision.resolved_digest for key, revision in related.items()} != target["related_suite_digests"]:
                return False
        except (KeyError, ValueError):
            return False
    return True
