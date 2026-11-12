import type {components} from '../generated/openapi.ts'

type Task = components['schemas']['AuditTask']
type DeepReview = components['schemas']['AuditDeepReview']

export const deepModeTitle = '深度模式'
export const deepModeDescription = '对关键文件做逐条追溯（跨文件一致性、条款级比对），审核时长增加约 2 倍，点数按 1.5 倍计。'
export const deepReviewDegraded = '深度复核未完成，已按标准结果计费'
const backgroundNote = '审核在后台进行，可关闭页面，完成后飞书通知你'

/** Files the estimate counts: the confirmation scope when the server gave one, else the matched files. */
export function billableFileCount(task: Pick<Task, 'confirmation_scope' | 'files'>): number {
  if (task.confirmation_scope) return task.confirmation_scope.target_file_version_ids.length
  return task.files.filter(row => row.selection === 'selected' && row.match?.state === 'matched').length
}

/** How the server's estimate was made; the number itself always comes from the server. */
export function pointsBasis(task: Pick<Task, 'confirmation_scope' | 'files' | 'deep_mode'>): string {
  const count = billableFileCount(task)
  return `按 500 点/份 × ${count} 份可送审文件${task.deep_mode ? ' × 1.5（深度模式）' : ''}计算；${backgroundNote}`
}

/** The switch is usable only while the task can still change and the server offers deep mode. */
export function deepModeControl(task: Pick<Task, 'state' | 'round_id' | 'deep_mode' | 'deep_mode_option'>, busy: boolean): {disabled: boolean; reason: string | null} {
  const option = task.deep_mode_option
  if (task.round_id || task.state !== 'pending_confirm') return {disabled: true, reason: null}
  // Turning deep mode off is always allowed; turning it on needs the deep runtime and a model.
  if (option && !option.available && !task.deep_mode) return {disabled: true, reason: option.reason || '深度模式暂不可用。'}
  return {disabled: busy, reason: null}
}

export function deepReviewNotice(review: DeepReview | null | undefined): string | null {
  return review?.state === 'degraded' ? deepReviewDegraded : null
}
