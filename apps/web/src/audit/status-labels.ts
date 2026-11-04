// One vocabulary for machine results across the workbench, task workspace and clause review.
export type ResultStatus = 'PASS' | 'NG' | 'UNABLE' | 'ERR' | 'NA'
export type ResultTone = 'destructive' | 'warning' | 'success' | 'outline'

export const resultLabel: Record<ResultStatus, string> = {PASS: '符合', NG: '不符合', UNABLE: '无法判断', ERR: '技术错误', NA: '不适用'}
export const uncoveredLabel = '未覆盖'
export const conclusionLabel: Record<string, string> = {PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项'}

export function resultTone(status: ResultStatus | 'uncovered' | 'UNKNOWN' | null | undefined): ResultTone {
  if (status === 'NG') return 'destructive'
  if (status === 'PASS') return 'success'
  if (status === 'UNABLE' || status === 'ERR' || status === 'uncovered' || status === 'UNKNOWN') return 'warning'
  return 'outline'
}
export const roundExecutionLabel: Record<string, string> = {queued: '排队中', running: '审核中', completed: '审核完成', failed: '审核异常'}
