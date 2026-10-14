// Prototype-only plan and acceptance record, owned by the existing intake draft.
import {isObject} from './saved-draft'
import {isKnownDemoClause, type RequirementBasis} from './requirements-state'

export type CheckMode = 'standard' | 'deep'
export type CoverageRow = {id: string; title: string; sources: string[]; state: 'ready' | 'investigate' | 'gap' | 'unsupported' | 'excluded'; detail: string}
export type CheckFile = {name: string; kind: string; contentRevision: string; sourceVersion: number; readability: string; origin: string; known?: boolean}
export type CheckPlan = {key: string; mode: CheckMode; rows: CoverageRow[]; estimate: {low: number; high: number; minutes: string}; receipt: string; basis: string; files: string[]}
export type StartOutcome = 'accepted' | 'unknown_accepted' | 'unknown_absent' | 'rejected' | 'waiting'
export type CheckRequest = {
  id: string; runId: string; plan: CheckPlan; attempt: number; at: string
  phase: 'requesting' | 'unknown' | 'accepted' | 'absent'
  remote: 'accepted' | 'absent' | 'pending'; queryError: boolean
  settlement?: {phase: 'pending' | 'unknown' | 'confirmed'; actual: number | null; at: string}
}
export type CheckPreparationState = {
  mode: CheckMode; quota: 'available' | 'low' | 'unavailable'
  outcome: StartOutcome; queryFails: boolean; request: CheckRequest | null
}
export const initialCheck = (): CheckPreparationState => ({mode: 'standard', quota: 'available', outcome: 'accepted', queryFails: false, request: null})
export const modeName = (mode: CheckMode) => mode === 'standard' ? '标准检查' : '深度审核'
export const quotaPoints = (quota: CheckPreparationState['quota']) => quota === 'available' ? 2000 : quota === 'low' ? 100 : null
export function checkPlan(basis: RequirementBasis, files: CheckFile[], receipt: string, mode: CheckMode, knownInput: boolean): CheckPlan {
  const rows: CoverageRow[] = basis.clauses.map(clause => {
    const row = {id: clause.id, title: clause.title, sources: [] as string[]}
    if (clause.applicability === 'exclude') return {...row, state: 'excluded', detail: clause.reason}
    if (!knownInput || !isKnownDemoClause(clause)) return {...row, sources: files.map(file => file.name), state: mode === 'deep' ? 'investigate' : 'unsupported', detail: mode === 'deep' ? '先调查条款与原件的对应关系；无法取得可靠证据时仍保留未覆盖。本原型没有此组合的预设结论。' : '条款含义或模板适配尚未确认；不会凭标题相似套用检查。'}
    const kinds = clause.id === 'R1' ? ['过程流程图', '控制计划'] : clause.id === 'R2' ? ['过程流程图'] : ['尺寸报告']
    const sources = files.filter(file => kinds.includes(file.kind))
    const needed = kinds.filter(kind => !sources.some(file => file.kind === kind))
    if (needed.length) return {...row, sources: sources.map(file => file.name), state: 'gap', detail: `缺少${needed.join('、')}；该要求保留未覆盖，其他有资料的要求可继续。`}
    if (sources.some(file => file.known === false)) return {...row, sources: sources.map(file => file.name), state: mode === 'deep' ? 'investigate' : 'unsupported', detail: mode === 'deep' ? '调查新原件与条款的对应关系；不能沿用此前文件的结论。' : '新原件尚未确认适配；该条款不套用此前文件的检查。'}
    if (clause.id === 'R3' && sources.some(file => file.contentRevision !== 'B')) return {...row, sources: sources.map(file => file.name), state: 'gap', detail: '现有尺寸报告不对应 Rev B，不能用于本次尺寸结论。'}
    return {...row, sources: sources.map(file => file.name), state: 'ready', detail: clause.id === 'R1' ? '按同一工序号比较流程图与控制计划中的名称。' : clause.id === 'R2' ? sources.some(file => file.readability === 'partial') ? '已知第 3 页局部不清；检查可读性，无法辨认处保留无法判断。' : '逐项核对纳入范围的工序号是否可辨认。' : '核对报告修订，并逐件比较实测值与本次公差。'}
  })
  return {key: JSON.stringify({receipt, files, basis}), mode, rows, receipt, basis: `${basis.source.title} ${basis.source.version}`, files: files.map(file => `${file.name} · v${file.sourceVersion} · Rev ${file.contentRevision}`), estimate: mode === 'standard' ? {low: 60, high: 90, minutes: '1–3'} : {low: 240, high: 480, minutes: '5–15'}}
}
// Execution must respect the accepted plan, not merely display the chosen mode.
export function applyCheckCoverage<T extends {id: string; result: string; explanation: string; sources: unknown[]}>(rows: T[], request?: CheckRequest | null): T[] {
  if (!request) return rows
  return rows.map(row => {
    const planned = request.plan.rows.find(item => item.id === row.id)
    return planned && ['unsupported', 'gap'].includes(planned.state) ? {...row, result: '未覆盖', explanation: planned.detail, sources: []} : row
  })
}
export function canStartCheck(state: CheckPreparationState, plan: CheckPlan, sharedAvailable: number | null = quotaPoints(state.quota)): boolean {
  const request = state.request
  return (!request || request.phase === 'absent') && (!request || request.plan.key === plan.key)
    && Math.min(quotaPoints(state.quota) ?? -1, sharedAvailable ?? -1) >= (request?.plan.estimate.high ?? plan.estimate.high)
    && (request?.plan.rows ?? plan.rows).some(row => row.state === 'ready' || row.state === 'investigate')
}
export function startCheck(state: CheckPreparationState, plan: CheckPlan, sharedAvailable: number | null = quotaPoints(state.quota)): CheckPreparationState {
  if (!canStartCheck(state, plan, sharedAvailable)) return state
  const id = state.request?.id ?? `REQ-${crypto.randomUUID().slice(0, 8)}`
  return {...state, request: {id, runId: state.request?.runId ?? `CHK-${id.slice(4)}`, plan: state.request?.plan ?? structuredClone(plan), attempt: (state.request?.attempt ?? 0) + 1, at: new Date().toISOString(), phase: 'requesting', remote: state.outcome === 'waiting' ? 'pending' : ['unknown_absent', 'rejected'].includes(state.outcome) ? 'absent' : 'accepted', queryError: false}}
}
export function observeCheck(state: CheckPreparationState, id: string, attempt: number, query: boolean): CheckPreparationState {
  const request = state.request
  if (!request || request.id !== id || request.attempt !== attempt || !['requesting', 'unknown'].includes(request.phase)) return state
  const failed = query && state.queryFails
  const unknown = failed || request.remote === 'pending' || !query && state.outcome.startsWith('unknown_')
  return {...state, request: {...request, phase: unknown ? 'unknown' : request.remote === 'accepted' ? 'accepted' : 'absent', queryError: failed}}
}
export function recoverCheck(state: CheckPreparationState): CheckPreparationState {
  return state.request?.phase === 'requesting' ? {...state, request: {...state.request, phase: 'unknown'}} : state
}
export function checkSummary(state: CheckPreparationState | undefined) {
  if (!state?.request) return {state: '待开始检查', next: '准备检查', detail: '资料已登记；尚未发起检查。'}
  const request = state.request
  if (request.phase === 'accepted') return null
  return {state: request.phase === 'absent' ? '检查未受理' : '受理待核对', next: request.phase === 'absent' ? '继续准备检查' : '核对受理状态', detail: `${request.id} · ${modeName(request.plan.mode)} · ${request.phase === 'absent' ? '已确认没有开始，可重试原请求。' : '还不能确认是否开始；先核对，不重复提交。'}`}
}
export function validCheck(value: unknown): value is CheckPreparationState {
  if (!isObject(value) || !['standard', 'deep'].includes(String(value.mode)) || !['available', 'low', 'unavailable'].includes(String(value.quota)) || !['accepted', 'unknown_accepted', 'unknown_absent', 'rejected', 'waiting'].includes(String(value.outcome)) || typeof value.queryFails !== 'boolean') return false
  const request = value.request
  if (request === null) return true
  if (!isObject(request) || !['id', 'runId', 'at'].every(key => typeof request[key] === 'string') || !Number.isInteger(request.attempt) || Number(request.attempt) < 1 || !['requesting', 'unknown', 'accepted', 'absent'].includes(String(request.phase)) || !['accepted', 'absent', 'pending'].includes(String(request.remote)) || typeof request.queryError !== 'boolean' || !isObject(request.plan)) return false
  const plan = request.plan
  const settlement = request.settlement
  if (settlement !== undefined && (!isObject(settlement) || !['pending', 'unknown', 'confirmed'].includes(String(settlement.phase)) || typeof settlement.at !== 'string' || (settlement.phase === 'confirmed' ? request.phase !== 'accepted' || !Number.isInteger(settlement.actual) || Number(settlement.actual) < 0 : settlement.actual !== null))) return false
  return ['key', 'receipt', 'basis'].every(key => typeof plan[key] === 'string') && ['standard', 'deep'].includes(String(plan.mode)) && Array.isArray(plan.files) && plan.files.every(file => typeof file === 'string')
    && isObject(plan.estimate) && Number.isFinite(plan.estimate.low) && Number(plan.estimate.low) >= 0 && Number.isFinite(plan.estimate.high) && Number(plan.estimate.high) >= Number(plan.estimate.low) && typeof plan.estimate.minutes === 'string'
    && Array.isArray(plan.rows) && plan.rows.every(row => isObject(row) && ['id', 'title', 'detail'].every(key => typeof row[key] === 'string') && Array.isArray(row.sources) && row.sources.every(source => typeof source === 'string') && ['ready', 'investigate', 'gap', 'unsupported', 'excluded'].includes(String(row.state)))
}
