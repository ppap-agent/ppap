import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {shareUnresolved} from './share-actions'
import type {TaskEvents} from './task-events'

type Task = components['schemas']['AuditTask']

/** Unknown external commands are reconciled through their original request, not a task refresh. */
export function canObserveTask(task: Task | null): boolean {
  return Boolean(task && ['matching', 'reviewing'].includes(task.state) && task.acceptance?.state !== 'pending' && !task.files.some(row => row.audit?.report.generation_request?.state === 'pending') && task.report_package?.receipt.state !== 'pending' && !shareUnresolved(task.report_share))
}

/** How the page learns about task changes: pushed notices, or reading on a timer. */
export type ObservationMode = 'live' | 'polling' | 'idle'

/**
 * One read at a time; notices and timers request observations, never manufacture task progress.
 * With live notices a read happens only when the task changed (and once after each reconnect);
 * without them, or after the stream fails, reads fall back to the backoff timer.
 */
export function useTaskObservation(taskId: string | undefined, enabled: boolean, read: () => Promise<boolean | null>, events?: TaskEvents['task']): ObservationMode {
  const latest = useRef({taskId, enabled, read})
  latest.current = {taskId, enabled, read}
  const [mode, setMode] = useState<ObservationMode>('idle')
  useEffect(() => {
    if (!enabled || !taskId) {setMode('idle'); return}
    let alive = true, reading = false, again = false, delay = 3000, retryAt = 0, polling = !events
    let timer: ReturnType<typeof setTimeout> | undefined
    setMode(polling ? 'polling' : 'live')
    function schedule(after = delay, force = false) {clearTimeout(timer); if (alive && (polling || force) && !document.hidden) timer = setTimeout(() => void refresh(), after)}
    async function refresh(notice = false) {
      if (!alive || latest.current.taskId !== taskId || !latest.current.enabled || document.hidden) return
      // Timers and focus never queue a read; only a change notice during a read reads once more.
      if (reading) {if (notice) again = true; return}
      if (performance.now() < retryAt) {schedule(retryAt - performance.now(), true); return}
      clearTimeout(timer); reading = true
      let result: boolean | null = false
      try {result = await latest.current.read()}
      catch {result = false}
      finally {
        reading = false
        if (result === false) {delay = Math.min(delay * 2, 30000); retryAt = performance.now() + delay}
        else if (result === true) {delay = 3000; retryAt = 0}
        // A notice during the read means the read may already be stale.
        if (again) {again = false; void refresh(true)}
        // A failed read is retried even with live notices: no further notice may come.
        else schedule(delay, result === false)
      }
    }
    const unsubscribe = events?.(taskId, {
      onReady: () => void refresh(true),
      onChange: () => void refresh(true),
      onUnavailable: () => {if (!alive || polling) return; polling = true; setMode('polling'); schedule()},
    })
    schedule()
    function wake() {if (document.hidden) clearTimeout(timer); else void refresh()}
    window.addEventListener('focus', wake)
    window.addEventListener('online', wake)
    document.addEventListener('visibilitychange', wake)
    return () => {
      alive = false; clearTimeout(timer); unsubscribe?.()
      window.removeEventListener('focus', wake)
      window.removeEventListener('online', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [taskId, enabled, events])
  return mode
}
