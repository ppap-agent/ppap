import {FileText, Image, MessageSquareText, PencilLine, SquareTerminal} from 'lucide-react'
import {useEffect, useState} from 'react'
import type {TaskActivity} from './task-events'
import type {LiveSteps} from './live-steps'

/* Chevron wavefront over a 3×3 grid: each cell lights up in turn, two fronts in flight. */
const STEPS = Array.from({length: 9}, (_, index) => (index % 3) + Math.abs(Math.floor(index / 3) - 1))

/** Pixel-grid loader for long-running work. Reduced motion keeps the grid still. */
export function PixelLoader() {
  return <span aria-hidden="true" className="grid w-max shrink-0 grid-cols-3 gap-0.5">
    {STEPS.map((step, index) => <span key={index} data-pixel-step={step} className="size-1 rounded-xs bg-primary opacity-15 motion-safe:animate-pixel-on"/>)}
  </span>
}

function parse(value?: string | null) {
  const time = value ? Date.parse(value) : NaN
  return Number.isFinite(time) ? time : null
}

/** Whole seconds since `since`, ticking once a second; null when the start is unknown. */
export function useElapsed(since?: string | null) {
  const start = parse(since)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (start === null) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [start])
  if (start === null) return null
  const seconds = Math.max(0, Math.floor((now - start) / 1000))
  return seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`
}

/**
 * The running state of a task: loader, shimmering label, elapsed time since the round started,
 * and whether changes arrive live or by periodic reading.
 */
export function LiveProgress({label, since, live}: {label: string; since?: string | null; live: boolean}) {
  const elapsed = useElapsed(since)
  return <div className="flex min-w-0 items-center gap-2.5">
    <PixelLoader/>
    <p className="font-medium motion-safe:shimmer-label motion-safe:animate-shimmer">{label}</p>
    {elapsed && <span className="font-mono text-xs tabular-nums text-muted-foreground" aria-label={`已用时 ${elapsed}`}>{elapsed}</span>}
    <span className="flex items-center gap-1 text-xs text-muted-foreground" title={live ? '审核进度变化时立即更新' : '每隔几秒读取一次审核进度'}>
      <span aria-hidden="true" className={`size-1.5 rounded-full ${live ? 'bg-success motion-safe:animate-pulse' : 'bg-muted-foreground/50'}`}/>{live ? '实时' : '自动刷新'}
    </span>
  </div>
}

const STEP_ICONS: Record<TaskActivity['kind'], typeof FileText> = {read: FileText, view: Image, run: SquareTerminal, edit: PencilLine, note: MessageSquareText}
const STEP_NAMES: Record<TaskActivity['kind'], string> = {read: '读取', view: '查看图片', run: '运行', edit: '整理', note: '说明'}

/**
 * What the deep review is doing right now: the latest steps broadcast while this page is open.
 * Nothing is stored, so a page opened mid-run starts with the next step.
 */
export function LiveStepList({steps}: {steps?: LiveSteps}) {
  const items = steps?.items || []
  const last = items.at(-1)?.id
  return <div className="space-y-1.5 border-t pt-3">
    <p className="flex items-center justify-between text-xs text-muted-foreground">
      <span>深度复核过程</span>
      {steps && steps.count > 0 && <span className="tabular-nums">已执行 {steps.count} 步</span>}
    </p>
    {items.length === 0
      ? <p className="text-sm text-muted-foreground">等待下一步…</p>
      : <ol role="log" aria-label="深度复核步骤" className="space-y-1">
        {items.map(step => {
          const Icon = STEP_ICONS[step.kind], current = step.id === last
          return <li key={step.id} className={`flex min-w-0 items-center gap-2 text-sm motion-safe:animate-in motion-safe:fade-in ${current ? '' : 'opacity-60'}`}>
            <Icon aria-label={STEP_NAMES[step.kind]} className="size-3.5 shrink-0 text-muted-foreground"/>
            <span className={`min-w-0 truncate ${step.kind === 'note' ? '' : 'font-mono text-xs'}`} title={step.text}>{step.text}</span>
            {current && <PixelLoader/>}
          </li>
        })}
      </ol>}
  </div>
}
