"""One formalization generation. A timed-out generation is reported, not silently repeated."""

from __future__ import annotations

import json

import documents.ai
from documents.ai import AIError
from documents.model_access import ModelUnavailable
from product.formalization.text import carried_rejections, chosen_readings, clarified, prior_points, text_prompts
from product.store.formalizations import FormalizationStore, source_binding


def process_rule_formalization(store, claimed, ident: str, *, ai=None) -> dict:
    from worker import audit_lease

    formalizations = FormalizationStore(store)
    row = formalizations.dispatch(claimed, ident)
    if row is None:
        return {"operation_id": claimed.payload.get("operation_id"), "state": "unknown"}
    try:
        rule = store.get_rule(str(row["tenant_id"]), str(row["rule_version_id"]))
        if rule is None:
            formalizations.finish(claimed, ident, error_code="access_denied")
            return {"state": "failed", "failure": "access_denied"}
        clause, digest = source_binding(rule, row["clause_id"])
        if digest != row["source_rule_digest"]:
            formalizations.finish(claimed, ident, error_code="source_changed")
            return {"state": "failed", "failure": "source_changed"}
        if not formalizations.allows(str(row["tenant_id"]), str(row["initiating_user_id"])):
            formalizations.finish(claimed, ident, error_code="access_denied")
            return {"state": "failed", "failure": "access_denied"}
        parent = formalizations.parent_snapshot(row)
        clause_text = clause.get("rule_to_check") or ""
        chosen = chosen_readings(clause_text, clarified(parent, row.get("clarification")))
        # Parts a person rejected stay rejected; the rewrite leaves them out.
        rejected = [part for item in carried_rejections(parent) for part in item.get("parts") or []]
        system, user = text_prompts(clause_text=clause_text, prior_points=prior_points(parent), instruction=row["user_instruction"], chosen=chosen, rejected=rejected)
        with audit_lease(store, claimed, ident):
            # The optimizer reasons at length to keep meaning; the output budget leaves room for that reasoning.
            completed = (ai or documents.ai.run_json)("rule_formalization", instructions=system, prompt=user, max_tokens=32000, reasoning_effort="high", timeout=600)
        # The store validates the reading strictly; it receives the model's JSON as text.
        raw = json.dumps(completed.output, ensure_ascii=False) if completed.output else ""
        usage = completed.usage()
        if not raw:
            formalizations.finish(claimed, ident, error_code="stopped", allow_expired=True)
            return {"operation_id": str(row["operation_id"]), "state": "stopped", "failure": "stopped"}
        saved = formalizations.finish(claimed, ident, raw=raw, usage=usage, allow_expired=True)
        if saved is None:
            return {"operation_id": str(row["operation_id"]), "state": "unknown", "failure": "execution_outcome_unknown"}
        return {"operation_id": str(row["operation_id"]), "state": saved}
    except ModelUnavailable:
        formalizations.finish(claimed, ident, error_code="model_unavailable")
        return {"state": "failed", "failure": "model_unavailable"}
    except AIError as exc:
        if exc.code == "timeout":
            formalizations.finish(claimed, ident, error_code="execution_outcome_unknown", usage_uncertain=True)
            return {"state": "unknown", "failure": "execution_outcome_unknown"}
        failure = "model_unavailable" if exc.code in {"request_failed", "provider_status"} else "formalization_failed"
        formalizations.finish(claimed, ident, error_code=failure)
        return {"state": "failed", "failure": failure}
    except Exception:
        formalizations.finish(claimed, ident, error_code="formalization_failed")
        return {"state": "failed", "failure": "formalization_failed"}
