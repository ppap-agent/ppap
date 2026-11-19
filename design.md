---
version: alpha
name: PPAP Web
description: "PRD V3.33 palette: grey-blue neutrals, one blue for commitments, a dark navigation rail, crisp 1px borders and 8px radius for the PPAP web app and Docs Add-on."
colors:
  primary: "#2c6be0"
  on-primary: "#ffffff"
  background: "#f7f8fa"
  surface: "#ffffff"
  on-surface: "#1b2430"
  muted: "#646f82"
  border: "#e9ecf2"
  input-border: "#dde2ea"
  secondary: "#f1f3f7"
  accent: "#eaf1fe"
  success: "#15803d"
  warning: "#b45309"
  error: "#dc2626"
  info: "#2c6be0"
  dark-primary: "#2c6be0"
  dark-on-primary: "#ffffff"
  dark-background: "#0f141b"
  dark-surface: "#161d27"
  dark-on-surface: "#e6eaf0"
  dark-muted: "#98a2b3"
  dark-border: "#2a3442"
  dark-input-border: "#333e4e"
  dark-secondary: "#1f2833"
  dark-accent: "#172540"
  dark-success: "#22c55e"
  dark-warning: "#e8901a"
  dark-error: "#dc2626"
  dark-info: "#6c9bff"
typography:
  label-sm:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 11px
    fontWeight: 600
    lineHeight: 14px
  body-sm:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 12px
    fontWeight: 400
    lineHeight: 16px
  label-md:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 12px
    fontWeight: 500
    lineHeight: 16px
  body-md:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 13px
    fontWeight: 400
    lineHeight: 18px
  body-lg:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 15px
    fontWeight: 400
    lineHeight: 20px
  headline-sm:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 15px
    fontWeight: 600
    lineHeight: 20px
  headline-md:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 18px
    fontWeight: 600
    lineHeight: 24px
  headline-lg:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 22px
    fontWeight: 600
    lineHeight: 28px
  headline-xl:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 28px
    fontWeight: 600
    lineHeight: 36px
rounded:
  control: 0.5rem
components:
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.control}"
  divider:
    backgroundColor: "{colors.border}"
  input-outline:
    backgroundColor: "{colors.input-border}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.control}"
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.control}"
  button-destructive:
    backgroundColor: "{colors.error}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.control}"
  page:
    backgroundColor: "{colors.background}"
    textColor: "{colors.on-surface}"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.control}"
  selected-row:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-surface}"
  caption:
    textColor: "{colors.muted}"
  badge-success:
    textColor: "{colors.success}"
  badge-warning:
    textColor: "{colors.warning}"
  badge-info:
    textColor: "{colors.info}"
  input-dark:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-on-surface}"
    rounded: "{rounded.control}"
  divider-dark:
    backgroundColor: "{colors.dark-border}"
  input-outline-dark:
    backgroundColor: "{colors.dark-input-border}"
  button-primary-dark:
    backgroundColor: "{colors.dark-primary}"
    textColor: "{colors.dark-on-primary}"
    rounded: "{rounded.control}"
  button-secondary-dark:
    backgroundColor: "{colors.dark-secondary}"
    textColor: "{colors.dark-on-surface}"
    rounded: "{rounded.control}"
  button-destructive-dark:
    backgroundColor: "{colors.dark-error}"
    textColor: "{colors.dark-on-primary}"
    rounded: "{rounded.control}"
  page-dark:
    backgroundColor: "{colors.dark-background}"
    textColor: "{colors.dark-on-surface}"
  card-dark:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-on-surface}"
    rounded: "{rounded.control}"
  selected-row-dark:
    backgroundColor: "{colors.dark-accent}"
    textColor: "{colors.dark-on-surface}"
  caption-dark:
    textColor: "{colors.dark-muted}"
  badge-success-dark:
    textColor: "{colors.dark-success}"
  badge-warning-dark:
    textColor: "{colors.dark-warning}"
  badge-info-dark:
    textColor: "{colors.dark-info}"
---

# PPAP Web design guidance

The token frontmatter above is generated from `apps/web/tokens/` by `pnpm design:update` (see
[docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)); do not edit it by hand. The layout and component guidance below
comes from the Stitch design system "Precision Slate"; the colours follow the PRD V3.33 design images. It is
reviewed by people; where an exact value in it differs from
the frontmatter, the frontmatter wins.

## Brand & Style

This design system is engineered for industrial supplier compliance, audit traceability, and precision automotive supply-chain operations. The visual posture is clinical, structured, and unapologetically utilitarian. It strips away decorative whimsy in favor of immediate legibility, high information density, and absolute systemic rigor.

The aesthetic blends Modern Enterprise SaaS with the restrained clarity of modern component architecture (shadcn/ui-inspired). By prioritizing crisp 1px borders, subtle slate fills, and an austere neutral canvas, the interface instills confidence during mission-critical inspections, production gate reviews, and regulatory validation workflows.

## Colors

The palette follows the PRD V3.33 interaction design images (卷一 `_imgbuild`): cool grey-blue neutrals, one blue for commitments and a dark navigation rail. Text colours that the images draw below WCAG AA are darkened to the nearest compliant value of the same hue.

- **Primary (`#2C6BE0`)**: primary interactive commitments, active step indicators, focus rings and the current navigation item. Hover `#2459C2`.
- **Subtle Accents (`#EAF1FE`, `#DCE8FD`, line `#CBDCFB`)**: selection, active tabs, info badges and low-contrast hover states.
- **Neutrals**: text `#1B2430`, secondary text `#3A4657`, muted text `#646F82` (the images' `#8A94A6`, darkened for AA), page `#F7F8FA`, cards `#FFFFFF` with `#E9ECF2` borders, inputs `#DDE2EA`, subtle fills `#F1F3F7`.
- **Navigation rail**: `#1E2733` with `#AEB6C2` items; the current item is solid primary.
- **Semantic Signals**: Success (`#15803D` / `#EAF7EF`), Warning (`#B45309` / `#FEF3E2`, solid `#E8901A` for charts), Destructive/Non-Conformance (`#DC2626` / `#FDECEC`).

## Typography

Typography prioritizes tabular alignment, compact tracking, and vertical rhythm. Each platform's system UI font carries Latin text and digits (SF Pro on Apple devices, Segoe UI on Windows, Roboto on Android), with PingFang SC, Microsoft YaHei or Noto Sans SC for Simplified Chinese; no web font is loaded.

- Use tabular figures (`tnum`) for part serial numbers, torque tolerances, audit timestamps, and percentage indicators.
- Maintain compact scale tiers: `body-md` (13px) acts as the operational standard for dense grids and spec tables, while 11px uppercase labels (`label-sm`) anchor status micro-tags.
- Strict visual hierarchy ensures that metadata headers remain visually subordinate to actual compliance figures.

## Layout & Spacing

The layout is built on a condensed 12-column fluid grid tailored for maximum dashboard efficiency and operational visibility.

- **Breakpoints**: Desktop (≥1280px), Tablet (768px–1279px), Mobile (<768px).
- **Desktop & Workstations**: Layout leverages side-by-side split panes and persistent audit trails. Gutters sit at `1rem`, with tight element clusters using `0.5rem` and `0.75rem`.
- **Vertical Rhythm**: Compact table row heights (36px default, 32px dense) allow high-volume scanning of line items without scroll exhaustion.
- **Reflow Rules**: On viewports below 1024px, secondary inspector panels collapse into sliding drawer sheets; tables shift to horizontally scrollable viewports with frozen primary identifier columns.

## Page Frame

Layout follows what the person is doing on the page; visual form follows this document. Reference images (including the PRD's) explain intent but are not the reason for a layout.

- **Single-action pages** (upload; precheck and rule matching) fold the task list, keep what the decision rests on at the top (title, the one-line verdict, the step line) and the action at the bottom (estimate, deep mode, start). Only the material between them scrolls (`PageLayout` in `apps/web/src/components/page-layout.tsx`), so neither leaves the screen at 1280 × 800.
- **Result pages** keep the task list open for moving between tasks and put their own heading (status, progress, estimate) in the top bar, once.
- **While one file's findings are read**, its table keeps about 1000px. Side columns give way in order of relevance: the task list folds first (it stays open only on windows of about 1664px and wider), then the file list narrows to a strip of file icons with result dots (below about 1432px, or when the task has one file). Each keeps its control to open it again; a choice the person makes wins.
- **Library pages** (files, templates, rules, usage) have no task list.
- The page kinds and their frame regions live in one table, `pageFrames` in `apps/web/src/audit/intake.tsx`; change the table, not scattered conditions.
- Each fact appears once: a count belongs to its section heading; the top verdict says only what the start depends on (files sent, blocked, needing a hand, unmatched).
- A control whose change redoes work says so beside it (changing the rules re-recognizes the whole batch).
- A completed step is shown in the primary colour with ✓, never in green: green means compliant here.
- Narrow windows fold auxiliary areas, never the main action, the verdict or the reasons for a block; nothing tells the person their window is too small.

## Elevation & Depth

This design system avoids heavy blurred drop shadows, utilizing low-contrast ghost outlines and crisp structural borders to construct depth.

- **Borders & Dividers**: Visual separation relies on 1px solid borders (`#E9ECF2` for outer boundaries, `#F1F3F7` for internal cell dividers).
- **Card & Container Elevation**: Static containers use zero shadow, resting flat on `#FFFFFF` against `#F7F8FA` page backdrops. 
- **Interactive Depth**: Dropdowns, popovers, and elevated modal panels use a tight, restrained shadow paired with a perimeter line: `0 1px 3px 0 rgba(27, 36, 48, 0.08), 0 1px 2px -1px rgba(27, 36, 48, 0.08)` with a 1px `#E9ECF2` border.
- **Overlay Layering**: Modal backdrops employ a clean slate scrim: `rgba(27, 36, 48, 0.4)`.

## Shapes

The geometric signature is grounded at a default radius of 8px (`0.5rem`). This strikes a balance between sharp industrial discipline and modern software ergonomics.

- **Inputs, Buttons, and Cards**: Standardize on `rounded` (8px).
- **Badges**: 6px (`0.375rem`) rounded tags, one step inside the 8px controls; not pills, which sit poorly around short Chinese labels.
- **Nested Elements**: Inner nested elements (e.g., input addons, segmented control items) use 6px (`0.375rem`) to ensure concentric alignment with outer 8px containers.

## Components

### Buttons
- **Primary**: Solid `#2C6BE0` fill, `#FFFFFF` text, 8px radius, height 36px (dense: 32px). Hover: `#2459C2`. Focus: 2px offset ring in `#2C6BE0`.
- **Secondary / Outline**: `#FFFFFF` background, 1px `#E9ECF2` border, `#1B2430` text. Hover: `#F7F8FA` background with `#DDE2EA` border.
- **Ghost**: Transparent fill, `#646F82` text. Hover: `#F1F3F7`, text `#1B2430`.

### Badges & Tag Pills
- **Style**: Height 20px, 11px medium font (`label-sm`), normal tracking, 6px rounded tags.
- **Variants**:
  - *Compliant / Pass*: Background `#EAF7EF`, text `#15803D`, border 1px solid `#BBF7D0`.
  - *Review / Warning*: Background `#FEF3E2`, text `#B45309`, border 1px solid `#FDE68A`.
  - *Non-Conformance / Critical*: Background `#FDECEC`, text `#B91C1C`, border 1px solid `#FECACA`.
  - *Neutral / Pending*: Background `#F1F3F7`, text `#3A4657`, border 1px solid `#E9ECF2`.

### Form Inputs & Checkboxes
- **Inputs**: Height 36px, background `#FFFFFF`, border 1px solid `#E9ECF2`, padding 0 12px. Focus: border `#2C6BE0`, box-shadow `0 0 0 1px #2C6BE0`.
- **Checkboxes**: 16px square, 4px radius, border 1px solid `#DDE2EA`. Checked state: `#2C6BE0` background with white checkmark icon.

### Inspection Cards
- Structured 3-tier card layout:
  1. *Header*: 12px top/bottom padding, subtle background `#F7F8FA`, border-bottom 1px `#E9ECF2`, housing part ID, revision code, and status badge.
  2. *Body*: White `#FFFFFF`, standard 16px padding, organized into key-value specification pairs with vertical divider lines.
  3. *Action Footer*: 8px padding, right-aligned micro-actions (Audit, Sign-off, Reject).

### Alert Banners
- Low-saturation, solid tint backgrounds (`#EAF1FE` for info, `#FEF3E2` for notice) with a 1px border. 
- Integrated left indicator rule (3px solid `#2C6BE0` or `#B45309`). Includes inline dismiss action and standard text sizes (13px body).