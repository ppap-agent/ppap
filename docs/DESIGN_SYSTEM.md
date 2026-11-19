# Design system

## Authority

This document owns how PPAP's visual decisions are stored, generated and checked.
[design.md](../design.md) owns visual guidance, the current confirmed design and
[PRD/IXD](../AGENTS.md#前端) own page meaning and copy, and CI owns the evidence.
The procedure for designing and changing pages is the
[product-design-loop](../.claude/skills/product-design-loop/SKILL.md) skill.

```text
apps/web/tokens/*.tokens.json   DTCG 2025.10, the only machine source
  ├─ Terrazzo → apps/web/src/generated/tokens.css   runtime theme (generated, not committed)
  └─ projection → design.md frontmatter              agent-facing (generated; prose reviewed)

runtime theme → src/index.css (Tailwind @theme) → src/components/ui → pages
```

`apps/web/tokens/web.resolver.json` selects the inputs: `shared.tokens.json` (font family,
control radius), `light.tokens.json` (colours) and the `dark` theme context
(`dark.tokens.json`). One implementation, `apps/web/scripts/design-tokens.ts`, produces both
the runtime theme and the design.md frontmatter, so they cannot disagree.

`design.md` at the repository root is the single design document. “DESIGN.md” elsewhere
names the external format; do not add an uppercase copy (macOS treats both names as one
file).

## Commands (in `apps/web`)

| Command | What it does |
|---|---|
| `pnpm tokens` | Writes the runtime theme. Vite also writes it when it starts (dev, preview, build). |
| `pnpm design:update` | Regenerates the design.md frontmatter from tokens and keeps the prose. Review and commit tokens and design.md together. |
| `pnpm design:check` | Fails when design.md differs from the projection; writes nothing. |
| `pnpm lint:css` | Stylelint: authored CSS uses tokens (below). |
| `pnpm lint` | ESLint: page classes and styles use tokens (`@shadcn/lint`). |

CI runs `design:check`, `lint:css` and `lint` as the required **Design enforcement** job. Design
consistency is checked on its own, not repaired as a side effect of building.

## Token boundary

Tokenize reusable design decisions: semantic colours (light and dark resolve the same roles),
the font family, the dense font size and the control radius. Add a token only when a real decision needs one; do
not pre-build layers or brands.

Keep content-driven breakpoints, grid tracks, chart coordinates, percentages and measured
runtime geometry with the code that owns them. Motion (`--animate-*`) stays in `index.css`.

## Source policy

- Design-bearing CSS values use generated tokens: no hex colours (`color-no-hex`), no
  redeclared token, no custom property that hides a raw colour, shape or elevation
  (`ppap/token-policy`, `apps/web/scripts/stylelint-token-policy.mjs`).
- Pages style through classes and data hooks, never ID selectors.
- Page TSX uses token classes: no arbitrary values, inline visual styles or raw colours
  (`@shadcn/lint`: `no-arbitrary-values`, `no-inline-styles`, `no-raw-colors`, `no-restyle`).
- Generated runtime theme and design.md frontmatter are never edited by hand.

Primitives in `src/components/ui` own appearance, state and interaction; pages bind data and
operations and do not redefine tokens or reach into primitives to repair appearance.

Primitives are linted for tokens as well, because that is where most visual decisions live; a
hard-coded value there would survive a token change. Their rules differ from the page rules:

- No arbitrary colour, typography, spacing, shape or effects values; use a token or the
  Tailwind scale. A decision that has no token yet (a hover colour, a dense font size) gets one.
- Layout and motion values are allowed (a transition property list is not a visual decision).
- One primitive composing another is not a page restyling it, so `no-restyle` does not apply.
- Inline styles may carry runtime geometry only (`transform`).
- Named exceptions, each with its reason in `apps/web/eslint.config.mjs`: `bg-[Canvas]` /
  `text-[CanvasText]` (system colours for native `<option>` lists) and `rounded-[inherit]`.

Icons are Font Awesome Free solid icons (CC BY 4.0) drawn by `src/components/ui/icon.tsx`
(`<Icon icon={faXmark}/>`); there is no second icon set. An icon is drawn in currentColor at
1em, so pages may size, tone and animate it with token classes; its shape stays in the primitive.

## Changing the visual language

Edit `apps/web/tokens/*.tokens.json`, run `pnpm design:update`, review design.md (frontmatter
and prose together) and the real UI, then commit both. The tests in
`apps/web/scripts/design.test.ts` keep the projection, the document check and the lint rules
honest.
