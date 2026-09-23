export {Badge, Button, FeedbackState, Link, PageHeader} from './index'
export type {BadgeTone, ButtonSize, ButtonVariant, FeedbackTone} from './index'

import {Link, type ButtonSize, type ButtonVariant} from './index'
import type {AnchorHTMLAttributes, ReactNode} from 'react'

type ButtonLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'className' | 'style' | 'color'> & {
  variant?: ButtonVariant
  size?: ButtonSize
  children?: ReactNode
}

/** Anchor with button intent. Default stays secondary so existing call sites keep a control, not a text link. */
export function ButtonLink({variant = 'secondary', size = 'md', children, ...props}: ButtonLinkProps) {
  return <Link variant={variant} size={size} {...props}>{children}</Link>
}
