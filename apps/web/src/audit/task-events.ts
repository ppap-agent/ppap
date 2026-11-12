import {apiUrl} from '../api.ts'

/** A task changed; the reader fetches it through the authorized task API. */
export type TaskChange = {kinds: string[]; tasks: string[]}

export type TaskStreamHandlers = {
  /** The stream is (re)connected: read once, since a notice may have been missed while it was down. */
  onReady: () => void
  onChange: (change: TaskChange) => void
  /** Live notices are not available: fall back to reading on a timer. */
  onUnavailable: () => void
}

export type TaskEvents = {
  task: (taskId: string, handlers: TaskStreamHandlers) => () => void
  tasks: (handlers: TaskStreamHandlers) => () => void
}

/** Consecutive failed (re)connections before the page stops waiting for live notices. */
const MAX_FAILURES = 3

function subscribe(path: string, handlers: TaskStreamHandlers): () => void {
  if (typeof EventSource === 'undefined') {
    handlers.onUnavailable()
    return () => {}
  }
  let failures = 0, closed = false
  const source = new EventSource(apiUrl + path, {withCredentials: true})
  const stop = (unavailable: boolean) => {
    if (closed) return
    closed = true
    source.close()
    if (unavailable) handlers.onUnavailable()
  }
  source.addEventListener('ready', () => {failures = 0; handlers.onReady()})
  source.addEventListener('change', event => {
    try {
      const body = JSON.parse((event as MessageEvent<string>).data) as Partial<TaskChange>
      handlers.onChange({kinds: Array.isArray(body.kinds) ? body.kinds : [], tasks: Array.isArray(body.tasks) ? body.tasks : []})
    } catch {
      handlers.onChange({kinds: [], tasks: []})
    }
  })
  // The server ended the stream (for example, the session expired): reading shows why.
  source.addEventListener('end', () => stop(true))
  source.onerror = () => {
    // CLOSED: the server refused (401/403/404 or not an event stream). CONNECTING: the browser retries.
    if (source.readyState === EventSource.CLOSED || ++failures >= MAX_FAILURES) stop(true)
  }
  return () => stop(false)
}

export const nativeTaskEvents: TaskEvents = {
  task: (taskId, handlers) => subscribe(`/audit-tasks/${encodeURIComponent(taskId)}/events`, handlers),
  tasks: handlers => subscribe('/audit-task-events', handlers),
}
