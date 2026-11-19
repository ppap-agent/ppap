---
version: alpha
name: PPAP Web
description: "Precision Slate: slate neutrals, one blue for commitments, crisp 1px borders and 8px radius for the PPAP web app and Docs Add-on."
colors:
  primary: "#2563eb"
  on-primary: "#ffffff"
  background: "#f8fafc"
  surface: "#ffffff"
  on-surface: "#0f172a"
  muted: "#64748b"
  border: "#e2e8f0"
  input-border: "#e2e8f0"
  secondary: "#f1f5f9"
  accent: "#eff6ff"
  success: "#16a34a"
  warning: "#d97706"
  error: "#dc2626"
  info: "#2563eb"
  dark-primary: "#2563eb"
  dark-on-primary: "#ffffff"
  dark-background: "#020617"
  dark-surface: "#0f172a"
  dark-on-surface: "#f1f5f9"
  dark-muted: "#94a3b8"
  dark-border: "#334155"
  dark-input-border: "#334155"
  dark-secondary: "#1e293b"
  dark-accent: "#172554"
  dark-success: "#22c55e"
  dark-warning: "#f59e0b"
  dark-error: "#dc2626"
  dark-info: "#60a5fa"
typography:
  body:
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "Segoe UI", sans-serif'
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

# PPAP Web design guidance: Precision Slate

The token frontmatter above is generated from `apps/web/tokens/` by `pnpm design:update` (see
[docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)); do not edit it by hand. The guidance below comes from the
Stitch design system "Precision Slate" and is reviewed by people; where an exact value in it differs from
the frontmatter, the frontmatter wins.

## Brand & Style

This design system is engineered for industrial supplier compliance, audit traceability, and precision automotive supply-chain operations. The visual posture is clinical, structured, and unapologetically utilitarian. It strips away decorative whimsy in favor of immediate legibility, high information density, and absolute systemic rigor.

The aesthetic blends Modern Enterprise SaaS with the restrained clarity of modern component architecture (shadcn/ui-inspired). By prioritizing crisp 1px borders, subtle slate fills, and an austere neutral canvas, the interface instills confidence during mission-critical inspections, production gate reviews, and regulatory validation workflows.

## Colors

The palette is anchored by a sterile white canvas (`#FFFFFF`) framed by zinc and slate neutral borders (`#E2E8F0`, `#F1F5F9`) to define structural boundaries without visual fatigue. 

- **Primary (`#2563EB`)**: Used deliberately for primary interactive commitments, active step indicators, and high-priority focus rings.
- **Subtle Accents (`#EFF6FF`, `#DBEAFE`)**: Used strictly for selection indicators, active navigation tabs, badge surfaces, and low-contrast interactive hover states.
- **Neutrals (`#0F172A` text, `#64748B` muted, `#F8FAFC` canvas alternate)**: Deliver contrast compliant with WCAG AAA standards for high-density tabular and inspection data.
- **Semantic Signals**: Success (`#16A34A` / `#DCFCE7`), Warning (`#D97706` / `#FEF3C7`), Destructive/Non-Conformance (`#DC2626` / `#FEE2E2`).

## Typography

Typography prioritizes tabular alignment, compact tracking, and vertical rhythm. Inter serves as the primary system font, complemented by PingFang SC for Simplified Chinese environments.

- Use tabular figures (`tnum`) for part serial numbers, torque tolerances, audit timestamps, and percentage indicators.
- Maintain compact scale tiers: `body-md` (13px) acts as the operational standard for dense grids and spec tables, while 11px uppercase labels (`label-sm`) anchor status micro-tags.
- Strict visual hierarchy ensures that metadata headers remain visually subordinate to actual compliance figures.

## Layout & Spacing

The layout is built on a condensed 12-column fluid grid tailored for maximum dashboard efficiency and operational visibility.

- **Breakpoints**: Desktop (≥1280px), Tablet (768px–1279px), Mobile (<768px).
- **Desktop & Workstations**: Layout leverages side-by-side split panes and persistent audit trails. Gutters sit at `1rem`, with tight element clusters using `0.5rem` and `0.75rem`.
- **Vertical Rhythm**: Compact table row heights (36px default, 32px dense) allow high-volume scanning of line items without scroll exhaustion.
- **Reflow Rules**: On viewports below 1024px, secondary inspector panels collapse into sliding drawer sheets; tables shift to horizontally scrollable viewports with frozen primary identifier columns.

## Elevation & Depth

This design system avoids heavy blurred drop shadows, utilizing low-contrast ghost outlines and crisp structural borders to construct depth.

- **Borders & Dividers**: Visual separation relies on 1px solid borders (`#E2E8F0` for outer boundaries, `#F1F5F9` for internal cell dividers).
- **Card & Container Elevation**: Static containers use zero shadow, resting flat on `#FFFFFF` against `#F8FAFC` page backdrops. 
- **Interactive Depth**: Dropdowns, popovers, and elevated modal panels use a tight, restrained shadow paired with a perimeter line: `0 1px 3px 0 rgba(15, 23, 42, 0.08), 0 1px 2px -1px rgba(15, 23, 42, 0.08)` with a 1px `#E2E8F0` border.
- **Overlay Layering**: Modal backdrops employ a clean slate scrim: `rgba(15, 23, 42, 0.4)`.

## Shapes

The geometric signature is grounded at a default radius of 8px (`0.5rem`). This strikes a balance between sharp industrial discipline and modern software ergonomics.

- **Inputs, Buttons, and Cards**: Standardize on `rounded` (8px).
- **Pills and Status Indicators**: Badges and status pills leverage full rounding (`rounded-full` / 9999px) to visually contrast against square-aligned data grids.
- **Nested Elements**: Inner nested elements (e.g., input addons, segmented control items) use 6px (`0.375rem`) to ensure concentric alignment with outer 8px containers.

## Components

### Buttons
- **Primary**: Solid `#2563EB` fill, `#FFFFFF` text, 8px radius, height 36px (dense: 32px). Hover: `#1D4ED8`. Focus: 2px offset ring in `#2563EB`.
- **Secondary / Outline**: `#FFFFFF` background, 1px `#E2E8F0` border, `#0F172A` text. Hover: `#F8FAFC` background with `#CBD5E1` border.
- **Ghost**: Transparent fill, `#64748B` text. Hover: `#F1F5F9`, text `#0F172A`.

### Badges & Tag Pills
- **Style**: Height 20px, uppercase 11px font (`label-sm`), tracking wide, fully rounded (pill) or 4px subtle rounded tags.
- **Variants**:
  - *Compliant / Pass*: Background `#DCFCE7`, text `#15803D`, border 1px solid `#BBF7D0`.
  - *Review / Warning*: Background `#FEF3C7`, text `#B45309`, border 1px solid `#FDE68A`.
  - *Non-Conformance / Critical*: Background `#FEE2E2`, text `#B91C1C`, border 1px solid `#FECACA`.
  - *Neutral / Pending*: Background `#F1F5F9`, text `#475569`, border 1px solid `#E2E8F0`.

### Form Inputs & Checkboxes
- **Inputs**: Height 36px, background `#FFFFFF`, border 1px solid `#E2E8F0`, padding 0 12px. Focus: border `#2563EB`, box-shadow `0 0 0 1px #2563EB`.
- **Checkboxes**: 16px square, 4px radius, border 1px solid `#CBD5E1`. Checked state: `#2563EB` background with white checkmark icon.

### Inspection Cards
- Structured 3-tier card layout:
  1. *Header*: 12px top/bottom padding, subtle background `#F8FAFC`, border-bottom 1px `#E2E8F0`, housing part ID, revision code, and status badge.
  2. *Body*: White `#FFFFFF`, standard 16px padding, organized into key-value specification pairs with vertical divider lines.
  3. *Action Footer*: 8px padding, right-aligned micro-actions (Audit, Sign-off, Reject).

### Alert Banners
- Low-saturation, solid tint backgrounds (`#EFF6FF` for info, `#FEF3C7` for notice) with a 1px border. 
- Integrated left indicator rule (3px solid `#2563EB` or `#D97706`). Includes inline dismiss action and standard text sizes (13px body).