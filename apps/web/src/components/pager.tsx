import {useState} from 'react'
import {ChevronLeft, ChevronRight} from 'lucide-react'
import {cn} from '../lib/utils'
import {Button} from './ui/button'
import {Input} from './ui/input'

/** Page numbers around the current one; gaps are `null`. */
export function pageWindow(page: number, count: number): (number | null)[] {
  if (count <= 7) return Array.from({length: count}, (_, index) => index + 1)
  const from = Math.max(2, Math.min(page - 1, count - 4)), to = Math.min(count - 1, Math.max(page + 1, 5))
  return [1, ...from > 2 ? [null] : [], ...Array.from({length: to - from + 1}, (_, index) => from + index), ...to < count - 1 ? [null] : [], count]
}

/** 共 N 条 · 10 条/页 · page numbers, optionally 跳至 N 页. The page size is fixed by the service. */
export function Pager({total, page, pageSize = 10, onPage, jump = false, disabled = false, className}: {total: number; page: number; pageSize?: number; onPage: (page: number) => void; jump?: boolean; disabled?: boolean; className?: string}) {
  const count = Math.max(1, Math.ceil(total / pageSize))
  const [target, setTarget] = useState('')
  function go(value: number) {if (!disabled && value >= 1 && value <= count && value !== page) onPage(value)}
  return <nav aria-label="分页" className={cn('flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground', className)}>
    <span>共 {total} 条</span>
    <span>{pageSize} 条/页</span>
    <Button type="button" variant="outline" size="icon-xs" aria-label="上一页" disabled={disabled || page <= 1} onClick={() => go(page - 1)}><ChevronLeft/></Button>
    {pageWindow(page, count).map((value, index) => value === null ? <span key={`gap-${index}`} aria-hidden="true">…</span> : <Button key={value} type="button" variant={value === page ? 'soft' : 'outline'} size="icon-xs" aria-label={`第 ${value} 页`} aria-current={value === page ? 'page' : undefined} disabled={disabled && value !== page} onClick={() => go(value)}>{value}</Button>)}
    <Button type="button" variant="outline" size="icon-xs" aria-label="下一页" disabled={disabled || page >= count} onClick={() => go(page + 1)}><ChevronRight/></Button>
    {jump && <label className="flex items-center gap-1.5">跳至<Input inputMode="numeric" aria-label="跳转页码" className="h-6 w-11 text-center" value={target} disabled={disabled} onChange={event => setTarget(event.target.value.replace(/\D/g, ''))} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); go(Math.min(count, Math.max(1, Number(target) || page))); setTarget('')}}}/>页</label>}
  </nav>
}
