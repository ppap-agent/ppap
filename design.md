---
version: alpha
name: PPAP Web
description: "Feishu taste: light grey canvas, white cards with 12px corners, Feishu blue for commitments, a light navigation, faint 1px dividers and 6px controls for the PPAP web app and Docs Add-on."
colors:
  primary: "#1456f0"
  on-primary: "#ffffff"
  background: "#f5f6f7"
  surface: "#ffffff"
  on-surface: "#1f2329"
  muted: "#646a73"
  border: "#e4e6e9"
  input-border: "#dee0e3"
  secondary: "#f2f3f5"
  accent: "#e1eaff"
  success: "#15803d"
  warning: "#b45309"
  error: "#dc2626"
  info: "#1456f0"
  dark-primary: "#2f66e8"
  dark-on-primary: "#ffffff"
  dark-background: "#1a1a1a"
  dark-surface: "#262626"
  dark-on-surface: "#ebebeb"
  dark-muted: "#a6a6a6"
  dark-border: "#333333"
  dark-input-border: "#3d3d3d"
  dark-secondary: "#2e2e2e"
  dark-accent: "#1f2d4d"
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
  card: 0.75rem
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
    rounded: "{rounded.card}"
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
    rounded: "{rounded.card}"
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
comes from the Stitch design system "Precision Slate"; the colours and shapes follow the taste of Feishu's own apps. It is
reviewed by people; where an exact value in it differs from
the frontmatter, the frontmatter wins.

## Brand & Style

This design system is engineered for industrial supplier compliance, audit traceability, and precision automotive supply-chain operations. The visual posture is clinical, structured, and unapologetically utilitarian. It strips away decorative whimsy in favor of immediate legibility, high information density, and absolute systemic rigor.

The aesthetic follows the quiet, native taste of Feishu's own apps on a shadcn/ui component base: a light grey page, white cards with generous corners, faint dividers instead of boxes, and one Feishu blue for commitments. The interface should feel like part of Feishu, so attention stays on the inspection data, production gate reviews and validation workflows.

## Colors

The palette follows Feishu's own apps (工作台, 多维表格, 视频会议), so PPAP sits quietly inside the Feishu window: a light grey page, white cards, Feishu blue for commitments. Text colours stay at or above WCAG AA.

- **Primary (`#1456F0`)**: primary interactive commitments, active step indicators, focus rings and the current navigation item. Hover `#0F46C9`.
- **Subtle Accents (`#E1EAFF`, `#D1DEFF`, line `#BACEFD`)**: selection, the current navigation item, info badges and low-contrast hover states.
- **Neutrals**: text `#1F2329`, muted text `#646A73`, page `#F5F6F7`, cards `#FFFFFF` with faint `#E4E6E9` dividers, inputs `#DEE0E3`, subtle fills `#F2F3F5`, hover and selected pills `#E5E6EB`.
- **Navigation**: white with `#646A73` items; the current item is `#E1EAFF` with primary text.
- **Semantic Signals**: Success (`#15803D` / `#EAF7EF`), Warning (`#B45309` / `#FEF3E2`, solid `#E8901A` for charts), Destructive/Non-Conformance (`#DC2626` / `#FDECEC`). They stay darker than Feishu's own status colours so their text keeps AA contrast.
- **Dark**: follows Feishu's dark theme: page `#1A1A1A`, cards `#262626`, text `#EBEBEB`, primary `#2F66E8` (white labels keep AA).

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
- **Two shells, one page**: in a browser the frame has the left navigation, the page heading and the account menu; inside the Feishu client (detected from the user agent or the Feishu web SDK) the five entries sit in a top bar as pills and the account menu is left to Feishu; the window title stays blank, since the top bar already names the app and the current page. Tokens, components and page content are shared; only the frame differs.
- The page kinds and their frame regions live in one table, `pageFrames` in `apps/web/src/audit/intake.tsx`; change the table, not scattered conditions.
- Each fact appears once: a count belongs to its section heading; the top verdict says only what the start depends on (files sent, blocked, needing a hand, unmatched).
- A control whose change redoes work says so beside it (changing the rules re-recognizes the whole batch).
- A completed step is shown in the primary colour with ✓, never in green: green means compliant here.
- Narrow windows fold auxiliary areas, never the main action, the verdict or the reasons for a block; nothing tells the person their window is too small.

## Elevation & Depth

Depth comes from the page tone, not from shadows or heavy lines.

- **Borders & Dividers**: faint 1px `#E4E6E9` lines separate table rows and sections; tables draw no vertical lines.
- **Card & Container Elevation**: static containers use zero shadow, resting flat on `#FFFFFF` against the `#F5F6F7` page.
- **Interactive Depth**: dropdowns, popovers and modal panels use a tight, restrained shadow with a 1px `#E4E6E9` border.
- **Overlay Layering**: modal backdrops use a dark scrim at about 40%.

## Shapes

- **Cards, panels and dialogs**: 12px (`rounded.card`), Tailwind `rounded-lg`.
- **Buttons, inputs, menus, tabs and navigation pills**: 6px, Tailwind `rounded-md`, derived from `rounded.control` (8px).
- **Badges**: 6px rounded tags; not pills, which sit poorly around short Chinese labels.
- **Nested Elements**: inner elements step down to 4px (`rounded-sm`).

## Components

### Buttons
- **Primary**: Solid `#1456F0` fill, `#FFFFFF` text, 6px radius, height 36px (dense: 32px). Hover: `#0F46C9`. Focus: 2px offset ring in `#1456F0`.
- **Secondary / Outline**: `#FFFFFF` background, 1px `#DEE0E3` border, `#1F2329` text. Hover: `#F2F3F5` background.
- **Ghost**: Transparent fill, `#646A73` text. Hover: `#F2F3F5`, text `#1F2329`.

### Badges & Tag Pills
- **Style**: Height 20px, 11px medium font (`label-sm`), normal tracking, 6px rounded tags.
- **Variants**:
  - *Compliant / Pass*: Background `#EAF7EF`, text `#15803D`, border 1px solid `#BBF7D0`.
  - *Review / Warning*: Background `#FEF3E2`, text `#B45309`, border 1px solid `#FDE68A`.
  - *Non-Conformance / Critical*: Background `#FDECEC`, text `#B91C1C`, border 1px solid `#FECACA`.
  - *Neutral / Pending*: Background `#F2F3F5`, text `#646A73`, border 1px solid `#E4E6E9`.

### Form Inputs & Checkboxes
- **Inputs**: Height 36px, background `#FFFFFF`, border 1px solid `#DEE0E3`, padding 0 12px, 6px radius. Focus: border `#1456F0`, box-shadow `0 0 0 1px #1456F0`.
- **Checkboxes**: 16px square, 4px radius, border 1px solid `#DEE0E3`. Checked state: `#1456F0` background with white checkmark icon.

### Inspection Cards
- Structured 3-tier card layout:
  1. *Header*: 12px top/bottom padding, subtle background `#F5F6F7`, border-bottom 1px `#E4E6E9`, housing part ID, revision code, and status badge.
  2. *Body*: White `#FFFFFF`, standard 16px padding, organized into key-value specification pairs with vertical divider lines.
  3. *Action Footer*: 8px padding, right-aligned micro-actions (Audit, Sign-off, Reject).

### Alert Banners
- Low-saturation, solid tint backgrounds (`#E1EAFF` for info, `#FEF3E2` for notice) with a 1px border. 
- Integrated left indicator rule (3px solid `#1456F0` or `#B45309`). Includes inline dismiss action and standard text sizes (13px body).