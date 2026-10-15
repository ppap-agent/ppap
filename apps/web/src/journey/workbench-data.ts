import {useCallback, useEffect, useRef, useState} from 'react'
import {request, type Draft, type DraftList, type RoundList, type RoundSummary} from '../api'
import {message} from './remote'

type Page<T> = {rows: T[]; next: number | null; pages: number}
type Snapshot = {query: string; drafts: Page<Draft> | null; rounds: Page<RoundSummary> | null; errors: string[]; busy: 'refresh' | 'more' | null}
const empty = (query: string): Snapshot => ({query, drafts: null, rounds: null, errors: [], busy: null})

// Refresh only the pages the user has opened, preserving each source on a failed read.
async function readPages<T extends {id: string}>(path: string, previous: Page<T> | null, more: boolean, signal: AbortSignal, decode: (body: unknown) => {rows: T[]; next: number | null}): Promise<Page<T>> {
  if (more && previous?.next === null) return previous
  let offset = more ? previous?.next ?? 0 : 0
  let rows = more ? previous?.rows ?? [] : []
  let pages = more ? previous?.pages ?? 0 : 0
  const count = more ? 1 : previous?.pages ?? 1
  for (let page = 0; page < count; page++) {
    const result = decode(await request<unknown>(`${path}&offset=${offset}`, 'GET', undefined, signal))
    if (result.next !== null && result.next <= offset) throw new Error('事项分页未能继续，请重新读取。')
    rows = [...new Map([...rows, ...result.rows].map(row => [row.id, row])).values()]
    pages++
    if (result.next === null || page === count - 1) return {rows, next: result.next, pages}
    offset = result.next
  }
  throw new Error('事项尚未读取。')
}

export function useWorkbenchData(query: string, enabled: boolean, active: boolean) {
  const [snapshot, setSnapshot] = useState(() => empty(query))
  const latest = useRef(snapshot)
  const inFlight = useRef<AbortController | null>(null)
  const publish = (next: Snapshot) => {latest.current = next; setSnapshot(next)}
  const read = useCallback(async (more = false) => {
    if (!enabled || !active || inFlight.current) return
    const controller = new AbortController()
    inFlight.current = controller
    const before = latest.current.query === query ? latest.current : empty(query)
    publish({...before, busy: more ? 'more' : 'refresh'})
    const suffix = `?limit=50&q=${encodeURIComponent(query)}`
    const results = await Promise.allSettled([
      readPages<Draft>(`/drafts${suffix}`, before.drafts, more, controller.signal, body => ({rows: (body as DraftList).drafts, next: (body as DraftList).next_offset})),
      readPages<RoundSummary>(`/rounds${suffix}`, before.rounds, more, controller.signal, body => ({rows: (body as RoundList).rounds, next: (body as RoundList).next_offset})),
    ])
    if (controller.signal.aborted || inFlight.current !== controller) return
    inFlight.current = null
    const [drafts, rounds] = results
    publish({query, busy: null,
      drafts: drafts.status === 'fulfilled' ? drafts.value : before.drafts,
      rounds: rounds.status === 'fulfilled' ? rounds.value : before.rounds,
      errors: results.flatMap((result, index) => result.status === 'rejected' ? [`${index ? '审核记录' : '审核准备'}读取失败：${message(result.reason)}`] : []),
    })
  }, [query, enabled, active])

  useEffect(() => {
    if (latest.current.query !== query) publish(empty(query))
    if (!enabled || !active) return
    const visible = () => !document.hidden && !['requirements', 'usage'].includes(new URLSearchParams(location.search).get('view') || '')
    const refresh = () => {if (visible()) void read()}
    refresh()
    const timer = window.setInterval(refresh, 15000)
    window.addEventListener('focus', refresh)
    window.addEventListener('online', refresh)
    window.addEventListener('popstate', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      inFlight.current?.abort(); inFlight.current = null
      clearInterval(timer)
      window.removeEventListener('focus', refresh)
      window.removeEventListener('online', refresh)
      window.removeEventListener('popstate', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [query, enabled, active, read])
  const current = snapshot.query === query ? snapshot : empty(query)
  return {...current,
    loading: enabled && !current.drafts && !current.rounds && !current.errors.length,
    more: current.drafts?.next != null || current.rounds?.next != null,
    refresh: () => void read(), loadMore: () => void read(true),
  }
}
