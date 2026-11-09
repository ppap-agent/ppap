import type {components} from '../generated/openapi.ts'
import {sameSnapshot} from './result-projection.ts'
import type {UploadRow} from './uploads.ts'

export type UploadPreparation = components['schemas']['AuditUploadPreparation']
export type PreparedUpload = components['schemas']['AuditPreparedUpload']
type Draft = components['schemas']['Draft']
export type PreparationAdapter = {
  read: (id: string) => Promise<Draft>
  save: (id: string, revision: number | null, value: UploadPreparation) => Promise<Draft>
  readFile: (fileId: string, versionId: string) => Promise<components['schemas']['FileVersion']>
}

export function describeUploads(rows: UploadRow[]): PreparedUpload[] {
  return rows.filter(row => row.input.kind === 'feishu' || row.input.sha256).map(({input, receipt, removed}) => input.kind === 'local'
    ? {request_id: receipt.request_id, kind: 'local', name: input.file.name, byte_size: input.file.size, content_type: input.file.type || 'application/octet-stream', sha256: input.sha256, ...(input.destination ? {folder: input.destination} : {}), removed}
    : {request_id: receipt.request_id, kind: 'feishu', name: input.file.name, drive_file: {token: input.file.token, name: input.file.name, type: input.file.type}, removed})
}

export function restoreUploads(rows: PreparedUpload[]): UploadRow[] {
  return rows.map(row => ({id: row.request_id, removed: row.removed, busy: false,
    input: row.kind === 'local'
      ? {kind: 'local', file: {name: row.name, size: row.byte_size!, type: row.content_type!}, sha256: row.sha256, ...(row.folder ? {destination: row.folder} : {})}
      : {kind: 'feishu', file: row.drive_file!},
    receipt: {request_id: row.request_id, state: 'unknown', file: null, retry_allowed: false, reason: '请查看原上传请求。刷新不会重新上传文件。'},
  }))
}

export function preparationFiles(files: components['schemas']['AuditTaskFile'][]): UploadPreparation['files'] {
  return files.map(row => ({file_version_id: row.file.id, validation_id: row.validation_id ?? null, selection: row.selection, removal_reason: row.removal_reason?.trim() || null, ...(row.preferred_rule_version_id ? {preferred_rule_version_id: row.preferred_rule_version_id} : {}), ...(row.reference_only ? {reference_only: true} : {}), ...(row.replacement_of_file_version_id ? {replacement_of_file_version_id: row.replacement_of_file_version_id} : {})}))
}

/** The server owns the draft. This queue only orders this page's commands. */
export function createUploadPreparation(adapter: PreparationAdapter) {
  const known = new Map<string, Draft>()
  const pending = new Map<string, Promise<Draft>>()
  const blocked = new Set<string>()
  function accept(id: string, draft: Draft) {
    if (draft.id === id && draft.task_id === id) {known.set(id, draft); return draft}
    if (draft.id !== id || !draft.upload_preparation || !Number.isSafeInteger(draft.preparation_revision) || draft.preparation_revision! < 0) throw new Error('上传准备结果不匹配。请查看原准备记录。')
    known.set(id, draft)
    return draft
  }
  async function read(id: string) {const next = accept(id, await adapter.read(id)); blocked.delete(id); return next}
  function save(id: string, value: UploadPreparation): Promise<Draft> {
    const frozen: UploadPreparation = JSON.parse(JSON.stringify(value))
    const before = pending.get(id) ?? Promise.resolve(known.get(id)!)
    const command = before.then(async () => {
      if (blocked.has(id)) throw new Error('准备资料保存结果待确认，请先查看原准备记录。')
      const current = known.get(id)
      if (current?.task_id) throw new Error('这份准备已开始识别，请读取现有任务。')
      if (current && sameSnapshot(current.upload_preparation, frozen)) return current
      const revision = current?.preparation_revision ?? null
      try {
        const next = await adapter.save(id, revision, frozen)
        if (next.preparation_revision !== (revision === null ? 0 : revision + 1) || next.task_id || !sameSnapshot(next.upload_preparation, frozen)) throw new Error('preparation_receipt_mismatch')
        return accept(id, next)
      } catch {
        blocked.add(id)
        // A lost write reply grants one read, never a replacement write.
        const observed = accept(id, await adapter.read(id))
        if (observed.task_id || observed.preparation_revision !== (revision === null ? 0 : revision + 1) || !sameSnapshot(observed.upload_preparation, frozen)) throw new Error('准备资料保存结果待确认；当前选择仍保留，请先查看原准备记录。')
        blocked.delete(id); return observed
      }
    })
    pending.set(id, command)
    void command.finally(() => {if (pending.get(id) === command) pending.delete(id)}).catch(() => {})
    return command
  }
  return {read, save, peek: (id: string) => known.get(id)}
}
