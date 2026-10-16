import type {components} from '../generated/openapi'
import type {Draft, DraftSaveRequest, Material, Round} from '../api'

type AuditTask = components['schemas']['AuditTask']

/** One frozen Product API input contract for V3.1 and the existing unified journey. */
export const inputItems = (items: Material[]): NonNullable<DraftSaveRequest['items']> => items.map(item => ({
  file_version_id: item.file_version_id,
  role: item.role,
  rule_version_id: item.role === 'target' ? item.rule_version_id : null,
}))

export const draftPlan = (draft: {items: Material[]; analysis_mode: Draft['analysis_mode']}) =>
  JSON.stringify({analysis_mode: draft.analysis_mode, items: inputItems(draft.items).sort((a, b) => a.file_version_id.localeCompare(b.file_version_id))})

export function sameAcceptedPlan(round: Round, draftId: string, plan: string) {
  return round.source_draft_id === draftId && draftPlan({items: round.inputs, analysis_mode: round.analysis_mode}) === plan
}

/** Every selected, matched V3.1 document is a target, even when it can also support another target's checks. */
export function matchedTaskMaterials(task: AuditTask): Material[] {
  if (task.state !== 'pending_confirm' || task.round_id !== null || ['pending', 'accepted'].includes(task.acceptance?.state ?? '')) {
    throw new Error('本次任务不是可确认的待审核规则状态，请读取原受理结果。')
  }
  const versions = new Set<string>()
  const materials: Material[] = []
  for (const row of task.files) {
    if (row.selection !== 'selected' || row.match?.state === 'unmatched') continue
    const match = row.match
    if (match?.state !== 'matched' || !match.rule) throw new Error('部分文件的规则尚未完成匹配，不能开始审核。')
    const rule = match.rule
    if (row.validation.state !== 'valid' || row.file.status !== 'ready') throw new Error('所选文件尚未完成预检查和上传。')
    if (!row.file.id || !row.file.file_id || !rule.id || rule.body.kind !== 'engine' || !rule.body.rules.length || rule.body.type !== match.document_type) {
      throw new Error('已匹配规则与文件类型或要求清单不一致，请重新读取匹配结果。')
    }
    if (versions.has(row.file.id)) throw new Error('同一文件版本在本轮审核中重复出现。')
    versions.add(row.file.id)
    materials.push({file_id: row.file.file_id, file_version_id: row.file.id, original_filename: row.file.original_filename, role: 'target', rule_version_id: rule.id})
  }
  if (!materials.length) throw new Error('没有已匹配的待审文件，不能创建审核轮次。')
  return materials
}

export type RoundCommands = {
  saveDraft: (draftId: string, body: DraftSaveRequest) => Promise<unknown>
  acceptDraft: (draftId: string, idempotencyKey: string) => Promise<Round>
}

/** Preserve existing Draft -> Round/Worker authority; no second task executor, ledger or billing path. */
export async function submitFrozenRound(
  input: {draftId: string; items: Material[]; analysisMode: Draft['analysis_mode']; idempotencyKey: string; caseContext?: DraftSaveRequest['case_context']},
  commands: RoundCommands,
): Promise<Round> {
  const {draftId, items, analysisMode, idempotencyKey, caseContext} = input
  if (!draftId || !idempotencyKey || !items.some(item => item.role === 'target')) throw new Error('审核草稿、受理请求或待审文件无效。')
  const plan = draftPlan({items, analysis_mode: analysisMode})
  const body: DraftSaveRequest = {analysis_mode: analysisMode, items: inputItems(items), ...(caseContext ? {case_context: caseContext} : {})}
  await commands.saveDraft(draftId, body)
  const round = await commands.acceptDraft(draftId, idempotencyKey)
  if (!sameAcceptedPlan(round, draftId, plan)) throw new Error('服务器受理轮次与冻结的文件及规则不一致，请核对原请求。')
  return round
}

/** APP-2 bridge. Calling this requires a real server draft; a UI fixture is not an accepted audit. */
export function submitMatchedTaskRound(task: AuditTask, idempotencyKey: string, commands: RoundCommands): Promise<Round> {
  if (!task.source_draft_id) throw new Error('本次任务没有可恢复的服务端审核草稿，不能直接提交。')
  return submitFrozenRound({
    draftId: task.source_draft_id,
    items: matchedTaskMaterials(task),
    analysisMode: task.analysis_mode,
    idempotencyKey,
  }, commands)
}
