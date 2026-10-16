import type {components} from '../generated/openapi'
import type {UploadAdapter} from './uploads'

export type FeedbackRequest = components['schemas']['AuditTemplateFeedbackRequest']
export type FeedbackReceipt = components['schemas']['AuditTemplateFeedbackReceipt']
export type FeedbackFile = components['schemas']['FileVersion']
export const feedbackFileAccept = '.zip,.xlsx,.xls,.xlsm,.doc,.docx,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff'
export function feedbackUploadError(file: Pick<File, 'name' | 'size'>) {
  return !feedbackFileAccept.split(',').includes(file.name.slice(file.name.lastIndexOf('.')).toLowerCase()) ? '不支持此附件格式。请使用 ZIP、Excel、Word 或图片。' : !file.size ? '附件为空，请移除后选择有内容的文件。' : file.size > 200 * 1024 * 1024 ? '附件超过 200 MB。请移除后重新选择。' : ''
}
export type AuditFeedbackAdapter = UploadAdapter & {
  submit?: (request: FeedbackRequest, attemptId: string) => Promise<FeedbackReceipt>
  read?: (request: FeedbackRequest, attemptId: string) => Promise<FeedbackReceipt>
  readAttachment: (file: FeedbackFile) => Promise<{file_version_id: string; blob: Blob}>
}

export function validFeedbackReceipt(update: FeedbackReceipt, request: FeedbackRequest, attemptId: string) {
  const expected = request.file_version_ids
  if (!request.request_id || !attemptId || !expected.length || expected.some(id => !id) || new Set(expected).size !== expected.length) return false
  if (update.request.request_id !== request.request_id || update.attempt_id !== attemptId || update.request.file_version_ids.length !== expected.length || update.request.file_version_ids.some((id, index) => id !== expected[index])) return false
  if (update.state !== 'received') return ['pending', 'rejected', 'failed', 'unknown'].includes(update.state) && !update.submission_id && !update.received_at && update.received_file_version_ids.length === 0
  const received = update.received_file_version_ids
  return Boolean(update.submission_id?.trim() && update.received_at && Number.isFinite(Date.parse(update.received_at)) && !update.retry_allowed && received.length === expected.length && new Set(received).size === expected.length && received.every(id => expected.includes(id)))
}

export async function readFeedbackAttachment(adapter: AuditFeedbackAdapter, file: FeedbackFile) {
  if (file.status !== 'ready' || !file.id || file.byte_size <= 0) throw new Error('feedback_attachment_not_ready')
  const result = await adapter.readAttachment(structuredClone(file))
  if (result.file_version_id !== file.id || result.blob.size !== file.byte_size || result.blob.type !== file.content_type) throw new Error('feedback_attachment_mismatch')
  return result.blob
}
