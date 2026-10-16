import type {components} from '../generated/openapi'

function timestamp(value: string) {
  const iso = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value)
  const time = Date.parse(value), wall = iso ? Date.parse(`${iso[1]}Z`) : NaN
  return iso && Number.isFinite(time) && Number.isFinite(wall) && new Date(wall).toISOString().startsWith(iso[1]) ? time : NaN
}

/** Service timestamps describe an interval; elapsed browser time never changes audit counts. */
export function AuditTaskEstimate({task, progressIssue}: {task: components['schemas']['AuditTask']; progressIssue: boolean}) {
  if (task.state !== 'reviewing') return null
  const estimate = task.completion_estimate
  const times = estimate ? [estimate.observed_at, estimate.earliest_at, estimate.latest_at].map(timestamp) : []
  const valid = !progressIssue && times.length === 3 && times.every(Number.isFinite) && times[0] <= times[1] && times[1] <= times[2]
  const format = new Intl.DateTimeFormat('zh-CN', {dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23'})
  return <section aria-label="任务时间估计" className="space-y-1 text-sm text-muted-foreground">
    {!valid || !estimate ? <p>服务暂未提供可靠的结束时间估计。</p> : <>
      <p>{times[2] <= Date.now() ? '上次估计区间已过，等待服务更新。' : <>预计任务结束：<time aria-label="预计最早结束时间" dateTime={estimate.earliest_at}>{format.format(times[1])}</time> – <time aria-label="预计最晚结束时间" dateTime={estimate.latest_at}>{format.format(times[2])}</time>（本地时间）</>}</p>
      <p className="text-xs">服务估计于 <time aria-label="估计观察时间" dateTime={estimate.observed_at}>{format.format(times[0])}</time>（本地时间）；包含首次报告处理，不代表审核完成。</p>
    </>}
  </section>
}
