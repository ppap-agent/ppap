# POC Rule Assets

Applies to `assets/poc测试/poc规则/`. Covers rule-document asset and media path invariants only. Engine behavior and regression live in `tests/corpus` and [#3](https://github.com/ppap-agent/ppap/issues/3). Product entry is the repository root `AGENTS.md`.

## Must follow

- Generated Markdown under `markdown/*.md` should not be hand-edited when the source docx is the authority.
- Preserve repository-relative media links such as `media/10规则-控制计划/media/image*.png`.
- Do not use prompt-benchmark files as source material for document generation rules.

## Layout

- Generated Markdown: `markdown/*.md`
- Extracted media: `markdown/media/`
- Rule issue notes: `issue/*.md`

## Validation

- Markdown image links must use `media/10规则-控制计划/media/image*.png`.
- Media files must live under `assets/poc测试/poc规则/markdown/media/10规则-控制计划/`.
- If generated rule content would change expected behavior, validate with `python -m pytest tests/corpus/v3_poc -q`.
