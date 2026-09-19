# PPAP Excel Audit Engine Instructions

## Scope

- Applies to `packages/ppap_excel_audit_engine/`.
- Use this file for rule engine implementation principles, rule operator changes, and engine validation policy.
- API workflow and service runtime guidance live under `../../ppap_agent/AGENTS.md`.

## Must Follow

- KISS first: prefer simple, generic, explainable rule logic.
- Fail closed by default. Never treat "unable to verify" as PASS.
- Missing required fields, columns, anchors, or cross-file evidence in a readable file should return NG, not PASS.
- Use ERR only for runtime/system exceptions such as parser crashes, IO failures, or corrupted files.
- Do not add file-specific, company-specific, or sample-specific special cases.
- Remove deprecated rule interfaces directly when they are intentionally retired; do not keep compatibility shims without a current call site.

## Task Routing

- Rule operator and fluent API work: `src/ppap_excel_audit_engine/rule_engine/AGENTS.md`
- Rule correctness regression: `../../ppap_agent/tests/AGENTS.md`
- Engine-wide unit guards: `../../ppap_agent/tests/unit/test_excel_audit_engine.py`, `../../ppap_agent/tests/unit/test_fail_open_guards.py`, and `../../ppap_agent/tests/unit/test_rule_engine_*.py`

## Commands

```bash
uv run pytest ppap_agent/tests/unit/test_excel_audit_engine.py -q
uv run pytest ppap_agent/tests/unit/test_fail_open_guards.py -q
uv run pytest ppap_agent/tests/unit/test_rule_engine_default_ops.py ppap_agent/tests/unit/test_rule_engine_fluent_table.py -q
```

## Validation

- Every new rule branch must answer: if parsing fails, what is the safe outcome?
- Add regression coverage for parse-failure paths so new code cannot fail open.
- If rule text conflicts with manual labels or samples, keep strict rule semantics and record the conflict in the report instead of silently weakening implementation.
- For behavior-impacting changes, also run the relevant V2 or V3 regression listed in `../../ppap_agent/tests/AGENTS.md`.

## Related Guidance

- `../../AGENTS.md`
- `src/ppap_excel_audit_engine/rule_engine/AGENTS.md`
- `../../ppap_agent/tests/AGENTS.md`
