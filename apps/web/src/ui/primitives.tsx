import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactNode,
} from 'react'

function classes(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
}

export function Button({variant = 'secondary', size = 'md', className, ...props}: ButtonProps) {
  return <button className={classes('ui-button', `ui-button--${variant}`, size === 'sm' && 'ui-button--sm', className)} {...props} />
}

type ButtonLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
}

export function ButtonLink({variant = 'secondary', size = 'md', className, ...props}: ButtonLinkProps) {
  return <a className={classes('ui-button', `ui-button--${variant}`, size === 'sm' && 'ui-button--sm', className)} {...props} />
}

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {tone?: BadgeTone}

export function Badge({tone = 'neutral', className, children, ...props}: BadgeProps) {
  return <span className={classes('ui-badge', tone !== 'neutral' && `ui-badge--${tone}`, className)} {...props}>{children}</span>
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="ui-page-header">
      <div className="ui-page-header__copy">
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="ui-page-header__actions">{actions}</div> : null}
    </header>
  )
}

export type FeedbackTone = 'empty' | 'loading' | 'notice' | 'error'

export function FeedbackState({
  tone = 'empty',
  title,
  detail,
  icon,
  compact = false,
  inline = false,
  children,
  testId,
}: {
  tone?: FeedbackTone
  title?: ReactNode
  detail?: ReactNode
  icon?: ReactNode
  compact?: boolean
  inline?: boolean
  children?: ReactNode
  testId?: string
}) {
  const role = tone === 'error' ? 'alert' : tone === 'loading' || tone === 'notice' ? 'status' : undefined
  const heading = inline ? null : title
  return (
    <div
      className={classes(
        'ui-feedback',
        compact && 'ui-feedback--compact',
        inline && 'ui-feedback--inline',
        tone === 'notice' && 'ui-feedback--notice',
        tone === 'error' && 'ui-feedback--error',
      )}
      role={role}
      data-testid={testId}
    >
      {icon ? <span className="ui-feedback__icon" aria-hidden="true">{icon}</span> : null}
      {heading ? <h2>{heading}</h2> : null}
      {inline && title ? <strong>{title}</strong> : null}
      {detail ? <p>{detail}</p> : null}
      {children}
    </div>
  )
}
