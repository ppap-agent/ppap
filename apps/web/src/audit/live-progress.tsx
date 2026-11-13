import {useEffect, useState} from 'react'

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

