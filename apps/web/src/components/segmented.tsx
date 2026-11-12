import {cn} from '../lib/utils'
import {Button} from './ui/button'

/** A row of mutually exclusive filter buttons; `value` null means none of the presets is in effect. */
export function Segmented<T extends string>({label, value, options, onChange, disabled, className}: {label: string; value: T | null; options: readonly (readonly [T, string])[]; onChange: (value: T) => void; disabled?: boolean; className?: string}) {
  return <div role="group" aria-label={label} className={cn('inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border bg-card p-0.5', className)}>
    {options.map(([key, text]) => <Button key={key} type="button" variant={value === key ? 'soft' : 'ghost'} size="sm" aria-pressed={value === key} disabled={disabled} onClick={() => onChange(key)}>{text}</Button>)}
  </div>
}
