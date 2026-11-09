// Prototype projection of existing check owners; no separate consumption ledger.
import {isObject, readDraft, scanDrafts, writeDraft} from './saved-draft'
import {modeName, observeCheck, validCheck, type CheckPreparationState, type CheckRequest} from './check-preparation-state'

export const exampleAllowance = 2000
export type CheckOwner = {kind: 'changes' | 'reassessment'; roundId: string}
export type UsageRow = {key: string; caseId: string; title: string; context: string; at: string; runId: string; status: string; detail: string; held: number | null; actual: number | null; attention: boolean; execution: string; request?: CheckRequest; owner?: CheckOwner}
type UsageRound = Record<string, unknown> & {state: string; check?: CheckPreparationState}
type UsageRounds = Record<string, unknown> & {rounds: UsageRound[] | Record<string, UsageRound>}
function validUsageRounds(value: unknown): value is UsageRounds {
  if (!isObject(value) || (!Array.isArray(value.rounds) && !isObject(value.rounds))) return false
  const rounds = Object.values(value.rounds)
  if (!rounds.every(round => isObject(round) && ['prepared', 'running', 'failed', 'done', 'manual'].includes(String(round.state)) && (round.check === undefined || validCheck(round.check)))) return false
  if (Array.isArray(value.rounds)) {
    const ids = rounds.map(round => round.id)
    return ids.every(id => typeof id === 'string' && id.length > 0) && new Set(ids).size === ids.length
  }
  return Object.keys(value.rounds).every(id => id.length > 0)
}
type UsageIntake = Record<string, unknown> & {scope: {part: string; supplier: string; revision: string}; submitted: boolean; progressScenario: string; check?: CheckPreparationState}
function validUsageIntake(value: unknown): value is UsageIntake {
  return isObject(value) && isObject(value.scope) && ['part', 'supplier', 'revision'].every(key => typeof (value.scope as Record<string, unknown>)[key] === 'string') && typeof value.submitted === 'boolean' && typeof value.progressScenario === 'string' && (value.check === undefined || validCheck(value.check))
}
function missingRow(key: string, caseId: string, title: string, at: string, detail: string): UsageRow {
  return {key, caseId, title, context: '历史检查', at, runId: key.split(':').at(-1) ?? key, status: '缺少用量记录', detail, held: null, actual: null, attention: true, execution: '历史记录'}
}
export function requestUsage(request: CheckRequest) {
  if (request.phase === 'absent') return {status: '未被接收', held: 0, actual: null, attention: false, detail: '已确认没有开始检查，预留已释放。'}
  if (request.phase !== 'accepted') return {status: '接收待确认', held: request.plan.estimate.high, actual: null, attention: true, detail: '先核对是否接收；此检查的预留继续保留。'}
  if (request.settlement?.phase === 'confirmed') return {status: '已结算', held: 0, actual: request.settlement.actual, attention: false, detail: '以已确认用量替换预留，不重复叠加。'}
  return {status: request.settlement?.phase === 'unknown' ? '结算待确认' : '待结算', held: request.plan.estimate.high, actual: null, attention: true, detail: request.settlement?.phase === 'unknown' ? '未取得结算结果，预留继续保留。' : '检查完成或失败都不等于结算完成。'}
}
export function readUsage(current?: {caseId: string; check: CheckPreparationState; owner?: CheckOwner}) {
  const intake = scanDrafts('intake:', validUsageIntake)
  const rows: UsageRow[] = []
  const titles = new Map<string, string>()
  for (const {key, value, updatedAt} of intake.records) {
    const caseId = key.slice('intake:'.length), title = value.scope.part || '未命名事项'
    titles.set(caseId, title)
    const check = current?.caseId === caseId && !current.owner ? current.check : value.check
    const request = check?.request
    if (request) rows.push({key, caseId, title, context: `${value.scope.supplier} · Rev ${value.scope.revision} · ${modeName(request.plan.mode)}`, at: request.at, runId: request.runId, execution: request.phase === 'accepted' ? ({complete: '检查已结束', failure: '检查局部失败', clarification: '检查待澄清', running: '检查进行中'}[value.progressScenario] ?? '进度待确认') : request.phase === 'absent' ? '未开始检查' : '尚待确认接收', request, ...requestUsage(request)})
    else if (value.submitted && check === undefined) rows.push(missingRow(key, caseId, title, updatedAt, '早期事项没有保存检查接收与用量；不能推断为零消费。'))
  }
  const change = scanDrafts('changes:', validUsageRounds), supplement = scanDrafts('reassessment:', validUsageRounds)
  for (const [kind, source] of [['changes', change], ['reassessment', supplement]] as const) for (const {key, value, updatedAt} of source.records) {
    const caseId = key.slice(kind.length + 1)
    for (const [index, round] of Object.entries(value.rounds)) {
      const roundId = kind === 'changes' ? String(round.id) : index, owner = {kind, roundId}
      const check = current?.caseId === caseId && current.owner?.kind === kind && current.owner.roundId === roundId ? current.check : round.check
      const request = check?.request, title = titles.get(caseId) ?? '复查事项'
      if (request) rows.push({key: `${key}:${roundId}`, caseId, owner, title, context: `${kind === 'changes' ? '要求或资料变化' : '补件复查'} · ${modeName(request.plan.mode)}`, at: request.at, runId: request.runId, execution: request.phase === 'absent' ? '未开始检查' : request.phase !== 'accepted' ? '尚未确认开始' : round.state === 'done' ? '复查已结束' : round.state === 'failed' ? '复查局部失败' : '等待或正在复查', request, ...requestUsage(request)})
      else if (!['prepared', 'manual'].includes(round.state)) rows.push({...missingRow(`${key}:${roundId}`, caseId, title, typeof round.at === 'string' ? round.at : updatedAt, '早期复查没有保存用量信息；不能推断为零消费。'), owner})
    }
  }
  const sources = [intake, change, supplement]
  const unreadable = sources.reduce((sum, source) => sum + source.invalidKeys.length, 0)
  const unavailable = sources.some(source => source.unavailable)
  const counts = new Map<string, number>()
  for (const row of rows.filter(row => row.request)) counts.set(row.runId, (counts.get(row.runId) ?? 0) + 1)
  const duplicate = [...counts.values()].some(count => count > 1)
  const incomplete = unavailable || unreadable > 0 || duplicate || rows.some(row => row.held === null)
  const unambiguous = rows.filter(row => !row.request || counts.get(row.runId) === 1)
  const held = unambiguous.reduce((sum, row) => sum + (row.held ?? 0), 0)
  const charged = unambiguous.reduce((sum, row) => sum + (row.actual ?? 0), 0)
  return {rows: rows.sort((a, b) => b.at.localeCompare(a.at)), held, charged, unreadable, unavailable, duplicate, incomplete, available: incomplete ? null : exampleAllowance - held - charged}
}
export type UsageOutcome = 'normal' | 'pending' | 'unknown' | 'failed' | 'zero'
export function reconcileUsage(caseId: string, runId: string, outcome: UsageOutcome): string {
  const matches = readUsage().rows.filter(row => row.request && row.runId === runId)
  if (matches.length > 1) return '检查号对应多条记录，不能确定用量归属；没有改动记录。'
  const row = matches[0]
  if (!row || row.caseId !== caseId) return '检查记录已改变或不可读取；没有改动原记录。'
  const key = `${row.owner?.kind ?? 'intake'}:${caseId}`
  const intake = row.owner ? null : readDraft(key, validUsageIntake)
  const rounds = row.owner ? readDraft(key, validUsageRounds) : null
  const round = rounds && (Array.isArray(rounds.value.rounds) ? rounds.value.rounds.find(item => item.id === row.owner?.roundId) : rounds.value.rounds[row.owner!.roundId])
  const check = round?.check ?? intake?.value.check, request = check?.request
  if (!check || !request || request.runId !== runId) return '检查记录已改变或不可读取；没有改动原记录。'
  if (request.phase === 'absent') return '已确认未被接收，没有新增消费。'
  if (request.settlement?.phase === 'confirmed') return `已确认 ${request.settlement.actual} 点；本次核对没有重复计入。`
  let next: CheckPreparationState
  let message: string
  if (request.phase !== 'accepted') {
    next = outcome === 'pending'
      ? {...check, request: {...request, phase: 'unknown', queryError: false}}
      : observeCheck({...check, queryFails: outcome === 'failed' || outcome === 'unknown'}, request.id, request.attempt, true)
    next.queryFails = check.queryFails
    message = next.request?.phase === 'accepted' ? '已确认接收；仍保留预留，实际用量待结算。' : next.request?.phase === 'absent' ? '已确认未被接收；释放本次预留，可回事项重试原请求。' : '接收仍待确认，预留没有释放。'
  } else {
    const canSettle = round ? round.state === 'done' : intake?.value.progressScenario === 'complete'
    const confirmed = canSettle && ['normal', 'zero'].includes(outcome)
    const actual = confirmed ? outcome === 'zero' ? 0 : Math.round(request.plan.estimate.low + (request.plan.estimate.high - request.plan.estimate.low) * .4) : null
    next = {...check, request: {...request, settlement: {phase: confirmed ? 'confirmed' : outcome === 'failed' || outcome === 'unknown' ? 'unknown' : 'pending', actual, at: new Date().toISOString()}}}
    message = confirmed ? `已取得模拟结算结果：${actual} 点，原预留已释放。` : outcome === 'failed' || outcome === 'unknown' ? '结算读取未成功，预留继续保留。' : canSettle ? '检查已结束，结算结果尚未就绪。' : '检查尚未结束；仍等待结算，不把中断或失败记为免费。'
  }
  try {
    if (rounds && round && row.owner) {
      const updated = {...round, check: next}
      const values = Array.isArray(rounds.value.rounds) ? rounds.value.rounds.map(item => item.id === row.owner!.roundId ? updated : item) : {...rounds.value.rounds, [row.owner.roundId]: updated}
      writeDraft(key, {...rounds.value, rounds: values})
    } else if (intake) writeDraft(key, {...intake.value, check: next})
    return message
  }
  catch {return '用量记录未能保存。原预留仍保留，请勿关闭尚未保存的事项。'}
}

export function usageHref(owner?: CheckOwner) {
  const query = new URLSearchParams(location.search)
  const url = new URL('/preview/intake.html', location.origin)
  url.searchParams.set('view', 'usage')
  const from = query.get('case') ?? query.get('fromCase')
  if (from) url.searchParams.set('fromCase', from)
  if (owner) {url.searchParams.set('fromKind', owner.kind); url.searchParams.set('fromRound', owner.roundId)}
  return url.pathname + url.search
}
