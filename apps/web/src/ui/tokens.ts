/** Closed design decisions. Feature code selects these names; it does not invent values. */

export const color = {
  background: {
    canvas: '#f7f8fa',
    surface: '#ffffff',
    subtle: '#f9fafb',
    interactiveHover: '#f3f5f8',
    avatar: '#edf0f5',
    overlay: 'rgba(22, 37, 61, 0.33)',
  },
  text: {
    primary: '#1b2330',
    secondary: '#465166',
    muted: '#667085',
    inverse: '#ffffff',
    onAccent: '#ffffff',
    info: '#1d4f91',
  },
  border: {
    default: '#e3e7ed',
    strong: '#cfd6df',
    hover: '#b8c3d2',
    translucent: '#e3e7ed',
  },
  action: {
    primary: '#235fc7',
    primaryHover: '#1d55b5',
    primarySoft: '#edf3ff',
  },
  status: {
    success: '#176b4f',
    successSoft: '#edf8f2',
    warning: '#8b5b19',
    warningSoft: '#fff6e8',
    danger: '#a53636',
    dangerSoft: '#fff1ef',
    neutral: '#465166',
    neutralSoft: '#f3f5f8',
  },
} as const

export const space = {
  none: '0px',
  xs: '4px',
  sm: '8px',
  md: '12px',
  lg: '16px',
  xl: '24px',
  '2xl': '32px',
  '3xl': '40px',
  '4xl': '48px',
} as const

export const radius = {
  none: '0px',
  sm: '6px',
  control: '8px',
  card: '12px',
  window: '16px',
  pill: '999px',
} as const

export const font = {
  family: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  size: {
    xs: '11px',
    sm: '12px',
    body: '13px',
    md: '14px',
    lg: '16px',
    xl: '19px',
    '2xl': '24px',
  },
  weight: {
    regular: '400',
    medium: '500',
    semibold: '600',
    bold: '700',
  },
  line: {
    tight: '1.35',
    body: '1.5',
    relaxed: '1.6',
  },
} as const

export const shadow = {
  none: 'none',
  card: '0 1px 2px rgba(28, 38, 54, 0.05), 0 6px 18px rgba(28, 38, 54, 0.04)',
  overlay: '0 16px 48px rgba(28, 38, 54, 0.16)',
} as const

export const control = {
  sm: '34px',
  md: '40px',
  touch: '44px',
  nav: '52px',
} as const

export const size = {
  border: '1px',
  focus: '2px',
  sidebar: '216px',
  sidebarCollapsed: '72px',
  avatar: '30px',
  avatarSm: '28px',
  menu: '144px',
  prose: '720px',
  page: '1400px',
} as const

export const tokens = {color, space, radius, font, shadow, control, size} as const

/** Text/background pairs that must stay at least WCAG AA for body text. */
export const contrastPairs = [
  ['color.text.primary', 'color.background.surface'],
  ['color.text.secondary', 'color.background.surface'],
  ['color.text.muted', 'color.background.surface'],
  ['color.text.muted', 'color.background.canvas'],
  ['color.text.primary', 'color.background.canvas'],
  ['color.text.onAccent', 'color.action.primary'],
  ['color.text.info', 'color.action.primarySoft'],
  ['color.status.success', 'color.status.successSoft'],
  ['color.status.warning', 'color.status.warningSoft'],
  ['color.status.danger', 'color.status.dangerSoft'],
] as const

export type Space = keyof typeof space
export type Radius = keyof typeof radius
export type FontSize = keyof typeof font.size
export type FontWeight = keyof typeof font.weight
export type BackgroundTone = keyof typeof color.background
export type TextTone = keyof typeof color.text
export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'
