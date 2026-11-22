// One vocabulary for machine results across the workbench, task workspace, clause review and the document add-on.
import type {components} from '../generated/openapi'

export type ResultStatus = 'PASS' | 'NG' | 'UNABLE' | 'ERR' | 'NA'
export type ResultTone = 'destructive' | 'warning' | 'success' | 'outline'

export const resultLabel: Record<ResultStatus, string> = {PASS: '符合', NG: '不符合', UNABLE: '待审核', ERR: '技术错误', NA: '不适用'}
export const uncoveredLabel = '未覆盖'
export const conclusionLabel: Record<string, string> = {PASS: '符合', NG: '不符合', UNKNOWN: '待审核', ERROR: '审核异常'}

export function resultTone(status: ResultStatus | 'uncovered' | 'UNKNOWN' | null | undefined): ResultTone {
  if (status === 'NG') return 'destructive'
  if (status === 'PASS') return 'success'
  if (status === 'UNABLE' || status === 'ERR' || status === 'uncovered' || status === 'UNKNOWN') return 'warning'
  return 'outline'
}
export const executionLabel = {queued: '排队中', running: '审核中', completed: '审核完成', review_failed: '审核异常'}
/** One clause's state in words; a clause without a result is 待审核, never a guess. */
export function checkStatusLabel(check: Pick<components['schemas']['AuditClauseEvaluation'], 'execution' | 'coverage' | 'finding'>): string {
  if (check.execution === 'queued') return executionLabel.queued
  if (check.execution === 'running') return executionLabel.running
  if (check.execution === 'failed') return resultLabel.ERR
  if (check.coverage !== 'executed') return uncoveredLabel
  return resultLabel[check.finding?.status ?? 'UNABLE']
}
export const reportLabel = {not_eligible: '无正式报告', pending: '报告待生成', generating: '报告生成中', ready: '报告已就绪', failed: '报告生成失败'}
export const reportShortLabel = {not_eligible: '无正式报告', pending: '待生成', generating: '生成中', ready: '就绪', failed: '生成失败'}

