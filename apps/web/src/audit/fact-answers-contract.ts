import {request} from '../api.ts'
import type {components} from '../generated/openapi'

export type FactAnswersView = components['schemas']['FactAnswersView']
export type FactAnswerRequest = components['schemas']['FactAnswerRequest']
export type FactAnswerReceipt = components['schemas']['FactAnswerReceipt']
export type FactQuestion = FactAnswersView['questions'][number]

export interface FactAnswersAdapter {
  read(roundId: string): Promise<FactAnswersView>
  apply(roundId: string, input: FactAnswerRequest): Promise<FactAnswerReceipt>
  readOperation(roundId: string, operationId: string): Promise<FactAnswerReceipt | null>
}

const path = (roundId: string) => `/audit-rounds/${encodeURIComponent(roundId)}/fact-answers`

export function checkedFactAnswer(receipt: FactAnswerReceipt, roundId: string, operationId: string, input?: FactAnswerRequest) {
  if (receipt.round_id !== roundId || receipt.operation_id !== operationId || receipt.answer.round_id !== roundId || receipt.answer.operation_id !== operationId || receipt.model_calls !== 0 || input && (receipt.answer.file_version_id !== input.file_version_id || receipt.answer.field !== input.field || receipt.answer.value !== input.value)) throw new Error('回答结果与本次操作不一致，请查看原操作。')
  return receipt
}

export const nativeFactAnswersAdapter: FactAnswersAdapter = {
  async read(roundId) {
    const view = await request<FactAnswersView>(path(roundId))
    if (view.round_id !== roundId || view.model_calls !== 0) throw new Error('事实问题不属于本次审核，请重新读取。')
    return view
  },
  async apply(roundId, input) {
    return checkedFactAnswer(await request<FactAnswerReceipt>(path(roundId), 'POST', input), roundId, input.operation_id, input)
  },
  async readOperation(roundId, operationId) {
    const receipt = await request<FactAnswerReceipt | null>(`${path(roundId)}/${encodeURIComponent(operationId)}`)
    return receipt === null ? null : checkedFactAnswer(receipt, roundId, operationId)
  },
}

export function definiteFactAnswerRejection(cause: unknown) {
  const {status, code} = (cause as {status?: number; code?: string}) || {}
  if (status === 409) return ['fact_revision_conflict', 'fact_question_changed', 'fact_question_expired', 'fact_answer_conflict'].includes(code || '')
  return status !== undefined && [400, 401, 403, 404, 422].includes(status)
}

/** The server owns relevance; the UI only orders the remaining questions. */
export function groupFactQuestions(questions: FactQuestion[]) {
  const groups = new Map<string, FactQuestion[]>()
  for (const question of questions.filter(row => !row.answered)) {
    const group = groups.get(question.file_version_id) ?? []
    group.push(question); groups.set(question.file_version_id, group)
  }
  return Array.from(groups, ([fileVersionId, rows]) => {
    const sorted = [...rows].sort((a, b) => new Set(b.item_keys).size - new Set(a.item_keys).size || a.field.localeCompare(b.field))
    return {fileVersionId, visible: sorted.slice(0, 3), folded: sorted.slice(3)}
  })
}

export const factValueLabel = (value: boolean | string | null) => value === null ? '看不清' : typeof value === 'boolean' ? value ? '是' : '否' : value

/** Task display keys are clause IDs; human records retain the canonical machine key. */
export function factMachineFindings(checks: components['schemas']['AuditClauseEvaluation'][] = []) {
  return checks.flatMap(check => check.finding ? [{...check.finding, item_key: check.review_item_key ?? check.finding.item_key}] : [])
}
