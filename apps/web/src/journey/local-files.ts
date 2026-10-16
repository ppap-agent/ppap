// Local prototype payloads, not a second case record. Snapshots own these immutable references.
import {useEffect, useRef, useState} from 'react'

export type LocalFileRef = {id: string; digest: string; size: number; type: string}
export type LocalUpload = {file: File; ref: LocalFileRef}
export const fileAccept = '.xls,.xlsx,.xlsm,.doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.txt,.csv'
const maxBytes = 200 * 1024 * 1024
const supported = /\.(xlsx?|xlsm|docx?|pdf|png|jpe?g|webp|txt|csv)$/i
export function validLocalFile(value: unknown): value is LocalFileRef {
  if (!value || typeof value !== 'object') return false
  const ref = value as Record<string, unknown>
  return typeof ref.id === 'string' && ref.id.length > 0 && typeof ref.digest === 'string' && /^[a-f0-9]{64}$/.test(ref.digest)
    && Number.isInteger(ref.size) && Number(ref.size) >= 0 && Number(ref.size) <= maxBytes && typeof ref.type === 'string'
}
export const optionalLocalFile = (value: unknown) => value === undefined || validLocalFile(value)
function openFiles(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('ppap-greenfield-files', 1)
    let blocked = false
    request.onupgradeneeded = () => request.result.createObjectStore('files', {keyPath: 'id'})
    request.onerror = () => reject(request.error)
    request.onblocked = () => {blocked = true; reject(new Error('请关闭其他 PPAP 页面后重试。'))}
    request.onsuccess = () => {
      if (blocked) {request.result.close(); return}
      request.result.onversionchange = () => request.result.close()
      resolve(request.result)
    }
  })
}
async function digest(blob: Blob) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('')
}
type StoredFile = {id: string; blob: Blob}
async function writeFiles(rows: StoredFile[], restore = false) {
  const db = await openFiles()
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('files', 'readwrite')
      tx.oncomplete = () => resolve()
      tx.onabort = () => reject(tx.error ?? new Error('文件保存已中断。'))
      const store = tx.objectStore('files')
      for (const row of rows) {if (restore) store.put(row); else store.add(row)}
    })
  } finally {db.close()}
}
export async function storeLocalFiles(files: File[]): Promise<LocalUpload[]> {
  for (const file of files) {
    if (!supported.test(file.name)) throw new Error(`${file.name}：请选择 Excel、Word、PDF、图片或文本文件。`)
    if (file.size > maxBytes) throw new Error(`${file.name} 超过 200 MB；此批文件尚未添加。`)
  }
  const uploads: LocalUpload[] = []
  for (const file of files) uploads.push({file, ref: {id: crypto.randomUUID(), digest: await digest(file), size: file.size, type: file.type}})
  if (uploads.length) await writeFiles(uploads.map(({file, ref}) => ({id: ref.id, blob: file})))
  return uploads
}
export async function readLocalFile(ref: LocalFileRef): Promise<Blob | null> {
  if (!validLocalFile(ref)) throw new Error('此记录的原件引用无效。请从新来件重新登记。')
  const db = await openFiles()
  let row: StoredFile | undefined
  try {
    row = await new Promise<StoredFile | undefined>((resolve, reject) => {
      const tx = db.transaction('files', 'readonly'), request = tx.objectStore('files').get(ref.id)
      tx.oncomplete = () => resolve(request.result as StoredFile | undefined)
      tx.onabort = () => reject(tx.error ?? new Error('本机读取中断。'))
    })
  } finally {db.close()}
  if (!row) return null
  if (!(row.blob instanceof Blob) || row.blob.size !== ref.size || await digest(row.blob) !== ref.digest) throw new Error('本机原件与登记内容不一致。请重新选择当时的原件。')
  return row.blob
}
export async function restoreLocalFile(ref: LocalFileRef, file: File) {
  if (!validLocalFile(ref) || file.size !== ref.size || await digest(file) !== ref.digest)
    throw new Error('内容与当时原件不同，未替换历史文件。若这是新版，请返回后作为新来件上传。')
  await writeFiles([{id: ref.id, blob: file}], true)
}
export function fileError(error: unknown) {
  return error instanceof DOMException && error.name === 'QuotaExceededError' ? '本机空间不足，文件未保存。请保留原件，释放浏览器存储空间后重试。'
    : error instanceof Error ? error.message : '本机文件保存失败，请重新选择文件。'
}
// Leaving the owner or changing its identity cancels the UI attachment, not the browser write.
export function useLocalUploads(onReady: (files: LocalUpload[]) => void | Promise<void>, owner: string) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const active = useRef(0), pending = useRef(false), mounted = useRef(true)
  const latest = useRef({onReady, owner}); latest.current = {onReady, owner}
  useEffect(() => {mounted.current = true; return () => {mounted.current = false; active.current++}}, [])
  function cancel() {active.current++; pending.current = false; setBusy(false); setError('')}
  async function upload(files: File[]) {
    if (!files.length || pending.current) return
    const attempt = ++active.current, identity = latest.current.owner
    pending.current = true; setBusy(true); setError('')
    try {
      const rows = await storeLocalFiles(files)
      if (mounted.current && attempt === active.current && identity === latest.current.owner) await latest.current.onReady(rows)
    } catch (cause) {if (mounted.current && attempt === active.current) setError(fileError(cause))}
    finally {if (mounted.current && attempt === active.current) {pending.current = false; setBusy(false)}}
  }
  return {upload, busy, error, cancel}
}
