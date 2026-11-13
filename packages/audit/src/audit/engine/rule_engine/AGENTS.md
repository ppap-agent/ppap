# Rule Engine

Applies to `packages/audit/src/audit/engine/rule_engine/`. Product entry: repository root `AGENTS.md`.

## Must follow

- Keep `DefaultExcelOps` and fluent APIs minimal.
- Do not keep public APIs with zero call sites.
- Do not add compatibility aliases unless a current caller requires them.
- Fail closed: "unable to verify" is never PASS; missing required fields, columns, anchors or cross-file evidence in a readable file is NG; ERR is only for runtime/system exceptions.
- No file-, company- or sample-specific special cases.

## Regression

Run the owner tests and the corpus runner (see [tests/README](../../../../../../tests/README.md#corpus)):

```sh
uv run --no-sync pytest packages/audit/tests
uv run --no-sync pytest tests/corpus/test_cases.py tests/corpus/regressions --ftype excel -q
```
