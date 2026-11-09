import {readLocalUpload, uploadLocalVersion, type Material, type UploadReceipt} from '../api'
import {validLocalFile, type LocalFileRef, type LocalUpload} from './local-files'
import {message} from './remote'

export type UploadDocument = {id: string; name: string; localFile?: LocalFileRef; material?: Material; uploadReceipt?: UploadReceipt}

export function withUploadReceipt<T extends UploadDocument>(file: T, receipt: UploadReceipt): T {
  const ref = file.localFile, version = receipt.file
  const contentType = (ref?.type || 'application/octet-stream').split(';')[0].trim().toLowerCase()
  if (!ref || receipt.request_id !== ref.id || version && (!version.file_id || version.id !== ref.id || file.uploadReceipt?.file && version.file_id !== file.uploadReceipt.file.file_id || version.byte_size !== ref.size || version.content_type.toLowerCase() !== contentType || version.sha256 !== null && version.sha256 !== ref.digest) || receipt.state === 'ready' && (!version || version.status !== 'ready' || version.sha256 !== ref.digest)) throw new Error('上传结果与原文件不一致，请重新核对原请求。')
  return {...file, uploadReceipt: receipt, ...(receipt.state === 'ready' && version ? {material: {file_id: version.file_id, file_version_id: version.id, original_filename: version.original_filename, role: 'target' as const, rule_version_id: null}} : {})}
}
export function optionalUploadReceipt(value: unknown, localFile: unknown) {
  if (value === undefined) return true
  if (!validLocalFile(localFile) || !value || typeof value !== 'object') return false
  const receipt = value as UploadReceipt
  if (!['uploading', 'ready', 'unknown', 'failed'].includes(receipt.state) || typeof receipt.retry_allowed !== 'boolean' || receipt.reason !== null && typeof receipt.reason !== 'string' || receipt.resume_allowed !== undefined && typeof receipt.resume_allowed !== 'boolean' || receipt.file !== null && typeof receipt.file !== 'object') return false
  try {withUploadReceipt({id: localFile.id, name: '', localFile}, receipt); return true} catch {return false}
}

// Save responsibility before any remote effect. Leaving stops new work, not an in-flight PUT.
export async function transferDocuments<T extends UploadDocument>(files: T[], incoming: LocalUpload[], save: (files: T[]) => Promise<void>, current: () => boolean) {
  let next = files
  try {
    for (const {file, ref} of incoming) {
      if (!current()) break
      const prepared = next.map(item => item.id === ref.id ? {...item, uploadReceipt: {request_id: ref.id, state: 'unknown' as const, file: null, retry_allowed: false, reason: '上传结果尚未确认，请核对原请求。'}} : item)
      await save(prepared)
      next = prepared
      if (!current()) break
      const receipt = await uploadLocalVersion(file, ref.id)
      if (!current()) break
      next = next.map(item => item.id === ref.id ? withUploadReceipt(item, receipt) : item)
      await save(next)
      if (receipt.state !== 'ready') break
    }
    return {files: next, error: ''}
  } catch (cause) {return {files: next, error: message(cause)}}
}

export async function readDocumentUpload<T extends UploadDocument>(file: T): Promise<T> {
  if (!file.uploadReceipt) throw new Error('此文件尚未上传。')
  return withUploadReceipt(file, await readLocalUpload(file.uploadReceipt.request_id))
}
