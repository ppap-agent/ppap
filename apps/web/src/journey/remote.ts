import {useCallback, useEffect, useRef, useState} from 'react'
import {request} from '../api'

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
