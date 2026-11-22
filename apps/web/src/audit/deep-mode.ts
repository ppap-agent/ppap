import type {components} from '../generated/openapi.ts'

type Task = components['schemas']['AuditTask']
type DeepReview = components['schemas']['AuditDeepReview']

export const deepModeTitle = '深度模式'
export const deepModeDescription = '规则范围与标准审核相同，另逐条复核每条结论，并核对同批文件之间是否一致，审核时长约增加 2 倍。'
export const deepReviewDegraded = '深度复核未完成，已保留标准审核结果'
const backgroundNote = '审核在后台进行，可关闭页面，完成后飞书通知你'

/** Files the estimate counts: the confirmation scope when the server gave one, else the matched files. */
export function billableFileCount(task: Pick<Task, 'confirmation_scope' | 'files'>): number {
  if (task.confirmation_scope) return task.confirmation_scope.target_file_version_ids.length
  return task.files.filter(row => row.selection === 'selected' && row.match?.state === 'matched').length
}

/** The published price per file, stated only when the server has not estimated this task. */
const listedPointsPerFile = 500

/** How the server's estimate was made: the price per file is read from the server's total; every mode costs the same per file. */
export function pointsBasis(task: Pick<Task, 'confirmation_scope' | 'files' | 'estimated_points'>): string {
  const files = billableFileCount(task), total = task.estimated_points
  const perFile = total != null && files > 0 && Number.isSafeInteger(total / files) ? total / files : listedPointsPerFile
  return `按 ${perFile} 点/份 × ${files} 份可送审文件计算；${backgroundNote}`
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
