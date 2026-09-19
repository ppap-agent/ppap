# POC Rule Asset Runbook

This runbook contains low-frequency document regeneration commands for `assets/poc测试/poc规则/`.
Durable invariants stay in `AGENTS.md`.

## Regenerate Control Plan Markdown

Run from the generated Markdown directory so extracted image links remain repository-relative:

```bash
cd /Users/bef0rewind/Projects/work/ppap-agent/assets/poc测试/poc规则/markdown
pandoc '../10规则-控制计划.docx' --wrap=none -t gfm -o '10规则-控制计划.docx.md' --extract-media='media/10规则-控制计划'
```

## Verify Output

- Markdown image links should be `media/10规则-控制计划/media/image*.png`.
- Media files should be under `assets/poc测试/poc规则/markdown/media/10规则-控制计划/`.
- Remove stale generated images that are no longer referenced after regeneration.

If generated rule content changes expected behavior, validate through `../../../ppap_agent/tests/AGENTS.md`.
