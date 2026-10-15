import type {DecisionEvidence, DecisionInput} from './decision'
import {isObject} from './saved-draft'
import {sourceKey} from './source-documents'

// A server round owns immutable inputs. Presentation labels do not change its identity.
export function decisionInputKey(input: DecisionInput) {
  return input.roundId ? JSON.stringify({round: input.roundId,
    files: input.files.map(file => file.material?.file_version_id),
    rules: input.basis?.server?.ruleId,
    evidence: input.evidence.map(({id, requirement, result, sources}) => ({id, requirement, result, sources})),
  }) : JSON.stringify(input)
}

export type Citation = {source: string; location: string; excerpt: string}
export type Judgment = {
  value: 'pending' | 'confirm' | 'disagree' | 'unknown' | 'manual-satisfied' | 'manual-unsatisfied'
  reason: string; citations?: Citation[]; confirmed?: string
  pendingSave?: {roundId: string; request: import('../api').ReviewRequest}
}
export const judgmentLabels: Record<Judgment['value'], string> = {
  pending: '尚未判断', confirm: '确认机器结果', disagree: '不同意机器结果', unknown: '仍无法判断',
  'manual-satisfied': '人工确认满足要求', 'manual-unsatisfied': '人工确认不满足要求',
}
export const isManual = (judgment?: Judgment) => judgment?.value === 'manual-satisfied' || judgment?.value === 'manual-unsatisfied'
function validPendingSave(value: unknown) {
  if (!isObject(value) || typeof value.roundId !== 'string' || !isObject(value.request)) return false
  const request = value.request
  return ['item_key', 'judgment', 'rationale'].every(key => typeof request[key] === 'string') && Number.isInteger(request.version) && Number(request.version) >= 0
    && (request.citations === undefined || Array.isArray(request.citations) && request.citations.every(cite => isObject(cite) && ['file_version_id', 'location', 'excerpt'].every(key => typeof cite[key] === 'string')))
}
export function validJudgment(value: unknown): value is Judgment {
  return isObject(value) && Object.hasOwn(judgmentLabels, String(value.value)) && typeof value.reason === 'string'
    && (value.confirmed === undefined || typeof value.confirmed === 'string')
    && (value.citations === undefined || Array.isArray(value.citations) && value.citations.every(cite => isObject(cite) && ['source', 'location', 'excerpt'].every(key => typeof cite[key] === 'string')))
    && (value.pendingSave === undefined || validPendingSave(value.pendingSave))
}
export function judgmentKey(input: DecisionInput, row: DecisionEvidence, judgment: Judgment) {
  return JSON.stringify([decisionInputKey(input), row.id, judgment.value, judgment.reason, judgment.citations])
}
export function manualReady(input: DecisionInput, judgment: Judgment) {
  return Boolean(isManual(judgment) && judgment.reason.trim() && judgment.citations?.length
    && judgment.citations.every(cite => cite.location.trim() && cite.excerpt.trim()
      && input.files.filter(file => sourceKey(file) === cite.source && !file.pending).length === 1))
}
export function judgmentComplete(input: DecisionInput, row: DecisionEvidence, judgment?: Judgment) {
  if (!judgment || judgment.value === 'pending' || !judgment.reason.trim() || judgment.pendingSave) return false
  if (input.roundId && judgment.confirmed !== judgmentKey(input, row, judgment)) return false
  return isManual(judgment) ? manualReady(input, judgment) && judgment.confirmed === judgmentKey(input, row, judgment) : true
}
export function requirementSatisfied(input: DecisionInput, row: DecisionEvidence, judgment?: Judgment) {
  return judgmentComplete(input, row, judgment)
    && (judgment?.value === 'manual-satisfied' || row.result === '满足' && judgment?.value === 'confirm')
}

// A retry may change one result, not the files, requirements or receipt. Preserve
// only judgments whose entire row is unchanged; changed inputs invalidate all.
export function judgmentsAfterCheck(previous: DecisionInput, next: DecisionInput, judgments: Record<string, Judgment>) {
  const {evidence: oldRows, ...oldInput} = previous
  const {evidence: newRows, ...newInput} = next
  if (JSON.stringify(oldInput) !== JSON.stringify(newInput)) return {}
  return Object.fromEntries(newRows.flatMap(row => {
    const oldRow = oldRows.find(item => item.id === row.id), judgment = judgments[row.id]
    if (!oldRow || !judgment || JSON.stringify(oldRow) !== JSON.stringify(row)) return []
    return [[row.id, isManual(judgment) && judgmentComplete(previous, oldRow, judgment)
      ? {...judgment, confirmed: judgmentKey(next, row, judgment)} : judgment]]
  }))
}
