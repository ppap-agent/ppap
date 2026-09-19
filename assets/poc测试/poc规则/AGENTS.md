# POC Rule Assets

## Scope

- Applies to `assets/poc测试/poc规则/`.
- Covers rule document asset generation and media path invariants only. Regeneration commands live in `RUNBOOK.md`.
- Engine behavior and regression tests live outside this asset directory.

## Must Follow

- Generated Markdown under `markdown/*.md` should not be hand-edited when the source docx is the authority.
- Preserve repository-relative media links such as `media/10规则-控制计划/media/image*.png`.
- Do not use prompt benchmark or backend regression files as source material for document generation rules.

## Task Routing

- Generated Markdown: `markdown/*.md`
- Extracted media: `markdown/media/`
- Rule issue notes: `issue/*.md`
- Regeneration runbook: `RUNBOOK.md`

## Validation

- Markdown image links must use `media/10规则-控制计划/media/image*.png`.
- Media files must live under `assets/poc测试/poc规则/markdown/media/10规则-控制计划/`.
- Remove obsolete generated images when regenerating.
- If generated rule content affects expected behavior, validate through `../../../ppap_agent/tests/AGENTS.md`.

## Related Guidance

- `../../../AGENTS.md`
- `../../../ppap_agent/tests/AGENTS.md`
- `RUNBOOK.md`
