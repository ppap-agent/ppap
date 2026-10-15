import type {AuditFeedbackAdapter, FeedbackReceipt} from '../src/audit/feedback-contract'
import type {components} from '../src/generated/openapi'

/** In-memory UI fixture only. Does not write Bitable, publish rules, or create tasks. */
export function fixtureFeedback(scene: string): AuditFeedbackAdapter {
  const transfers = new Map<string, components['schemas']['AuditUploadItem']>()
  const blobs = new Map<string, Blob>()
  const submissions = new Map<string, FeedbackReceipt>()
  const inputs = new Map<string, string>()
  const uploadAttempts = new WeakMap<File, number>()
  return {
    upload: async (input, requestId) => {
      const existing = transfers.get(requestId)
      if (existing) return structuredClone(existing)
      if (input.kind !== 'local') throw new Error('feedback_local_fixture_only')
      const source = input.file
      const attempt = (uploadAttempts.get(source) || 0) + 1
      uploadAttempts.set(source, attempt)
      if (scene === 'feedback-upload-failed' && source.name === '需要重试.xlsx' && attempt === 1) {
        const failed: components['schemas']['AuditUploadItem'] = {request_id: requestId, state: 'failed', file: null, reason: '示例：上传已明确终结，没有在途写入，允许重试。', retry_allowed: true}
        transfers.set(requestId, failed)
        return structuredClone(failed)
      }
      const reason = !/\.(zip|xlsx|xls|xlsm|doc|docx|png|jpg|jpeg|webp|gif|bmp|tif|tiff)$/i.test(source.name) ? '不支持此附件格式。请使用 ZIP、Excel、Word 或图片。' : !source.size ? '附件为空，请移除后选择有内容的文件。' : source.size > 200 * 1024 * 1024 ? '附件超过 200 MB。请移除后重新选择。' : ''
      const file = reason ? null : {file_id: `preview-feedback-file-${crypto.randomUUID()}`, id: `preview-feedback-version-${crypto.randomUUID()}`, original_filename: source.name, content_type: source.type || 'application/octet-stream', byte_size: source.size, status: 'ready', sha256: null}
      if (file) blobs.set(file.id, new Blob([await source.arrayBuffer()], {type: file.content_type}))
      const receipt: components['schemas']['AuditUploadItem'] = {request_id: requestId, state: file ? 'ready' : 'failed', file, reason: reason || null, retry_allowed: false}
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
      if (scene === 'feedback-failed' && !original) {
        const failed: FeedbackReceipt = {request: structuredClone(request), attempt_id: attemptId, state: 'failed', submission_id: null, received_file_version_ids: [], received_at: null, retry_allowed: true, reason: '示例：服务已明确终结本次写入，没有未决运营记录，允许重试原材料。'}
        submissions.set(key, failed)
        return structuredClone(failed)
      }
      const received = request.file_version_ids.length > 0 && new Set(request.file_version_ids).size === request.file_version_ids.length && request.file_version_ids.every(id => blobs.has(id))
      const receipt: FeedbackReceipt = {request: structuredClone(request), attempt_id: attemptId, state: received ? 'received' : 'rejected', submission_id: received ? `preview-feedback-${crypto.randomUUID()}` : null, received_file_version_ids: received ? [...request.file_version_ids] : [], received_at: received ? new Date().toISOString() : null, retry_allowed: false, reason: received ? null : '示例：附件版本不完整，本次未受理。'}
      submissions.set(key, receipt)
      if (scene === 'feedback-unknown' || scene === 'feedback-failed') throw new Error('preview_feedback_response_lost')
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
}
