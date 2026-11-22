import {cn} from '../lib/utils'

const tones = ['bg-primary', 'bg-success', 'bg-warning', 'bg-brand-light']

/**
 * A customer's mark: the name's first character on a tone picked from that character,
 * so one customer looks the same on every page even where its name is written longer.
 */
export function CustomerAvatar({name, className}: {name: string; className?: string}) {
  const initial = [...name.trim()][0] ?? '·'
  const tone = tones[initial.codePointAt(0)! % tones.length]
  return <span aria-hidden="true" className={cn('flex size-9 shrink-0 items-center justify-center rounded-md text-base font-semibold text-white', tone, className)}>{initial}</span>
}
