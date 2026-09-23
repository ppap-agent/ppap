import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import type {StatusTone} from '../tokens'
import {publicProps, type Locked, type Sealed} from './dom'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md'

type ButtonProps = Sealed<ButtonHTMLAttributes<HTMLButtonElement>> & {
  variant?: ButtonVariant
  size?: ButtonSize
}

export function Button({variant = 'secondary', size = 'md', type = 'button', children, ...props}: ButtonProps) {
  return (
    <button {...publicProps(props)} data-ui="button" data-variant={variant} data-size={size} type={type}>
      {children}
    </button>
  )
}

type LinkProps = Sealed<AnchorHTMLAttributes<HTMLAnchorElement>> & {
  variant?: ButtonVariant | 'text'
  size?: ButtonSize
}

export function Link({variant = 'text', size = 'md', children, ...props}: LinkProps) {
  return (
    <a {...publicProps(props)} data-ui={variant === 'text' ? 'link' : 'button'} data-variant={variant === 'text' ? undefined : variant} data-size={size}>
      {children}
    </a>
  )
}

export function IconButton({label, children, ...props}: Locked<Sealed<ButtonHTMLAttributes<HTMLButtonElement>>, 'type'> & {label: string}) {
  return (
    <button {...publicProps(props)} data-ui="icon-button" type="button" aria-label={label} title={label}>
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

export function Input(props: Sealed<InputHTMLAttributes<HTMLInputElement>>) {
  return <input {...publicProps(props)} data-ui="input" />
}

export function Select(props: Sealed<SelectHTMLAttributes<HTMLSelectElement>>) {
  return <select {...publicProps(props)} data-ui="select" />
}

export function Textarea(props: Sealed<TextareaHTMLAttributes<HTMLTextAreaElement>>) {
  return <textarea {...publicProps(props)} data-ui="textarea" />
}

export function FileInput({
  label,
  variant = 'primary',
  ...props
}: {label: ReactNode; variant?: ButtonVariant} & Locked<Sealed<InputHTMLAttributes<HTMLInputElement>>, 'type'>) {
  return (
    <label data-ui="file">
      <span data-ui="button" data-variant={variant}>{label}</span>
      <input {...publicProps(props)} data-ui="file-input" type="file" />
    </label>
  )
}

export function Checkbox({label, ...props}: {label: ReactNode} & Locked<Sealed<InputHTMLAttributes<HTMLInputElement>>, 'type'>) {
  return (
    <label data-ui="check">
      <input {...publicProps(props)} data-ui="checkbox" type="checkbox" />
      <span>{label}</span>
    </label>
  )
}

export type BadgeTone = StatusTone

export function Badge({tone = 'neutral', children, ...props}: {tone?: BadgeTone; children?: ReactNode} & Sealed<HTMLAttributesBridge>) {
  return <span {...publicProps(props)} data-ui="badge" data-tone={tone}>{children}</span>
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
