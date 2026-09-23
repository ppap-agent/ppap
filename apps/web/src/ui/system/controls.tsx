import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import type {StatusTone} from '../tokens'

type NoStyle<T> = Omit<T, 'className' | 'style' | 'color'>

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md'

type ButtonProps = NoStyle<ButtonHTMLAttributes<HTMLButtonElement>> & {
  variant?: ButtonVariant
  size?: ButtonSize
}

export function Button({variant = 'secondary', size = 'md', type = 'button', children, ...props}: ButtonProps) {
  return (
    <button data-ui="button" data-variant={variant} data-size={size} type={type} {...props}>
      {children}
    </button>
  )
}

type LinkProps = NoStyle<AnchorHTMLAttributes<HTMLAnchorElement>> & {
  variant?: ButtonVariant | 'text'
  size?: ButtonSize
}

export function Link({variant = 'text', size = 'md', children, ...props}: LinkProps) {
  return (
    <a data-ui={variant === 'text' ? 'link' : 'button'} data-variant={variant === 'text' ? undefined : variant} data-size={size} {...props}>
      {children}
    </a>
  )
}

export function IconButton({label, children, ...props}: NoStyle<ButtonHTMLAttributes<HTMLButtonElement>> & {label: string}) {
  return (
    <button data-ui="icon-button" type="button" aria-label={label} title={label} {...props}>
      {children}
    </button>
  )
}

export function Field({label, children, hint}: {label: ReactNode; children: ReactNode; hint?: ReactNode}) {
  return (
    <label data-ui="field">
      <span data-ui="field-label">{label}</span>
      {children}
      {hint ? <span data-ui="field-hint">{hint}</span> : null}
    </label>
  )
}

export function Input(props: NoStyle<InputHTMLAttributes<HTMLInputElement>>) {
  return <input data-ui="input" {...props} />
}

export function Select(props: NoStyle<SelectHTMLAttributes<HTMLSelectElement>>) {
  return <select data-ui="select" {...props} />
}

export function Textarea(props: NoStyle<TextareaHTMLAttributes<HTMLTextAreaElement>>) {
  return <textarea data-ui="textarea" {...props} />
}

export function FileInput({
  label,
  variant = 'primary',
  ...props
}: {label: ReactNode; variant?: ButtonVariant} & NoStyle<InputHTMLAttributes<HTMLInputElement>>) {
  return (
    <label data-ui="file">
      <span data-ui="button" data-variant={variant}>{label}</span>
      <input data-ui="file-input" type="file" {...props} />
    </label>
  )
}

export function Checkbox({label, ...props}: {label: ReactNode} & NoStyle<InputHTMLAttributes<HTMLInputElement>>) {
  return (
    <label data-ui="check">
      <input data-ui="checkbox" type="checkbox" {...props} />
      <span>{label}</span>
    </label>
  )
}

export type BadgeTone = StatusTone

export function Badge({tone = 'neutral', children, ...props}: {tone?: BadgeTone; children?: ReactNode} & NoStyle<HTMLAttributesBridge>) {
  return <span data-ui="badge" data-tone={tone} {...props}>{children}</span>
}

type HTMLAttributesBridge = {
  id?: string
  role?: string
  title?: string
  'data-testid'?: string
  'data-status'?: string
}

export function PageHeader({title, description, actions}: {title: ReactNode; description?: ReactNode; actions?: ReactNode}) {
  return (
    <header data-ui="page-header">
      <div data-ui="page-header-copy">
        <h1 data-ui="page-title">{title}</h1>
        {description ? <p data-ui="page-description">{description}</p> : null}
      </div>
      {actions ? <div data-ui="page-header-actions">{actions}</div> : null}
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
  return (
    <div data-ui="feedback" data-tone={tone} data-compact={compact || undefined} data-inline={inline || undefined} role={role} data-testid={testId}>
      {icon ? <span data-ui="feedback-icon" aria-hidden="true">{icon}</span> : null}
      {!inline && title ? <h2>{title}</h2> : null}
      {inline && title ? <strong>{title}</strong> : null}
      {detail ? <p>{detail}</p> : null}
      {children}
    </div>
  )
}
