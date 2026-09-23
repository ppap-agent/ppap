import type {LucideIcon} from 'lucide-react'

const pixel = {sm: 16, md: 18, lg: 20} as const

export type IconSize = keyof typeof pixel

/** Closed icon scale. Callers pick sm, md, or lg; they do not pass a pixel size. */
export function Icon({icon: Glyph, size = 'md'}: {icon: LucideIcon; size?: IconSize}) {
  return <Glyph size={pixel[size]} aria-hidden="true" />
}
