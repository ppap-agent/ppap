# Rule Engine

Applies to `packages/audit/src/audit.engine/rule_engine/`. Parent: [`packages/audit/AGENTS.md`](../../../AGENTS.md). Product entry: repository root `AGENTS.md`.

## Must follow

- Keep `DefaultExcelOps` and fluent APIs minimal.
- Do not keep public APIs with zero call sites.
- Do not add compatibility aliases unless a current caller requires them.
- Preserve fail-closed behavior from the parent engine guidance.

## Regression

```sh
python -m pytest tests/corpus/v2_assets --ftype excel -q
python -m pytest tests/corpus/v3_poc -q
```
