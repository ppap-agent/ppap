import {request} from '../api.ts'
import type {components} from '../generated/openapi'

export type DeepFeedbackView = components['schemas']['DeepFeedbackView']
export type RuleChangeRequest = components['schemas']['RuleChangeRequest']
export type RuleChangeReceipt = components['schemas']['RuleChangeReceipt']
export type McpRuleChangeProposal = components['schemas']['McpRuleChangeProposal']

export interface DeepFeedbackAdapter {
  read(roundId: string): Promise<DeepFeedbackView>
  apply(roundId: string, input: RuleChangeRequest, workspaceId: string): Promise<RuleChangeReceipt>
  readOperation(roundId: string, operationId: string, workspaceId: string): Promise<RuleChangeReceipt | null>
  readProposal(roundId: string, proposalId: string): Promise<McpRuleChangeProposal>
}

const feedbackPath = (roundId: string) => `/audit-rounds/${encodeURIComponent(roundId)}`

async function feedbackRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 45_000)
  try {return await request<T>(path, method, body, controller.signal)}
  finally {clearTimeout(timer)}
}

export const nativeDeepFeedbackAdapter: DeepFeedbackAdapter = {
  read: roundId => feedbackRequest(`${feedbackPath(roundId)}/deep-feedback`),
  async apply(roundId, input, workspaceId) {
    const receipt = await feedbackRequest<RuleChangeReceipt>(`${feedbackPath(roundId)}/rule-changes`, 'POST', input)
    return checkedRuleChange(receipt, workspaceId, input.operation_id)
  },
  async readOperation(roundId, operationId, workspaceId) {
    const receipt = await feedbackRequest<RuleChangeReceipt | null>(`${feedbackPath(roundId)}/rule-changes/${encodeURIComponent(operationId)}`)
    return receipt === null ? null : checkedRuleChange(receipt, workspaceId, operationId)
  },
  readProposal: (roundId, proposalId) => feedbackRequest(`${feedbackPath(roundId)}/mcp-rule-changes/${encodeURIComponent(proposalId)}`),
}

export function checkedRuleChange(receipt: RuleChangeReceipt, workspaceId: string, operationId: string) {
  if (receipt.operation_id !== operationId || receipt.workspace_id !== workspaceId) throw new Error('保存回执与本次操作不一致，请读取原操作。')
  return receipt
}

export function definiteRuleChangeRejection(failure: unknown) {
  const {status, code} = (failure as {status?: number; code?: string}) || {}
  if (status === 409) return ['feedback_revision_conflict', 'feedback_scope_conflict', 'feedback_question_expired', 'feedback_candidate_changed', 'feedback_question_changed'].includes(code || '')
  return status !== undefined && [400, 401, 403, 404, 422].includes(status)
}
