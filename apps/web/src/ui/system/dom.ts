import type {HTMLAttributes, ReactNode} from 'react'
import type {Space} from '../tokens'

export const layoutTags = [
  'div', 'section', 'nav', 'main', 'aside', 'article', 'header', 'footer',
  'ul', 'ol', 'li', 'span', 'p', 'form', 'label', 'dl', 'dt', 'dd',
  'fieldset', 'legend', 'time', 'strong', 'small',
] as const

export type LayoutTag = (typeof layoutTags)[number]

type Forbidden = 'className' | 'style' | 'color'

export type DomProps<T extends HTMLElement> = Omit<HTMLAttributes<T>, Forbidden>

export type SharedProps = {
  children?: ReactNode
  id?: string
  testId?: string
  padding?: Space
  paddingX?: Space
  paddingY?: Space
  gap?: Space
}

export function spaceVar(name?: Space) {
  return name ? `var(--space-${name})` : undefined
}
