import {knownRequestMessage} from '../api.ts'

export type FormalizationView = {ruleVersionId: string; clauseId: string; candidateId?: string}
export type FormalizationReceiptIdentity = {operation_id?: string; rule_version_id?: string; clause_id?: string; candidate_id?: string | null}

export function visibleFormalizationReadings<T extends {cnl_text: string}>(state: string, candidates: T[]): {current: T | null; previous: T | null; historical: T | null} {
  const latest = candidates.at(-1) ?? null
  if (state === 'source_changed') return {current: null, previous: null, historical: latest}
  const previous = candidates.length > 1 ? candidates.at(-2) ?? null : null
  return {current: latest, previous: previous && latest && previous.cnl_text !== latest.cnl_text ? previous : null, historical: null}
}

export function canRequestFormalization(state: string, operationState?: string): boolean {
  if (operationState === 'queued' || operationState === 'running' || operationState === 'unknown') return false
  return state === 'not_started' || state === 'source_changed' || operationState === 'stopped' || operationState === 'failed'
}

export function formalizationFailure(code?: string | null): string {
  const message = code && code !== 'source_changed' ? knownRequestMessage(code) : undefined
  return message ?? '这次解释没有完成。可以重新让系统解释。'
}

export function confirmedReadingAside<T extends {id: string; cnl_text: string}>(confirmedId: string | undefined, candidates: T[], shown: {id?: string; cnl_text?: string | null}[]): T | null {
  const reading = confirmedId ? candidates.find(item => item.id === confirmedId) : undefined
  if (!reading || shown.some(item => item.id === reading.id || item.cnl_text === reading.cnl_text)) return null
  return reading
}

export function textForCandidate(candidateId: string | undefined, selectedCandidateId: string, value: string): string {
  return candidateId !== undefined && candidateId === selectedCandidateId ? value : ''
}

export function sameFormalizationTarget(origin: {ruleVersionId: string; clauseId: string}, current: {ruleVersionId: string; clauseId: string}): boolean {
  return origin.ruleVersionId === current.ruleVersionId && origin.clauseId === current.clauseId
}

export function formalizationReceiptMatches(view: FormalizationView, receipt: FormalizationReceiptIdentity, operationId: string): boolean {
  if (!receipt.operation_id || receipt.operation_id !== operationId) return false
  if (!receipt.rule_version_id || receipt.rule_version_id !== view.ruleVersionId) return false
  if (!receipt.clause_id || receipt.clause_id !== view.clauseId) return false
  if (view.candidateId && receipt.candidate_id !== view.candidateId) return false
  return true
}


export type CheckerValidation = {verified?: boolean; samples_not_rerun?: boolean}

export function checkerAvailability(validation: CheckerValidation | null | undefined): 'verified' | 'unverified' | 'unknown' {
  if (!validation || typeof validation.verified !== 'boolean') return 'unknown'
  if (validation.verified === true && validation.samples_not_rerun === false) return 'verified'
  return 'unverified'
}

export function checkerAvailabilityCopy(state: 'verified' | 'unverified' | 'unknown'): string {
  if (state === 'verified') return '检查功能已验证，可用于审核。'
  if (state === 'unverified') return '检查功能仍需单独验证后才能用于审核。'
  return '检查状态暂时不能读取。'
}
