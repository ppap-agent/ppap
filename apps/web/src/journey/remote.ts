import {useCallback, useEffect, useRef, useState} from 'react'
import {request, type Draft, type DraftSaveRequest, type Material, type Round} from '../api'

export function useRemote<T>(path: string | null, interval = 0) {
  const [value, setData] = useState<T | null>(null)
  const generation = useRef(0)
  const setValue = useCallback((next: T | null) => {generation.current++; setData(next)}, [])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(previous => previous + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    generation.current++; setData(null); setError(''); setLoading(Boolean(path))
    async function read() {
      if (!path) return
      const identity = ++generation.current
      let keepPolling = Boolean(interval)
      try {
        const result = await request<T>(path, 'GET', undefined, controller.signal)
        if (!controller.signal.aborted && identity === generation.current) {
          setData(result); setError('')
          if (result && typeof result === 'object' && 'execution' in result && ['completed', 'failed'].includes(String(result.execution))) keepPolling = false
          if (result && typeof result === 'object' && 'status' in result && ['succeeded', 'failed'].includes(String(result.status))) keepPolling = false
        }
      } catch (cause) {if (!controller.signal.aborted && identity === generation.current) setError(message(cause))}
      finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          if (keepPolling) timer = setTimeout(() => void read(), interval)
        }
      }
    }
    void read()
    return () => {controller.abort(); clearTimeout(timer)}
  }, [path, interval, revision])
  return {value, error, loading, refresh, setValue}
}

export const message = (cause: unknown) => cause instanceof TypeError && /fetch/i.test(cause.message) ? '暂时无法连接服务，请重新读取。' : cause instanceof Error ? cause.message : '操作未完成，请稍后重试。'
export const inputItems = (items: Material[]): NonNullable<DraftSaveRequest['items']> => items.map(item => ({file_version_id: item.file_version_id, role: item.role, rule_version_id: item.role === 'target' ? item.rule_version_id : null}))
export const draftPlan = (draft: {items: Material[]; analysis_mode: Draft['analysis_mode']}) => JSON.stringify({analysis_mode: draft.analysis_mode, items: inputItems(draft.items).sort((a, b) => a.file_version_id.localeCompare(b.file_version_id))})
export type Acceptance = {key: string; plan: string}
export function acceptanceKey(userId: string, draftId: string) {return `ppap-live:accept:${userId}:${draftId}`}
export function readAcceptance(storageKey: string): Acceptance | null {
  const raw = localStorage.getItem(storageKey)
  if (!raw) return null
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || !('key' in value) || !('plan' in value) || typeof value.key !== 'string' || typeof value.plan !== 'string') throw new Error('检查请求记录无法读取，请先核对服务器受理状态。')
  return {key: value.key, plan: value.plan}
}
export function sameAcceptedPlan(round: Round, draftId: string, plan: string) {
  return round.source_draft_id === draftId && draftPlan({items: round.inputs, analysis_mode: round.analysis_mode}) === plan
}
