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
export const executionLabel = {queued: '排队中', running: '审核中', completed: '审核完成', review_failed: '审核异常'}
export const reportLabel = {not_eligible: '无正式报告', pending: '报告待生成', generating: '报告生成中', ready: '报告已就绪', failed: '报告生成失败'}
export const reportShortLabel = {not_eligible: '无正式报告', pending: '待生成', generating: '生成中', ready: '就绪', failed: '生成失败'}

// UAES source-owned document identities are stored as codes; people should only ever read their names.
const sourceDocumentNames: Record<string, string> = {E01: '初始样品检验报告', E02: '测量比较报告', E03: '包装方案', E04: '实验室资质证书', E05: '材料规范', E06: '样品阶段履历', E07: '生产件样品照片', E08: '订货规范', E09: '试装单', E10: '零件追溯说明', E11: '顾客工程批准', E12: '采购件质量计划'}
export const documentTypeLabel = (type: string) => sourceDocumentNames[type] ?? type
