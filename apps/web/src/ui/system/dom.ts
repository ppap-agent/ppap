import type {HTMLAttributes, ReactNode} from 'react'
import type {Space} from '../tokens'

export const layoutTags = [
  'div', 'section', 'nav', 'main', 'aside', 'article', 'header', 'footer',
  'ul', 'ol', 'li', 'span', 'p', 'form', 'label', 'dl', 'dt', 'dd',
  'fieldset', 'legend', 'time', 'strong', 'small',
] as const

export type LayoutTag = (typeof layoutTags)[number]

type Forbidden = 'className' | 'style' | 'color'
type SystemAttr = 'data-ui' | 'data-variant' | 'data-size' | 'data-tone' | 'data-kind'

/** Callers cannot introduce styling, even by spreading an object that already has those fields. */
export type Sealed<T> = Omit<T, Forbidden | SystemAttr> & {
  className?: never
  style?: never
  color?: never
  'data-ui'?: never
  'data-variant'?: never
  'data-size'?: never
  'data-tone'?: never
  'data-kind'?: never
}

/** A prop the primitive owns. Callers cannot set or override it. */
export type Locked<T, K extends PropertyKey> = Omit<T, Extract<K, keyof T>> & {[P in Extract<K, string>]?: never}

const blockedProps = ['className', 'style', 'color', 'data-ui', 'data-variant', 'data-size', 'data-tone', 'data-kind'] as const

/** Drop styling and system markers before a primitive spreads the rest onto the DOM. */
export function publicProps<T extends object>(props: T) {
  const next = {...props} as Record<string, unknown>
  for (const key of blockedProps) delete next[key]
  return next as {[K in keyof T]?: T[K]}
}

export type DomProps<T extends HTMLElement> = Sealed<HTMLAttributes<T>>

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
