// Local editing drafts. The Product API owns accepted rounds and saved reviews.
import {useEffect, useRef, useState, type SetStateAction} from 'react'

export let draftPrefix = 'ppap-greenfield:v1:'
let prefix = draftPrefix
export function setDraftOwner(owner: string | null) {
  prefix = draftPrefix = owner ? `ppap-journey:v1:${owner}:` : 'ppap-greenfield:v1:'
}
type Envelope<T> = {version: 1; updatedAt: string; value: T}
export const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
export const draftEvent = 'ppap-local-records'
type SaveFailure = {key: string; message: string; raw: string | null}
const failures = new Map<string, SaveFailure>()
export const unsavedDrafts = () => [...failures.values()]
export const notifyDrafts = () => window.dispatchEvent(new Event(draftEvent))
export function parseDraft(raw: string): Envelope<unknown> | null {
  try {
    const data: unknown = JSON.parse(raw)
    return isObject(data) && data.version === 1 && typeof data.updatedAt === 'string' && Number.isFinite(Date.parse(data.updatedAt)) && isObject(data.value)
      ? data as Envelope<unknown> : null
  } catch {return null}
}
export function loadDraft<T>(key: string, validate: (value: unknown) => value is T) {
  try {
    const raw = localStorage.getItem(prefix + key)
    if (raw === null) return {raw, saved: null, error: ''}
    const data = parseDraft(raw)
    if (data && validate(data.value)) return {raw, saved: {...data, value: data.value}, error: ''}
    return {raw, saved: null, error: '原记录无法读取，已停止自动保存以保留原内容。请从账号菜单打开“本机记录”导出检查。'}
  } catch {return {raw: undefined, saved: null, error: '暂时无法读取本机记录。请勿把当前空白内容当作已保存的事项。'}}
}

export function useSavedDraft<T extends object>(key: string, initial: () => T, validate: (value: unknown) => value is T, restore?: (saved: T) => T) {
  const [loaded] = useState(() => ({key, ...loadDraft(key, validate)}))
  const [value, setValue] = useState<T>(() => {
    const saved = loaded.saved?.value ?? initial()
    return restore ? restore(saved) : saved
  })
  const [saveError, setSaveError] = useState(loaded.error)
  const expected = useRef(loaded.raw)
  const persisted = useRef(loaded.saved ? JSON.stringify(loaded.saved.value) : null)
  useEffect(() => {
    const serialized = JSON.stringify(value)
    const raw = JSON.stringify({version: 1, updatedAt: new Date().toISOString(), value})
    function fail(message: string, recoverable = true) {
      setSaveError(message)
      failures.set(key, {key, message, raw: recoverable ? raw : null}); notifyDrafts()
    }
    if (loaded.key !== key || loaded.error) {fail(loaded.error || '事项标识已改变，已停止保存。', false); return}
    if (persisted.current === serialized && !failures.has(key)) return
    try {
      // Compare the exact last-read record. This detects observed changes; it is not a cross-tab transaction.
      if (localStorage.getItem(prefix + key) !== expected.current) {
        fail('本机记录已在别处改变，当前输入未覆盖原记录。请先导出当前输入，再重新打开事项。'); return
      }
      if (persisted.current === serialized) {
        setSaveError(''); failures.delete(key); notifyDrafts(); return
      }
      localStorage.setItem(prefix + key, raw)
      expected.current = raw; persisted.current = serialized
      setSaveError('')
      failures.delete(key); notifyDrafts()
    } catch {fail('本机保存失败。当前输入仍在页面中，请重试保存或先导出，勿关闭或刷新。')}
  }, [key, value, loaded])
  function field<K extends keyof T>(name: K): [T[K], (next: SetStateAction<T[K]>) => void] {
    return [value[name], next => setValue(current => ({...current, [name]: typeof next === 'function' ? (next as (old: T[K]) => T[K])(current[name]) : next}))]
  }
  return {value, field, saveError, update: setValue}
}
