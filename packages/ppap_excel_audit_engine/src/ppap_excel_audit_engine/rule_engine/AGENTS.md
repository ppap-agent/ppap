# Rule Engine Instructions

## Scope

- Applies to `packages/ppap_excel_audit_engine/src/ppap_excel_audit_engine/rule_engine/`.
- Parent engine rules in `../../../AGENTS.md` still apply.

## Must Follow

- Keep `DefaultExcelOps` and fluent APIs minimal.
- Do not keep public APIs with zero call sites.
- Do not add compatibility aliases unless a current caller requires them.
- Preserve fail-closed behavior from the parent engine guidance.

## Validation

```bash
uv run pytest ppap_agent/tests/unit/test_rule_engine_default_ops.py ppap_agent/tests/unit/test_rule_engine_fluent_table.py ppap_agent/tests/unit/test_rule_engine_layering_guard.py -q
```
