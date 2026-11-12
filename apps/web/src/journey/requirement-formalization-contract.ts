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
  return message ?? '这条没整理出来。'
}

/**
 * The check points a person reads. Rendered lines start with internal obligation ids, and an incomplete
 * reading first lists diagnostics; only the readings and stated premises are shown, each numbered once.
 */
export function checkPointLines(text: string, obligationIds: string[] = []): string[] {
  let lines = text.split(/\n/)
  if (lines[0]?.trim() === '尚不能确定：') {
    const start = lines.findIndex(line => line.trim() === '候选理解（未确认）：')
    lines = [...start < 0 ? [] : lines.slice(start + 1), ...lines.filter(line => line.startsWith('已声明前提：'))]
  }
  const shown = lines
    .map(line => {
      const owner = obligationIds.find(id => line.startsWith(`${id}：`))
      return (owner ? line.slice(owner.length + 1) : line).replace(/^\s*(?:[-*•]|\d+[.、)）]|[（(]\d+[)）])\s*/, '').trim()
    })
    .filter(Boolean)
  return [...new Set(shown)]
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

/** What a clause waits for from a person, as the server classifies it. */
export type Attention = 'question' | 'review' | 'ready' | 'failed' | 'waiting' | 'running' | 'decided'
/** In the order a person should take them up; decided clauses come last. */
export const attentionLabels: [Attention, string][] = [['question', '要选择'], ['review', '要补充标准'], ['ready', '可以直接确认'], ['failed', '没整理出来'], ['waiting', '还没整理'], ['running', '还在整理'], ['decided', '已处理']]

/** Counts per decision, and whether anything still waits for the person or the model. */
export function attentionSummary(clauses: {attention?: string}[]): {counts: Partial<Record<Attention, number>>; open: number; running: number} {
  const counts: Partial<Record<Attention, number>> = {}
  for (const clause of clauses) {
    const key = attentionLabels.some(([value]) => value === clause.attention) ? clause.attention as Attention : 'waiting'
    counts[key] = (counts[key] ?? 0) + 1
  }
  return {counts, open: clauses.length - (counts.decided ?? 0) - (counts.running ?? 0), running: counts.running ?? 0}
}
