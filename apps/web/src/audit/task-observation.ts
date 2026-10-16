import {useEffect, useRef} from 'react'
import type {components} from '../generated/openapi'
import {shareUnresolved} from './share-actions'

type Task = components['schemas']['AuditTask']

/** Unknown external commands are reconciled through their original request, not a task refresh. */
export function canObserveTask(task: Task | null): boolean {
  return Boolean(task && ['matching', 'reviewing'].includes(task.state) && task.acceptance?.state !== 'pending' && !task.files.some(row => row.audit?.report.generation_request?.state === 'pending') && task.report_package?.receipt.state !== 'pending' && !shareUnresolved(task.report_share))
}

/** One read at a time; timers request observations, never manufacture task progress. */
export function useTaskObservation(taskId: string | undefined, enabled: boolean, read: () => Promise<boolean | null>) {
  const latest = useRef({taskId, enabled, read})
  latest.current = {taskId, enabled, read}
  useEffect(() => {
    if (!enabled || !taskId) return
    let alive = true, reading = false, delay = 3000, retryAt = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    function schedule(after = delay) {clearTimeout(timer); if (alive && !document.hidden) timer = setTimeout(() => void refresh(), after)}
    async function refresh() {
      if (!alive || latest.current.taskId !== taskId || !latest.current.enabled || document.hidden || reading) return
      if (performance.now() < retryAt) {schedule(retryAt - performance.now()); return}
      clearTimeout(timer); reading = true
      let result: boolean | null = false
      try {result = await latest.current.read()}
      catch {result = false}
      finally {
        reading = false
        if (result === false) {delay = Math.min(delay * 2, 30000); retryAt = performance.now() + delay}
        else if (result === true) {delay = 3000; retryAt = 0}
        schedule()
      }
    }
    function wake() {if (document.hidden) clearTimeout(timer); else void refresh()}
    schedule()
    window.addEventListener('focus', wake)
    window.addEventListener('online', wake)
    document.addEventListener('visibilitychange', wake)
    return () => {
      alive = false; clearTimeout(timer)
      window.removeEventListener('focus', wake)
      window.removeEventListener('online', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [taskId, enabled])
}
