import {useRef, useState} from 'react'
import type {components} from '../generated/openapi'

type Receipt = components['schemas']['AuditUploadItem']
type FileVersion = components['schemas']['FileVersion']
export type UploadInput = {kind: 'local'; file: File} | {kind: 'feishu'; file: components['schemas']['DriveFile']}
export type UploadAdapter = {upload: (input: UploadInput, requestId: string) => Promise<Receipt>; readUpload: (requestId: string) => Promise<Receipt>}
export type UploadRow = {id: string; input: UploadInput; receipt: Receipt; removed: boolean; busy: boolean}
export const uploadName = (input: UploadInput) => input.file.name
export const canRetryUpload = (row: UploadRow) => !row.removed && !row.busy && row.receipt.state === 'failed' && row.receipt.retry_allowed
export const canResumeUpload = (row: UploadRow) => !row.removed && !row.busy && row.input.kind === 'local' && row.input.file instanceof File && row.receipt.state === 'uploading' && row.receipt.resume_allowed === true

/** Receipts belong to a request and draft, never to a filename or whichever page is active. */
export function useUploads(adapter: UploadAdapter, onReady: (file: FileVersion, removed: boolean) => void) {
  const current = useRef(new Map<string, UploadRow>())
  const epoch = useRef(0)
  const [rows, setRows] = useState<UploadRow[]>([])
  function save(row: UploadRow) {current.current.set(row.id, row); setRows([...current.current.values()])}
  async function execute(row: UploadRow, readOnly: boolean) {
    const started = epoch.current
    try {
      const result = await (readOnly ? adapter.readUpload(row.receipt.request_id) : adapter.upload(row.input, row.receipt.request_id))
      if (started !== epoch.current) return
      const latest = current.current.get(row.id)
      if (!latest || latest.receipt.request_id !== row.receipt.request_id) return
      if (result.request_id !== row.receipt.request_id || result.state === 'ready' && !result.file?.id) throw new Error('upload_identity_mismatch')
      const receipt = {...result, retry_allowed: result.state === 'failed' && result.retry_allowed}
      save({...latest, receipt, busy: false})
      if (receipt.state === 'ready' && receipt.file) onReady(receipt.file, latest.removed)
    } catch {
      if (started !== epoch.current) return
      const latest = current.current.get(row.id)
      if (latest?.receipt.request_id === row.receipt.request_id) save({...latest, busy: false, receipt: {...latest.receipt, state: 'unknown', retry_allowed: false, reason: '未取得明确传输结果，文件可能已接收。请先读取同一请求，不要重新上传。'}})
    }
  }
  function add(inputs: UploadInput[]) {
    for (const input of inputs) {
      if (input.kind === 'feishu' && [...current.current.values()].some(row => !row.removed && row.input.kind === 'feishu' && row.input.file.token === input.file.token)) continue
      const id = crypto.randomUUID()
      const row: UploadRow = {id, input, removed: false, busy: true, receipt: {request_id: crypto.randomUUID(), state: 'uploading', file: null, reason: null, retry_allowed: false}}
      save(row); void execute(row, false)
    }
  }
  function retry(id: string) {
    const row = current.current.get(id)
    if (!row || !canRetryUpload(row)) return
    const next: UploadRow = {...row, busy: true, receipt: {...row.receipt, request_id: crypto.randomUUID(), state: 'uploading', reason: null, retry_allowed: false}}
    save(next); void execute(next, false)
  }
  function read(id: string) {
    const row = current.current.get(id)
    if (!row || row.busy || !['uploading', 'unknown'].includes(row.receipt.state)) return
    const next = {...row, busy: true}
    save(next); void execute(next, true)
  }
  function resume(id: string) {
    const row = current.current.get(id)
    if (!row || !canResumeUpload(row)) return
    const next = {...row, busy: true, receipt: {...row.receipt, resume_allowed: false}}
    save(next); void execute(next, false)
  }
  function remove(id: string) {const row = current.current.get(id); if (row) save({...row, removed: true})}
  function reset() {epoch.current += 1; current.current.clear(); setRows([])}
  function snapshot() {return [...current.current.values()].map(row => row.busy ? {...row, busy: false, receipt: {...row.receipt, state: 'unknown' as const, retry_allowed: false, reason: '页面切换时尚未取得回执，请读取原传输请求。'}} : row)}
  function restore(saved: UploadRow[]) {reset(); for (const row of saved) current.current.set(row.id, row); setRows([...current.current.values()])}
  return {rows, add, retry, read, resume, remove, reset, snapshot, restore}
}
