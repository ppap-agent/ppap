// Prototype-only change rounds. Each round owns a fixed input; no old PASS is copied.
import {completeSampleEvidence, decisionSummary, type DecisionInput} from './decision'
import {basisConfirmed, isKnownDemoClause, isRequirementBasis, type RequirementBasis} from './requirements-state'
import {clauseContent, scopeMatches, sourceContent, type RequirementCollection, type RequirementScope} from './requirement-library-state'
import {isObject, readDraft} from './saved-draft'
import {applyCheckCoverage, checkPlan, checkSummary, validCheck, type CheckPreparationState} from './check-preparation-state'
import {documentRows, sourceKey, type DemoDocument} from './source-documents'
import {optionalLocalFile, type LocalFileRef} from './local-files'

export type ChangeScope = RequirementScope & {supplier: string; plant: string}
export const changeScopeKey = (scope: ChangeScope) => JSON.stringify({customer: scope.customer, supplier: scope.supplier, part: scope.part, revision: scope.revision, plant: scope.plant})
export const changeScopeLabel = (scope: ChangeScope) => `${scope.customer} · ${scope.supplier} · ${scope.part} · 图纸 Rev ${scope.revision} · ${scope.plant}`
export const inputIdentity = (input: DecisionInput) => JSON.stringify([input.receiptId, input.scope, input.basis, input.files])
export type ChangeFile = {id: string; name: string; kind: string; revision: string; replaces: string; checked: boolean; note: string; localFile?: LocalFileRef}
export type ChangeDraft = {
  id: string; base: DecisionInput; rootKey: string; scope: ChangeScope; basis: RequirementBasis | null
  files: ChangeFile[]; reason: string; impacts: Record<string, string>; acknowledged: boolean
}
export type ChangeRound = {
  id: string; draft: ChangeDraft; input: DecisionInput; at: string
  state: 'prepared' | 'running' | 'failed' | 'done' | 'manual'; attempt: number; fail: boolean; check?: CheckPreparationState
  documents?: DemoDocument[]
}
export type ChangeState = {draft: ChangeDraft | null; rounds: ChangeRound[]; selected: string; editingBasis: boolean; decision: string | null}
export const changeDecisionId = (caseId: string, roundId: string) => `${caseId}:change:${roundId}`
export function freshChange(base: DecisionInput, scope: ChangeScope, rootKey: string): ChangeDraft {
  const basis = base.basis && structuredClone(base.basis)
  return {id: crypto.randomUUID(), base: structuredClone(base), rootKey, scope: {...scope}, basis: basis && {...basis, confirmed: null, clauses: basis.clauses.map(clause => ({...clause, checked: false, applicability: 'pending', reason: ''}))}, files: [], reason: '', impacts: {}, acknowledged: false}
}
export function clauseDifferences(before: RequirementBasis | null, after: RequirementBasis | null) {
  const ids = [...new Set([...(before?.clauses.map(clause => clause.id) ?? []), ...(after?.clauses.map(clause => clause.id) ?? [])])]
  return ids.map(id => {
    const old = before?.clauses.find(clause => clause.id === id), next = after?.clauses.find(clause => clause.id === id)
    const kind = !old ? '新增' : !next ? '移除' : clauseContent(old) !== clauseContent(next) ? '内容或出处变化' : next.applicability === 'pending' ? '适用性待确认' : old.applicability !== next.applicability || old.reason !== next.reason ? '适用性变化' : '内容未变'
    return {id, old, next, kind, needsImpact: !['内容未变', '适用性待确认'].includes(kind)}
  })
}
export function changeIssues(draft: ChangeDraft, base: DecisionInput, collections: RequirementCollection[]): string[] {
  const issues: string[] = []
  if (inputIdentity(draft.base) !== inputIdentity(base)) issues.push('已有更新的事项依据。请放弃本草稿，再从最新依据开始。')
  if (!basisConfirmed(draft.basis, changeScopeKey(draft.scope))) issues.push('确认新一轮的要求与适用范围。')
  const version = collections.flatMap(item => item.versions).find(item => item.id === (draft.basis?.library?.versionId ?? draft.basis?.source.id))
  if (version && !scopeMatches(draft.scope, version.scope)) issues.push('所选规则版本不适用于本次范围。')
  if (draft.basis?.library && !version) issues.push('所选要求来源版本不可读取。')
  if (!draft.reason.trim()) issues.push('填写变化来源与重审原因。')
  if (!draft.scope.revision.trim()) issues.push('核对本次图纸修订。')
  for (const file of draft.files) {
    if (!file.checked || !file.kind || !file.revision.trim() || !file.replaces || !file.note.trim()) issues.push(`${file.name}：核对类型、修订、关联和说明。`)
    if (file.replaces && file.replaces !== 'additional' && !draft.base.files.some((_, index) => String(index) === file.replaces)) issues.push(`${file.name}：替代目标不在当前资料中，请重新核对。`)
  }
  const replacements = draft.files.filter(file => file.replaces && file.replaces !== 'additional').map(file => file.replaces)
  if (new Set(replacements).size !== replacements.length) issues.push('同一旧文件有多个替代候选，请明确一个当前版本。')
  const oldRevision = (() => {try {return JSON.parse(draft.base.basis?.confirmed?.scope ?? '{}').revision} catch {return undefined}})()
  if (draft.scope.revision !== oldRevision && !draft.files.some(file => file.kind === '图纸' && file.checked && file.revision.trim() === draft.scope.revision.trim())) issues.push('改变图纸修订前，先上传并核对该修订的图纸。')
  const changed = draft.basis && draft.base.basis && sourceContent(draft.basis) !== sourceContent(draft.base.basis)
  const applicabilityChanged = clauseDifferences(draft.base.basis, draft.basis).some(row => row.kind === '适用性变化')
  if (!changed && !applicabilityChanged && !draft.files.length && draft.scope.revision === oldRevision) issues.push('尚未选择新的要求或文件。')
  for (const row of clauseDifferences(draft.base.basis, draft.basis)) {
    if (row.needsImpact && !draft.impacts[row.id]?.trim()) issues.push(`${row.id}：记录变化影响或移除理由。`)
  }
  return [...new Set(issues)]
}
export function newChangeInput(draft: ChangeDraft): DecisionInput {
  const replaced = new Set(draft.files.map(file => file.replaces))
  const files = draft.base.files.filter((_, index) => !replaced.has(String(index))).map(file => ({...file}))
  files.push(...draft.files.map(file => ({name: file.name, revision: file.revision, localFile: file.localFile, sourceVersion: file.replaces !== 'additional' ? (draft.base.files[Number(file.replaces)]?.sourceVersion ?? 0) + 1 : 1})))
  return {sample: draft.files.length ? undefined : draft.base.sample, receiptId: `CHANGE-${draft.id}`, scope: changeScopeLabel(draft.scope), basis: structuredClone(draft.basis), files, clarification: draft.base.clarification, evidence: [],
    receipts: [...(draft.base.receipts ?? [{id: draft.base.receiptId, source: '此前已登记资料', files: draft.base.files, notes: draft.base.clarification ?? ''}]), {id: `CHANGE-${draft.id}`, source: '变化评估', files: draft.files.map(file => ({name: file.name, revision: file.revision, localFile: file.localFile, sourceVersion: file.replaces !== 'additional' ? (draft.base.files[Number(file.replaces)]?.sourceVersion ?? 0) + 1 : 1})), notes: [draft.reason, ...Object.entries(draft.impacts).map(([id, reason]) => `${id}：${reason}`), ...draft.files.map(file => `${file.name}：${file.note}`)].join('\n')}],
  }
}
export function executeChange(round: ChangeRound): DecisionInput {
  const input = structuredClone(round.input), basis = input.basis
  if (!basis) return input
  // Named, known synthetic evidence is recalculated. Arbitrary uploads never inherit it.
  const names = input.files.map(file => file.name).sort()
  const complete = ['控制计划_B_一致.xlsx', '过程流程图_B_完整.pdf', '尺寸报告_B_完整.xlsx'].sort()
  const outlier = ['控制计划_B_一致.xlsx', '过程流程图_B_完整.pdf', '尺寸报告_B_超差.xlsx'].sort()
  const known = Boolean(input.sample) && round.draft.files.length === 0 && round.draft.scope.revision === 'B' && input.files.every(file => file.revision === 'B' && file.sourceVersion === 1) && (JSON.stringify(names) === JSON.stringify(complete) || JSON.stringify(names) === JSON.stringify(outlier))
  input.evidence = known ? completeSampleEvidence(basis, names.includes('尺寸报告_B_超差.xlsx')) : basis.clauses.map(clause => ({id: clause.id, title: clause.title, requirement: clause.text, result: clause.applicability === 'exclude' ? '不适用' : '未覆盖', explanation: clause.applicability === 'exclude' ? clause.reason : '当前资料或图纸没有对应的示例执行结果。旧结果不适用于本轮。', sources: []}))
  if (round.documents && round.draft.scope.revision === 'B') input.evidence = basis.clauses.map(clause => {
    const row = {id: clause.id, title: clause.title, requirement: clause.text}
    if (clause.applicability === 'exclude') return {...row, result: '不适用', explanation: clause.reason, sources: []}
    if (!isKnownDemoClause(clause)) return {...row, result: '未覆盖', explanation: '该条款超出示例检查范围；没有套用此前结论。', sources: []}
    const kinds = clause.id === 'R1' ? ['flow', 'control'] : clause.id === 'R2' ? ['flow'] : ['dimension']
    const kindsByLabel: Record<string, string> = {'过程流程图': 'flow', '控制计划': 'control', '尺寸报告': 'dimension'}
    if (round.draft.files.some(file => kinds.includes(kindsByLabel[file.kind]))) return {...row, result: '未覆盖', explanation: '本次新上传的关联原件尚未解析，不能只检查旧文件就确认满足。', sources: []}
    const docs = kinds.map(kind => {
      const candidates = round.documents!.filter(doc => doc.kind === kind && input.files.some(file => sourceKey(file) === sourceKey(doc)))
      return candidates.length === 1 ? candidates[0] : undefined
    })
    if (docs.some(doc => !doc)) return {...row, result: '未覆盖', explanation: '当前原件缺失、存在多个候选或没有可执行的示例内容。', sources: []}
    const [first, second] = docs as DemoDocument[]
    const sources = (docs as DemoDocument[]).map(doc => ({file: doc.name, location: `${doc.kind === 'flow' ? '第 3 页 · 工序列' : doc.kind === 'control' ? '控制计划 · A16:B18' : '尺寸 · B2、D8:F8'} · 原件 v${doc.sourceVersion} · ${doc.origin}`, excerpt: documentRows(doc).map(row => row.cells.join(' · ')).join('\n')}))
    if (docs.some(doc => doc!.revision !== 'B')) return {...row, result: '未覆盖', explanation: '所需原件不对应本轮 Rev B。', sources}
    if (clause.id === 'R1') return {...row, result: first.process !== second.process ? '不满足' : first.readable === true && second.readable === true ? '满足' : '无法判断', explanation: first.process !== second.process ? `工序 17：流程图为“${first.process}”，控制计划为“${second.process}”。` : first.readable === true && second.readable === true ? '示例工序 16、17、18 的名称一致；不证明现场工艺执行。' : '名称没有发现差异，但仍有不可读工序，无法确认全部一致。', sources}
    if (clause.id === 'R2') return {...row, result: first.readable === true ? '满足' : '无法判断', explanation: first.readable === true ? '示例工序号 16、17、18 清晰可辨。' : '当前流程图仍有无法辨认的工序号。', sources}
    const values = first.measurements
    return {...row, result: values?.length !== 3 ? '未覆盖' : values.every(value => value >= 9.9 && value <= 10.1) ? '满足' : '不满足', explanation: values?.length !== 3 ? '尚无 3 件样本的可核对实测值。' : `3 件实测为 ${values.join('、')} mm，逐件与 9.90–10.10 mm 比较。`, sources}
  })
  input.evidence = applyCheckCoverage(input.evidence, round.check?.request)
  if (round.fail) {
    const target = input.evidence.find(row => row.result !== '不适用')?.id
    input.evidence = input.evidence.map(row => row.id === target ? {...row, result: '执行失败', explanation: '模拟检查中断；其他条目结果保留。', sources: []} : row)
  }
  return input
}
export function changeCheckPlan(round: ChangeRound) {
  const files = round.input.files.map(file => {
    const docs = round.documents?.filter(doc => sourceKey(doc) === sourceKey(file)), doc = docs?.length === 1 ? docs[0] : undefined
    const declaredKind = round.draft.files.find(item => item.name === file.name)?.kind
    return {name: file.name, contentRevision: file.revision, sourceVersion: file.sourceVersion, kind: declaredKind ?? (doc ? ({control: '控制计划', flow: '过程流程图', dimension: '尺寸报告'}[doc.kind]) : file.name.startsWith('控制计划_') ? '控制计划' : file.name.startsWith('过程流程图_') ? '过程流程图' : file.name.startsWith('尺寸报告_') ? '尺寸报告' : ''), readability: doc?.readable === false ? 'partial' : 'readable', origin: round.input.receiptId, known: round.documents ? Boolean(doc) : undefined}
  })
  return checkPlan(round.input.basis!, files, round.input.receiptId, round.check!.mode, round.draft.scope.revision === 'B' && (round.documents !== undefined || Boolean(round.input.sample) && round.draft.files.length === 0))
}
function validInput(value: unknown): value is DecisionInput {return isObject(value) && typeof value.receiptId === 'string' && typeof value.scope === 'string' && (value.basis === null || isRequirementBasis(value.basis)) && Array.isArray(value.files) && value.files.every(file => isObject(file) && typeof file.name === 'string' && typeof file.revision === 'string' && typeof file.sourceVersion === 'number' && optionalLocalFile(file.localFile)) && Array.isArray(value.evidence)}
function validChange(value: unknown): value is ChangeDraft {
  return isObject(value) && ['id', 'rootKey', 'reason'].every(key => typeof value[key] === 'string') && validInput(value.base) && isObject(value.scope) && ['customer', 'supplier', 'part', 'revision', 'plant'].every(key => typeof (value.scope as Record<string, unknown>)[key] === 'string') && (value.basis === null || isRequirementBasis(value.basis)) && typeof value.acknowledged === 'boolean' && isObject(value.impacts) && Object.values(value.impacts).every(note => typeof note === 'string') && Array.isArray(value.files) && value.files.every(file => isObject(file) && ['id', 'name', 'kind', 'revision', 'replaces', 'note'].every(key => typeof file[key] === 'string') && typeof file.checked === 'boolean' && optionalLocalFile(file.localFile))
}
export function isChangeState(value: unknown): value is ChangeState {
  return isObject(value) && (value.draft === null || validChange(value.draft)) && typeof value.selected === 'string' && typeof value.editingBasis === 'boolean' && (value.decision === null || typeof value.decision === 'string') && Array.isArray(value.rounds) && value.rounds.every(round => isObject(round) && typeof round.id === 'string' && typeof round.at === 'string' && validChange(round.draft) && validInput(round.input) && ['prepared', 'running', 'failed', 'done', 'manual'].includes(String(round.state)) && typeof round.attempt === 'number' && typeof round.fail === 'boolean' && (round.check === undefined || validCheck(round.check)) && (round.documents === undefined || Array.isArray(round.documents) && round.documents.every(doc => isObject(doc) && ['name', 'revision', 'origin'].every(key => typeof doc[key] === 'string') && Number.isInteger(doc.sourceVersion) && Number(doc.sourceVersion) > 0 && ['flow', 'control', 'dimension'].includes(String(doc.kind)) && (doc.kind === 'dimension' ? doc.measurements === undefined || Array.isArray(doc.measurements) && doc.measurements.every(Number.isFinite) : typeof doc.process === 'string' && typeof doc.readable === 'boolean'))))
}
export function savedChangeSummary(id: string) {
  const state = readDraft(`changes:${id}`, isChangeState)?.value
  if (!state) return null
  if (state.draft) return {scope: state.draft.scope, pending: true, state: '变化待评估', next: '继续评估变化', detail: state.draft.reason || '已保存变更草稿；旧决定未改变，新范围尚未批准。'}
  const round = state.rounds.at(-1)
  if (!round) return null
  const decision = decisionSummary(`${id}:change:${round.id}`, id)
  return {scope: round.draft.scope, pending: false, ...(decision ?? (round.state === 'prepared' ? checkSummary(round.check) : null) ?? {state: round.state === 'running' ? '变化重查中' : round.state === 'failed' ? '变化检查部分失败' : '变化待复核', next: '继续变化重审', detail: `${round.input.basis?.source.version ?? '待确认要求'} · ${round.draft.scope.revision} · ${round.draft.reason}`})}
}
