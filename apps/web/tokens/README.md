# Design tokens

The files here are the only source of PPAP's visual values: colours, font family, type scale and radii.
[design.md](../../../design.md) owns the visual guidance; its token frontmatter and the runtime theme are both
generated from these files by one projection (`scripts/design-tokens.ts`), so they cannot disagree.

```text
tokens/*.tokens.json (DTCG, selected by web.resolver.json)
  ├─ pnpm tokens        → src/generated/tokens.css   runtime theme, not committed
  └─ pnpm design:update → design.md frontmatter      prose kept; reviewed by people
```

Tokenize a reusable visual decision (a semantic colour in light and dark, the type scale, the control and card radius)
only when a real decision needs one. Breakpoints, grid tracks, chart coordinates, measured geometry and motion stay
with the code that owns them.

To change the visual language: edit the tokens, run `pnpm design:update` in `apps/web`, review design.md and the real
UI, and commit both. CI's Design enforcement job runs `design:check`, `lint:css` and `lint`; the lint rules and their
reasons live in `eslint.config.mjs`, `stylelint.config.mjs` and `scripts/stylelint-token-policy.mjs`.

`design.md` at the repository root is the one design document; do not add an uppercase `DESIGN.md` (macOS treats both
names as one file).
