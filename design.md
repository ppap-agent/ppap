---
version: alpha
name: PPAP Web
description: Product palette, font family and control radius of the PPAP web app and Docs Add-on.
colors:
  primary: "#2255e8"
  on-primary: "#ffffff"
  background: "#f5f6f7"
  surface: "#ffffff"
  on-surface: "#1f2329"
  muted: "#646a73"
  border: "#e5e6eb"
  input-border: "#dee0e3"
  secondary: "#f2f3f5"
  accent: "#eaf1ff"
  success: "#24853a"
  warning: "#b35900"
  error: "#d83931"
  info: "#2255e8"
  dark-primary: "#4c7bf3"
  dark-on-primary: "#ffffff"
  dark-background: "#111214"
  dark-surface: "#1f2023"
  dark-on-surface: "#ebebeb"
  dark-muted: "#a6a9ae"
  dark-secondary: "#2a2b2f"
  dark-accent: "#1d2a47"
  dark-success: "#52c26b"
  dark-warning: "#f2a33a"
  dark-error: "#f76964"
  dark-info: "#7a9cf7"
typography:
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "Segoe UI", sans-serif'
rounded:
  control: 0.375rem
components:
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
  divider:
    backgroundColor: "{colors.border}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.control}"
  input-outline:
    backgroundColor: "{colors.input-border}"
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
[docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)); do not edit it by hand. This prose is the reviewed guidance.

## Brand & Style

A light, Feishu-like workspace: white cards on a pale gray canvas, one blue for primary actions and
selection, and restrained semantic colours for results (success, warning, destructive, info).

## Colors

Use semantic roles, never raw values: `primary` for the one primary action and the current selection,
`muted` text for secondary information, `destructive`/`success`/`warning`/`info` only for result and
state meaning. The dark theme resolves the same roles.

## Typography

One sans-serif family with PingFang SC for Chinese. Hierarchy comes from weight and size, not colour.

## Shapes

Controls and cards share one radius; larger and smaller radii are derived from it.
