import {cn} from '../lib/utils'

const formats: [RegExp, string, string][] = [
  [/\.(?:xlsx|xls|xlsm|xlsb|csv)$/i, 'XLS', 'bg-success'],
  [/\.(?:docx?|docm)$/i, 'DOC', 'bg-primary'],
  [/\.pdf$/i, 'PDF', 'bg-destructive'],
  [/\.(?:png|jpe?g|webp)$/i, 'IMG', 'bg-warning'],
  [/\.(?:zip|rar|7z)$/i, 'ZIP', 'bg-info'],
]

/** A small lettered tile for the file format; the file name beside it carries the same fact for assistive technology. */
export function FileTypeBadge({name, className}: {name: string; className?: string}) {
  const [, code, tone] = formats.find(([pattern]) => pattern.test(name.trim())) ?? [null, (name.match(/\.([a-z0-9]{1,4})$/i)?.[1] ?? 'FILE').toUpperCase().slice(0, 4), 'bg-muted-foreground']
  return <span aria-hidden="true" className={cn('inline-grid h-5 min-w-5 shrink-0 place-items-center rounded-sm px-1 text-xs leading-none font-bold text-white', tone, className)}>{code}</span>
}
