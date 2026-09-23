export {Badge, Button, FeedbackState, Link, PageHeader} from './index'
export type {BadgeTone, ButtonSize, ButtonVariant, FeedbackTone, Sealed} from './index'

import {Link, type ButtonSize, type ButtonVariant} from './index'
import type {AnchorHTMLAttributes, ReactNode} from 'react'
import type {Sealed} from './system/dom'

type ButtonLinkProps = Sealed<AnchorHTMLAttributes<HTMLAnchorElement>> & {
  variant?: ButtonVariant
  size?: ButtonSize
  children?: ReactNode
}

/** Anchor with button intent. Default stays secondary so existing call sites keep a control, not a text link. */
export function ButtonLink({variant = 'secondary', size = 'md', children, ...props}: ButtonLinkProps) {
  return <Link variant={variant} size={size} {...props}>{children}</Link>
}
