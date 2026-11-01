# PPAP Excel Audit Engine

Applies to `packages/audit/`. Product entry is the repository root `AGENTS.md`. Engine/corpus parity is owned by [#3](https://github.com/ppap-agent/ppap/issues/3).

## Must follow

- Prefer simple, generic, explainable rule logic.
- Fail closed. Never treat "unable to verify" as PASS.
- Missing required fields, columns, anchors, or cross-file evidence in a readable file is NG, not PASS.
- ERR is only for runtime/system exceptions (parser crash, IO failure, corrupted file).
- Do not add file-specific, company-specific, or sample-specific special cases.

## Regression

```sh
python -m pytest tests/corpus/v2_assets --ftype excel -q
python -m pytest tests/corpus/v3_poc -q
```
