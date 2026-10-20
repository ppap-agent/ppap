import {feedbackUploadError, type AuditFeedbackAdapter, type FeedbackReceipt, type FeedbackEntry} from '../src/audit/feedback-contract'
import type {components} from '../src/generated/openapi'

/** In-memory UI fixture only. Does not write Bitable, publish rules, or create tasks. */
export function fixtureFeedback(scene: string): AuditFeedbackAdapter {
  const transfers = new Map<string, components['schemas']['AuditUploadItem']>()
  const blobs = new Map<string, Blob>()
  const submissions = new Map<string, FeedbackReceipt>()
  const inputs = new Map<string, string>()
  const uploadAttempts = new WeakMap<File, number>()
  const history: FeedbackEntry[] = scene.startsWith('feedback-history') ? Array.from({length: 11}, (_, index) => {
    const file = {file_id: `preview-history-file-${index}`, id: `preview-history-version-${index}`, original_filename: `保留模板-${index + 1}.zip`, content_type: 'application/zip', byte_size: 3, status: 'ready', sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}
    blobs.set(file.id, new Blob(['abc'], {type: file.content_type}))
    const receipt: FeedbackReceipt = {request: {request_id: `preview-history-request-${index}`, file_version_ids: [file.id]}, attempt_id: `preview-history-attempt-${index}`, state: 'unknown', submission_id: null, received_file_version_ids: [], received_at: null, retry_allowed: false, reason: null}
    submissions.set(`${receipt.request.request_id}:${receipt.attempt_id}`, {...receipt, state: 'received', submission_id: `preview-history-record-${index}`, received_file_version_ids: [file.id], received_at: '2026-10-18T00:00:00Z'})
    return {receipt, files: index === 10 ? [] : [file], files_available: index !== 10, reason: index === 10 ? '附件访问已受限，请联系支持核对原提报。' : null, created_at: '2026-10-18T00:00:00Z'}
  }) : []
  const adapter: AuditFeedbackAdapter = {
    ...(scene.startsWith('feedback-history') ? {listHistory: async (page: number) => ({page, page_size: 10 as const, total: history.length, entries: structuredClone(history.slice((page - 1) * 10, page * 10))})} : {}),
    upload: async (input, requestId) => {
      const existing = transfers.get(requestId)
      if (existing) {
        if (scene === 'feedback-upload-resume' && existing.state === 'uploading' && existing.file && input.kind === 'local') {
          const file = {...existing.file, status: 'ready'}
          blobs.set(file.id, new Blob([await input.file.arrayBuffer()], {type: file.content_type}))
          const result = {...existing, file, state: 'ready' as const, resume_allowed: false}
          transfers.set(requestId, result)
          return structuredClone(result)
        }
        return structuredClone(existing)
      }
      if (input.kind !== 'local') throw new Error('feedback_local_fixture_only')
      const source = input.file
      const attempt = (uploadAttempts.get(source) || 0) + 1
      uploadAttempts.set(source, attempt)
      if (scene === 'feedback-upload-failed' && source.name === '需要重试.xlsx' && attempt === 1) {
        const failed: components['schemas']['AuditUploadItem'] = {request_id: requestId, state: 'failed', file: null, reason: '上传失败，请重试。', retry_allowed: true}
        transfers.set(requestId, failed)
        return structuredClone(failed)
      }
      const reason = feedbackUploadError(source)
      const file = reason ? null : {file_id: `preview-feedback-file-${crypto.randomUUID()}`, id: `preview-feedback-version-${crypto.randomUUID()}`, original_filename: source.name, content_type: source.type || 'application/octet-stream', byte_size: source.size, status: 'ready', sha256: null}
      if (file) blobs.set(file.id, new Blob([await source.arrayBuffer()], {type: file.content_type}))
      const receipt: components['schemas']['AuditUploadItem'] = {request_id: requestId, state: file ? 'ready' : 'failed', file, reason: reason || null, retry_allowed: false}
      if (file && scene === 'feedback-upload-resume') {
        blobs.delete(file.id)
        receipt.file = {...file, status: 'staging'}
        receipt.state = 'uploading'
        receipt.resume_allowed = true
      }
      transfers.set(requestId, receipt)
      if ((scene === 'feedback-upload-unknown' || scene === 'feedback-upload-failed' && attempt > 1) && file) throw new Error('preview_upload_response_lost')
      return structuredClone(receipt)
    },
    readUpload: async requestId => {
      const result = transfers.get(requestId)
      if (!result) throw new Error('unknown_feedback_upload')
      return structuredClone(result)
    },
    submit: async (request, attemptId) => {
      const key = `${request.request_id}:${attemptId}`, previous = submissions.get(key)
      if (previous) return structuredClone(previous)
      const original = inputs.get(request.request_id), files = JSON.stringify(request.file_version_ids)
      if (original && original !== files) throw new Error('feedback_fixture_input_changed')
      inputs.set(request.request_id, files)
      if (scene === 'feedback-no-retry' || scene === 'feedback-failed' && !original) {
        const failed: FeedbackReceipt = {request: structuredClone(request), attempt_id: attemptId, state: 'failed', submission_id: null, received_file_version_ids: [], received_at: null, retry_allowed: scene === 'feedback-failed', reason: scene === 'feedback-no-retry' ? '暂不能再次提交，请联系支持。' : '提交失败，请重试。'}
        submissions.set(key, failed)
        return structuredClone(failed)
      }
      const received = request.file_version_ids.length > 0 && new Set(request.file_version_ids).size === request.file_version_ids.length && request.file_version_ids.every(id => blobs.has(id))
      const receipt: FeedbackReceipt = {request: structuredClone(request), attempt_id: attemptId, state: received ? 'received' : 'rejected', submission_id: received ? `preview-feedback-${crypto.randomUUID()}` : null, received_file_version_ids: received ? [...request.file_version_ids] : [], received_at: received ? new Date().toISOString() : null, retry_allowed: false, reason: received ? null : '附件版本不完整，本次未受理。'}
      submissions.set(key, receipt)
      if (scene === 'feedback-unknown' || scene === 'feedback-failed') throw Object.assign(new Error('preview_feedback_response_lost'), {traceId: 'preview-feedback-service-request'})
      if (scene === 'feedback-incomplete-receipt') return {...receipt, received_file_version_ids: receipt.received_file_version_ids.slice(0, 1)}
      return structuredClone(receipt)
    },
    read: async (request, attemptId) => {
      const result = submissions.get(`${request.request_id}:${attemptId}`)
      if (!result) throw new Error('unknown_feedback_submission')
      return structuredClone(result)
    },
    readAttachment: async file => {
      const blob = blobs.get(file.id)
      if (!blob) throw new Error('unknown_feedback_attachment')
      return {file_version_id: file.id, blob}
    },
  }
  return ['feedback-attachments', 'feedback-upload-resume'].includes(scene) ? {...adapter, submit: undefined, read: undefined} : adapter
}
