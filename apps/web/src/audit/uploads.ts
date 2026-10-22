import {useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {localFileDigest} from '../api.ts'

type Receipt = components['schemas']['AuditUploadItem']
type FileVersion = components['schemas']['FileVersion']
export type UploadInput = {kind: 'local'; file: File | Pick<File, 'name' | 'size' | 'type'>; sha256?: string; destination?: {token: string; name: string}} | {kind: 'feishu'; file: components['schemas']['DriveFile']}
export type UploadAdapter = {upload: (input: UploadInput, requestId: string) => Promise<Receipt>; readUpload: (requestId: string) => Promise<Receipt>}
export type UploadRow = {id: string; input: UploadInput; receipt: Receipt; removed: boolean; busy: boolean; inputIssue?: string}
export const uploadName = (input: UploadInput) => input.file.name
export const canRetryUpload = (row: UploadRow) => !row.removed && !row.busy && (row.input.kind === 'feishu' || row.input.file instanceof File) && row.receipt.state === 'failed' && row.receipt.retry_allowed
export const canResumeUpload = (row: UploadRow) => !row.removed && !row.busy && (row.input.kind === 'feishu' || row.input.file instanceof File) && row.receipt.state === 'uploading' && row.receipt.resume_allowed === true

export function originalUploadFile(original: Pick<File, 'name' | 'type'>, bytes: File): File {
  return new File([bytes], original.name, {type: original.type, lastModified: bytes.lastModified})
}

/** Receipts belong to a request and draft, never to a filename or whichever page is active. */
export function useUploads(adapter: UploadAdapter, onReady: (file: FileVersion, removed: boolean) => void, beforeWrite?: (rows: UploadRow[]) => Promise<void>) {
  const current = useRef(new Map<string, UploadRow>())
  const epoch = useRef(0)
  const [rows, setRows] = useState<UploadRow[]>([])
  function save(row: UploadRow) {current.current.set(row.id, row); setRows([...current.current.values()])}
  async function send(rows: UploadRow[]) {
    const started = epoch.current
    try {
      for (let index = 0; index < rows.length; index++) {
        const row = rows[index]
        if (row.input.kind !== 'local' || !(row.input.file instanceof File)) continue
        if (row.input.file.size > 200 * 1024 * 1024) {
          save({...row, busy: false, receipt: {...row.receipt, state: 'failed', retry_allowed: false, reason: '文件超过 200 MB，未开始上传。'}})
          rows.splice(index--, 1); continue
        }
        let sha256: string
        try {sha256 = await localFileDigest(row.input.file)}
        catch {
          if (started !== epoch.current) return
          const latest = current.current.get(row.id)
          if (latest) save({...latest, busy: false, inputIssue: row.input.sha256 ? '本地原件暂不可读取，请重新选择当时的原件。' : undefined, receipt: {...latest.receipt,
            state: row.input.sha256 ? 'unknown' : 'failed', retry_allowed: false, resume_allowed: false,
            reason: row.input.sha256 ? '未能读取本地原件，本次未继续传输。请读取原请求，不要新建上传。' : '本地原件无法读取，未开始上传。请排除此文件后重新选择。',
          }})
          rows.splice(index--, 1); continue
        }
        if (started !== epoch.current) return
        const latest = current.current.get(row.id)
        if (latest) {rows[index] = {...latest, input: {...row.input, sha256}}; save(rows[index])}
      }
      if (started !== epoch.current) return
      if (!rows.length) return
      await beforeWrite?.([...current.current.values()].filter(row => row.input.kind === 'feishu' || row.input.sha256))
      if (started !== epoch.current) return
      for (const row of rows) {
        const latest = current.current.get(row.id)
        if (!latest) continue
        if (!latest.removed) void execute(latest, false)
        else save({...latest, busy: false, receipt: {...latest.receipt, state: 'unknown', reason: '已在传输前排除，未启动新传输。'}})
      }
    } catch {
      if (started === epoch.current) for (const row of rows) {
        const latest = current.current.get(row.id)
        if (latest) save({...latest, busy: false, receipt: {...latest.receipt, state: 'unknown', retry_allowed: false, reason: '上传准备尚未确认保存，本批没有启动新传输。请先读取准备记录。'}})
      }
    }
  }
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
    const added: UploadRow[] = []
    for (const input of inputs) {
      if (input.kind === 'feishu' && [...current.current.values()].some(row => !row.removed && row.input.kind === 'feishu' && row.input.file.token === input.file.token)) continue
      const id = crypto.randomUUID()
      const row: UploadRow = {id, input, removed: false, busy: true, receipt: {request_id: crypto.randomUUID(), state: 'uploading', file: null, reason: null, retry_allowed: false}}
      save(row); added.push(row)
    }
    if (added.length) void send(added)
  }
  function retry(id: string) {
    const row = current.current.get(id)
    if (!row || !canRetryUpload(row)) return
    save({...row, removed: true})
    const next: UploadRow = {...row, id: crypto.randomUUID(), busy: true, receipt: {...row.receipt, request_id: crypto.randomUUID(), file: null, state: 'uploading', reason: null, retry_allowed: false}}
    save(next); void send([next])
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
    save(next); void send([next])
  }
  async function attach(id: string, file: File) {
    const row = current.current.get(id), started = epoch.current
    if (!row || row.input.kind !== 'local' || row.busy || row.removed) return
    if (!row.input.sha256 || file.size !== row.input.file.size) {save({...row, inputIssue: '所选文件与当时原件内容不同，未替换或上传。请重新选择原件。'}); return}
    const checking = {...row, busy: true, inputIssue: undefined}
    save(checking)
    try {
      const digest = await localFileDigest(file)
      if (started !== epoch.current || current.current.get(id) !== checking) return
      if (digest !== row.input.sha256) {save({...row, inputIssue: '所选文件与当时原件内容不同，未替换或上传。请重新选择原件。'}); return}
      save({...row, input: {...row.input, file: originalUploadFile(row.input.file, file)}, inputIssue: undefined})
    } catch {
      if (started === epoch.current && current.current.get(id) === checking) save({...row, inputIssue: '原件暂不可读取，请重新选择。'})
    } finally {
      const latest = current.current.get(id)
      if (started === epoch.current && latest?.removed && latest.busy && latest.input === checking.input) save({...latest, busy: false})
    }
  }
  function remove(id: string) {const row = current.current.get(id); if (row) save({...row, removed: true})}
  function reset() {epoch.current += 1; current.current.clear(); setRows([])}
  function snapshot() {return [...current.current.values()].map(row => row.busy ? {...row, busy: false, receipt: {...row.receipt, state: 'unknown' as const, retry_allowed: false, reason: '页面切换时尚未取得回执，请读取原传输请求。'}} : row)}
  function restore(saved: UploadRow[]) {reset(); for (const row of saved) current.current.set(row.id, row); setRows([...current.current.values()])}
  return {rows, add, retry, read, resume, attach, remove, reset, snapshot, restore}
}
