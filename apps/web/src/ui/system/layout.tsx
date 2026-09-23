import {createElement, type ReactNode} from 'react'
import type {BackgroundTone, FontSize, TextTone} from '../tokens'
import {type LayoutTag, type SharedProps, spaceVar} from './dom'

type BoxProps = SharedProps & {
  as?: LayoutTag
  background?: BackgroundTone
  hidden?: boolean
  role?: string
  tabIndex?: number
  ariaLabel?: string
  ariaHidden?: boolean
}

function boxStyle(props: SharedProps & {background?: BackgroundTone}) {
  return {
    display: props.gap ? 'flex' : undefined,
    flexDirection: props.gap ? 'column' as const : undefined,
    gap: spaceVar(props.gap),
    padding: spaceVar(props.padding),
    paddingInline: spaceVar(props.paddingX),
    paddingBlock: spaceVar(props.paddingY),
    background: props.background ? `var(--color-background-${props.background})` : undefined,
  }
}

export function Box({as = 'div', background, hidden, role, tabIndex, ariaLabel, ariaHidden, testId, id, children, ...space}: BoxProps) {
  return createElement(as, {
    id,
    hidden,
    role,
    tabIndex,
    'aria-label': ariaLabel,
    'aria-hidden': ariaHidden,
    'data-ui': 'box',
    'data-testid': testId,
    style: boxStyle({...space, background}),
  }, children)
}

type StackProps = SharedProps & {
  as?: LayoutTag
  align?: 'start' | 'center' | 'stretch' | 'end'
  justify?: 'start' | 'center' | 'between' | 'end'
}

const alignMap = {start: 'flex-start', center: 'center', stretch: 'stretch', end: 'flex-end'} as const
const justifyMap = {start: 'flex-start', center: 'center', between: 'space-between', end: 'flex-end'} as const

export function Stack({as = 'div', gap = 'md', align = 'stretch', justify = 'start', testId, id, children, padding, paddingX, paddingY}: StackProps) {
  return createElement(as, {
    id,
    'data-ui': 'stack',
    'data-testid': testId,
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: spaceVar(gap),
      alignItems: alignMap[align],
      justifyContent: justifyMap[justify],
      padding: spaceVar(padding),
      paddingInline: spaceVar(paddingX),
      paddingBlock: spaceVar(paddingY),
      minWidth: 0,
    },
  }, children)
}

export function Inline({as = 'div', gap = 'sm', align = 'center', justify = 'start', wrap = false, testId, id, children, padding, paddingX, paddingY}: StackProps & {wrap?: boolean}) {
  return createElement(as, {
    id,
    'data-ui': 'inline',
    'data-testid': testId,
    style: {
      display: 'flex',
      flexDirection: 'row',
      flexWrap: wrap ? 'wrap' : 'nowrap',
      gap: spaceVar(gap),
      alignItems: alignMap[align],
      justifyContent: justifyMap[justify],
      padding: spaceVar(padding),
      paddingInline: spaceVar(paddingX),
      paddingBlock: spaceVar(paddingY),
      minWidth: 0,
    },
  }, children)
}

export function Grid({columns, gap = 'md', testId, id, children, as = 'div'}: SharedProps & {as?: LayoutTag; columns: 1 | 2 | 3 | 4}) {
  return createElement(as, {
    id,
    'data-ui': 'grid',
    'data-columns': columns,
    'data-testid': testId,
    style: {
      display: 'grid',
      gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
      gap: spaceVar(gap),
    },
  }, children)
}

export function Surface({tone = 'surface', padding = 'lg', testId, id, children, as = 'section'}: SharedProps & {as?: LayoutTag; tone?: 'canvas' | 'surface' | 'subtle'}) {
  return createElement(as, {
    id,
    'data-ui': 'surface',
    'data-tone': tone,
    'data-testid': testId,
    style: {
      background: `var(--color-background-${tone})`,
      border: 'var(--size-border) solid var(--color-border-default)',
      borderRadius: 'var(--radius-card)',
      padding: spaceVar(padding),
    },
  }, children)
}

export function Text({
  as = 'p',
  tone = 'primary',
  size = 'md',
  weight = 'regular',
  children,
  id,
  testId,
}: {
  as?: 'p' | 'span' | 'small' | 'strong'
  tone?: TextTone
  size?: FontSize
  weight?: 'regular' | 'medium' | 'semibold' | 'bold'
  children?: ReactNode
  id?: string
  testId?: string
}) {
  return createElement(as, {
    id,
    'data-ui': 'text',
    'data-testid': testId,
    style: {
      margin: 0,
      color: `var(--color-text-${tone})`,
      fontSize: `var(--font-size-${size})`,
      fontWeight: `var(--font-weight-${weight})`,
      lineHeight: 'var(--font-line-body)',
      minWidth: 0,
    },
  }, children)
}

export function Heading({
  level = 2,
  children,
  visuallyHidden = false,
  id,
}: {
  level?: 1 | 2 | 3
  children?: ReactNode
  visuallyHidden?: boolean
  id?: string
}) {
  const tag = level === 1 ? 'h1' : level === 3 ? 'h3' : 'h2'
  return createElement(tag, {
    id,
    'data-ui': visuallyHidden ? 'sr' : 'heading',
    'data-level': level,
  }, children)
}

export function VisuallyHidden({children, as = 'span'}: {children?: ReactNode; as?: 'span' | 'h1' | 'h2'}) {
  return createElement(as, {'data-ui': 'sr'}, children)
}
