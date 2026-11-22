# POC Rule Assets

Applies to `assets/nio/history/poc规则/`. Covers rule-document asset and media path invariants only. Engine behavior and regression live in `tests/corpus`. Product entry is the repository root `AGENTS.md`.

## Must follow

- Generated Markdown under `markdown/*.md` should not be hand-edited when the source docx is the authority.
- Preserve repository-relative media links such as `media/10规则-控制计划/media/image*.png`.
- Do not use prompt-benchmark files as source material for document generation rules.

## Layout

- Generated Markdown: `markdown/*.md`
- Extracted media: `markdown/media/`
- Rule issue notes: `issue/*.md`

## Regenerate

The source DOCX is the content authority. Only when the assets need regenerating, run Pandoc from the output directory so image links stay `media/10规则-控制计划/media/image*.png`:

```sh
cd assets/nio/history/poc规则/markdown
pandoc '../10规则-控制计划.docx' --wrap=none -t gfm -o '10规则-控制计划.docx.md' --extract-media='media/10规则-控制计划'
```

Afterwards check the diff, every image reference and its file. Remove only old generated images confirmed to be unreferenced; never hand-edit rule text. A successful export does not mean an audit passes.

## Validation

- Markdown image links must use `media/10规则-控制计划/media/image*.png`.
- Media files must live under `assets/nio/history/poc规则/markdown/media/10规则-控制计划/`.
- If generated rule content would change expected behavior, validate as described in [Corpus tests](../../../../tests/README.md#corpus).
