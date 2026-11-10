"""One formalization generation. Unknown provider outcome is not another charge."""

from __future__ import annotations

from audit.engine.checkers.client import ModelTransportError, TextCompletion
from documents.model_access import ModelUnavailable
from product.formalization.propose import generation_prompts
from product.store.formalizations import FormalizationStore, source_binding


def _provider_usage(result: TextCompletion | None) -> dict | None:
    """Copy known token counts only. Never copy credentials or free-form provider text."""
    if not isinstance(result, TextCompletion):
        return None
    counts: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "thinking_tokens"):
        value = getattr(result, key)
        if type(value) is int and value >= 0:
            counts[key] = value
    if "input_tokens" not in counts or "output_tokens" not in counts:
        return None
    if counts.get("thinking_tokens", 0) > counts["output_tokens"]:
        counts.pop("thinking_tokens", None)
    usage: dict[str, int | str] = {**counts, "source": "provider"}
    return usage


def _unknown(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name or "transport" in name


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
        system, user = generation_prompts(
            clause_text=clause.get("rule_to_check") or "",
            source_rule_version_id=str(row["rule_version_id"]),
            clause_id=row["clause_id"],
            scope={"customers": (rule.get("body") or {}).get("customers") or [], "parts": (rule.get("body") or {}).get("parts") or []},
            prior=formalizations.parent_snapshot(row),
            clarification=row["clarification"] or None,
            instruction=row["user_instruction"],
        )
        client = ai
        if client is None:
            from audit.engine.checkers.client import AIClient

            client = AIClient()
        with audit_lease(store, claimed, ident):
            completed = client.structured_completion(
                system_prompt=system,
                user_prompt=user,
                max_tokens=4000,
                temperature=0,
                json_format=True,
                operation="rule_formalization",
                retry_limit=0,
            )
        raw = completed.text if isinstance(completed, TextCompletion) else completed
        usage = _provider_usage(completed if isinstance(completed, TextCompletion) else None)
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
    except ModelTransportError as exc:
        failure = "model_unavailable" if exc.code == "request_failed" else "formalization_failed"
        formalizations.finish(claimed, ident, error_code=failure)
        return {"state": "failed", "failure": failure}
    except Exception as exc:
        if _unknown(exc):
            formalizations.finish(claimed, ident, error_code="execution_outcome_unknown", usage_uncertain=True)
            return {"state": "unknown", "failure": "execution_outcome_unknown"}
        formalizations.finish(claimed, ident, error_code="formalization_failed")
        return {"state": "failed", "failure": "formalization_failed"}
