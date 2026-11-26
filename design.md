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
    fontSize: 12px
    fontWeight: 600
    lineHeight: 18px
  body-sm:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 12px
    fontWeight: 400
    lineHeight: 20px
  label-md:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 12px
    fontWeight: 500
    lineHeight: 20px
  body-md:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 22px
  body-lg:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 22px
  headline-sm:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 14px
    fontWeight: 600
    lineHeight: 22px
  headline-md:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 16px
    fontWeight: 600
    lineHeight: 24px
  headline-lg:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 20px
    fontWeight: 600
    lineHeight: 28px
  headline-xl:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif'
    fontSize: 24px
    fontWeight: 600
    lineHeight: 32px
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
[apps/web/tokens](apps/web/tokens/README.md)); do not edit it by hand. The layout and component guidance below
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
- Follow Feishu's scale: 14px body (`body-md`, Tailwind `text-sm`) for text, tables and controls; 12px for secondary text and tags; 16px section titles, 20px page titles, 24px at most. Nothing smaller than 12px.
- Hierarchy comes from size and spacing, not weight: medium weight only for titles and the current item; links and table text stay regular.

## Layout & Spacing

The layout is built on a condensed 12-column fluid grid tailored for maximum dashboard efficiency and operational visibility.

- **Viewport**: desktop at 1280px and wider; below 1280px (including the Feishu mobile client) the same pages drill down one level per screen, with 44px tap targets, cards instead of tables, full-screen or bottom-sheet dialogs and tap-to-expand instead of hover (see apps/web/README「窄屏不另起一套」).
- **Desktop & Workstations**: Layout leverages side-by-side split panes and persistent audit trails. Gutters sit at `1rem`, with tight element clusters using `0.5rem` and `0.75rem`.

## Page Frame

Layout follows what the person is doing on the page; visual form follows this document. Reference images (including the PRD's) explain intent but are not the reason for a layout.

- **Single-action pages** (upload; precheck and rule matching) fold the task list, keep what the decision rests on at the top (title, the one-line verdict, the step line) and the action at the bottom (estimate, deep mode, start). Only the material between them scrolls (`PageLayout` in `apps/web/src/components/page-layout.tsx`), so neither leaves the screen at 1280 × 800.
- **Result pages** keep the task list open for moving between tasks and put their own heading (status, progress, estimate) in the top bar, once.
- **While one file's findings are read**, its table keeps about 1000px. Side columns give way in order of relevance: the task list folds first (it stays open only on windows of about 1664px and wider), then the file list narrows to a strip of file icons with result dots (below about 1432px, or when the task has one file). Each keeps its control to open it again; a choice the person makes wins.
- **Library pages** (files, templates, rules, usage) have no task list.
- **Two shells, one page**: in a browser the frame has the left navigation, the page heading and the account menu; inside the Feishu client (detected from the user agent or the Feishu web SDK) the five entries sit in a top bar as pills, the page's actions sit at the right of that bar and the account menu is left to Feishu; the window title names the current page (Feishu shows the address when the title is blank). A library page at its top level and a new audit have no header row there: the pill and the window title already name them. Task, report and rule pages keep a breadcrumb row. Tokens, components and page content are shared; only the frame differs.
- **Master-detail pages** (rules, templates, files) are one white surface split by a divider: the list on the left chooses, the right side shows; the chosen item is never repeated as a heading card.
- The page kinds and their frame regions live in one table, `pageFrames` in `apps/web/src/audit/intake.tsx`; change the table, not scattered conditions.
- Each fact appears once: a count belongs to its section heading; the top verdict says only what the start depends on (files sent, blocked, needing a hand, unmatched).
- A control whose change redoes work says so beside it (changing the rules re-recognizes the whole batch).
- A completed step is shown in the primary colour with ✓, never in green: green means compliant here.
- Narrow windows fold auxiliary areas, never the main action, the verdict or the reasons for a block; nothing tells the person their window is too small.

## Elevation & Depth

Depth comes from the page tone, not from shadows or heavy lines.

- **Borders & Dividers**: faint 1px `#E4E6E9` lines separate table rows and sections; tables draw no vertical lines.
- **Card & Container Elevation**: static containers use zero shadow and no outline, resting flat on `#FFFFFF` against the `#F5F6F7` page; boxes are not nested inside boxes. Only a card that needs a state (warning, blocking) gets a tinted outline.
- **Interactive Depth**: dropdowns, popovers and modal panels use a tight, restrained shadow with a 1px `#E4E6E9` border.
- **Overlay Layering**: modal backdrops use a dark scrim at about 40%.

## Shapes

- **Cards, panels and dialogs**: 12px (`rounded.card`), Tailwind `rounded-lg`.
- **Buttons, inputs, menus, tabs and navigation pills**: 6px, Tailwind `rounded-md`, derived from `rounded.control` (8px).
- **Badges**: 4px rounded tags without an outline; not pills, which sit poorly around short Chinese labels.
- **Nested Elements**: inner elements step down to 4px (`rounded-sm`).

## Components

### Buttons
- **Primary**: Solid `#1456F0` fill, `#FFFFFF` text, 6px radius, height 32px (small: 28px). Hover: `#0F46C9`. Focus: 2px offset ring in `#1456F0`.
- **Secondary / Outline**: `#FFFFFF` background, 1px `#DEE0E3` border, `#1F2329` text. Hover: `#F2F3F5` background.
- **Inline**: a text action inside a sentence or value row, link-coloured with no box ("复制群名").
- **Ghost**: Transparent fill, `#646A73` text. Hover: `#F2F3F5`, text `#1F2329`.

### Badges & Tags
- **Style**: Height 20px, 12px regular text, 4px corners, a light tint and no outline (Feishu tags).
- **Variants**: Pass `#15803D` on a green tint, Warning `#B45309` on an amber tint, Non-Conformance `#DC2626` on a red tint, Neutral `#646A73` on `#F2F3F5`.

### Icons
- Thin outlined line icons (1.75px stroke on a 24px grid), never solid glyphs; one set for the whole product.

### Segmented Controls
- A `#F2F3F5` track with 6px corners; the chosen item is a white chip with medium text, the others are quiet text.

### Dialogs and Panels
- Centered dialogs only: 12px corners, 24px padding, a 16px semibold title, a 40% dark mask without blur, actions right-aligned with no footer band. Feishu does not use slide-in drawers; information that used to slide in (file information, report information, package records, notification settings) opens as a centered dialog.
- The one docked side panel is the original-document comparison, which stays open beside the findings so both can be read together.

### Tables
- 14px text, rows about 44px high with faint `#E4E6E9` dividers, 12px horizontal cell padding, a `#F2F3F5` row hover; headers grey and regular weight. Tables sit directly on the white surface, not in a box of their own.

### Form Inputs & Checkboxes
- **Inputs**: Height 36px, background `#FFFFFF`, border 1px solid `#DEE0E3`, padding 0 12px, 6px radius. Focus: border `#1456F0`, box-shadow `0 0 0 1px #1456F0`.
- **Checkboxes**: 16px square, 4px radius, border 1px solid `#DEE0E3`. Checked state: `#1456F0` background with white checkmark icon.

### Notices
- A notice is a quiet bar, not a card: `#F2F3F5` (or a red tint for a failure), 6px corners, no outline, a medium title and 14px muted text. Copy-confirmations and other passing results are one line of status text, not a block.