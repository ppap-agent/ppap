import {isPreview} from './access'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch: projects/13261964020862199279/screens/2886cf1575be44bbb169efc64abc14fe.
// One review workspace; decision and delivery remain separate user tasks.
// Uploaded originals and synthetic examples stay distinct. Not a production approval system.
import {useContext, useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, ChevronDown} from 'lucide-react'
import {Button} from '../components/ui/button'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {BasisSummary} from './requirements'
import {isKnownDemoClause, type RequirementBasis} from './requirements-state'
import {isObject, loadDraft, readDraft, useSavedDraft} from './saved-draft'
import {SourceReaderButton} from './source-reader'
import {mergeDocuments, sourceKey, type DemoDocument} from './source-documents'
import {ReceiptCheckActions, ReceiptCheckPreparation, readReceiptCheck, receiptReviewState, receiptInputKey, useReceiptRecheck} from './receipt-recheck'
import {ReviewWorkspace} from './review-workspace'
import {RoundReviewContext, serverJudgments} from './review-record'
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../components/ui/dropdown-menu'
import {decisionInputKey, judgmentComplete, judgmentLabels, judgmentsAfterCheck, requirementSatisfied, validJudgment, type Judgment} from './judgment-state'
import {DeliveryWorkspace} from './delivery'
import {deliveryLabel, recoveredDelivery, validDelivery, type DeliveryState} from './delivery-state'
import {FollowupWorkspace, currentRequest, requestLabel, emptyFollowup, receiveReply, validDecisionInput, validFollowup, type FollowupState} from './followup'
export type DecisionEvidence = {
  displayId?: string
  id: string; title: string; requirement: string
  result: '满足' | '不满足' | '无法判断' | '未覆盖' | '执行失败' | '不适用'
  explanation: string; sources: {file: string; fileVersionId?: string; location: string; excerpt: string}[]
}
export type DecisionInput = {
  roundId?: string
  sample?: 'complete' | 'outlier'
  receiptId: string; scope: string; basis: RequirementBasis | null
  files: import('./source-documents').SourceFile[]
  clarification: string | null; evidence: DecisionEvidence[]
  receipts?: {id: string; source: string; files: import('./source-documents').SourceFile[]; notes: string}[]
}
export function completeSampleEvidence(basis: RequirementBasis, outlier = false): DecisionEvidence[] {
  const measurements = outlier ? '10.02、10.14、9.98 mm' : '10.02、10.04、9.98 mm'
  const facts: Record<string, Pick<DecisionEvidence, 'result' | 'explanation' | 'sources'>> = {
    R1: {result: '满足', explanation: '本样例全部纳入比较的工序号为 16、17、18，对应名称一致。只证明文件一致，不证明现场工艺执行。', sources: [
      {file: '过程流程图_B_完整.pdf', location: '第 3 页 · 工序 16–18', excerpt: '16 机加工\n17 热处理\n18 表面检查'},
      {file: '控制计划_B_一致.xlsx', location: '工作表「控制计划」· A16:B18', excerpt: '16 机加工\n17 热处理\n18 表面检查'},
    ]},
    R2: {result: '满足', explanation: '纳入核对的工序号 16、17、18 均清晰可辨。', sources: [{file: '过程流程图_B_完整.pdf', location: '第 3 页 · 工序号列', excerpt: '16 → 17 → 18'}]},
    R3: {result: outlier ? '不满足' : '满足', explanation: outlier ? '第 2 件实测 10.14 mm，大于 10.10 mm 上限。' : '报告 Rev B，3 件样本均在 9.90–10.10 mm 内。', sources: [{file: outlier ? '尺寸报告_B_超差.xlsx' : '尺寸报告_B_完整.xlsx', location: '工作表「尺寸」· B2（修订）、D8:F8（孔径）', excerpt: `图纸修订 B\nφ10.00±0.10 mm\n样本 1、2、3：${measurements}`}]},
  }
  return basis.clauses.map(clause => ({id: clause.id, title: clause.title, requirement: clause.text,
    ...(clause.applicability === 'exclude' ? {result: '不适用' as const, explanation: clause.reason, sources: []} : isKnownDemoClause(clause) ? facts[clause.id] : {result: '未覆盖' as const, explanation: '此条款与已核对的示例执行范围不同，没有对应检查结果。', sources: []}),
  }))
}
export function unexecutedEvidence(basis: RequirementBasis): DecisionEvidence[] {
  return basis.clauses.map(clause => ({id: clause.id, title: clause.title, requirement: clause.text,
    result: clause.applicability === 'exclude' ? '不适用' : '未覆盖',
    explanation: clause.applicability === 'exclude' ? clause.reason : '没有本条要求的机器检查结果。请核对原件并单独记录人工判断。', sources: []}))
}
export type RecordedDecision = {
  id: string; outcome: '批准本次范围' | '不批准'; by: string; reason: string; at: string
  input: DecisionInput; judgments: Record<string, Judgment>
  followup?: FollowupState
}
type DecisionDraft = {
  inputKey: string; judgments: Record<string, Judgment>; signer: string; reason: string
  choice: '' | RecordedDecision['outcome']; acknowledged: boolean; record: RecordedDecision | null
  report: 'idle' | 'generating' | 'failed' | 'ready'; downloadRequested: string | null
  delivery: {recipient: string; channel: string; note: string; at: string} | null
  deliveryDraft?: {recipient: string; channel: string; note: string; acknowledged: boolean}
  fulfillment?: DeliveryState
  workingInput?: DecisionInput; followup?: FollowupState
  appliedCheck?: string
}
export function validDraft(value: unknown): value is DecisionDraft {
  if (!isObject(value) || !['inputKey', 'signer', 'reason'].every(key => typeof value[key] === 'string') || !isObject(value.judgments)) return false
  if (!Object.values(value.judgments).every(validJudgment)) return false
  if (value.appliedCheck !== undefined && typeof value.appliedCheck !== 'string') return false
  if (value.workingInput !== undefined && !validDecisionInput(value.workingInput) || value.followup !== undefined && !validFollowup(value.followup)) return false
  return (value.fulfillment === undefined || validDelivery(value.fulfillment))
    && (value.deliveryDraft === undefined || isObject(value.deliveryDraft) && ['recipient', 'channel', 'note'].every(key => typeof (value.deliveryDraft as Record<string, unknown>)[key] === 'string') && typeof value.deliveryDraft.acknowledged === 'boolean')
    && ['', '批准本次范围', '不批准'].includes(String(value.choice)) && typeof value.acknowledged === 'boolean'
    && ['idle', 'generating', 'failed', 'ready'].includes(String(value.report))
    && (value.downloadRequested === null || typeof value.downloadRequested === 'string')
    && (value.delivery === null || isObject(value.delivery) && ['recipient', 'channel', 'note', 'at'].every(key => typeof (value.delivery as Record<string, unknown>)[key] === 'string'))
    && (value.record === null || isObject(value.record) && ['批准本次范围', '不批准'].includes(String(value.record.outcome)) && ['id', 'by', 'reason', 'at'].every(key => typeof (value.record as Record<string, unknown>)[key] === 'string') && validDecisionInput(value.record.input) && isObject(value.record.judgments) && Object.values(value.record.judgments).every(validJudgment) && (value.record.followup === undefined || validFollowup(value.record.followup)))
}
export function decisionSummary(id: string, caseId = id) {
  const draft = readDraft(`decision:${id}`, validDraft)?.value
  if (!draft) return null
  if (draft.record) return {recorded: true, state: `${draft.record.outcome} · ${draft.fulfillment ? deliveryLabel(recoveredDelivery(draft.fulfillment), Boolean(draft.delivery)) : draft.delivery ? '已登记交付' : '待交付'}`, next: '查看决定与报告', detail: `${draft.record.input.receiptId} · ${draft.record.reason}`}
  let input: DecisionInput
  try {input = draft.workingInput ?? JSON.parse(draft.inputKey); if (!Array.isArray(input?.evidence) || !Array.isArray(input?.files)) return null} catch {return null}
  if (draft.followup?.draft) return {recorded: false, state: '回件登记中', next: '继续处理', detail: '本批回件尚未登记，不能沿用旧判断作出决定。'}
  const check = readReceiptCheck(caseId, id, input, draft.appliedCheck)
  if (check.error) return {recorded: false, state: '检查记录待恢复', next: '继续处理', detail: check.error}
  if (check.pending) return {recorded: false, state: check.awaitingReview ? '新检查结果待复核' : '本批检查待完成', next: '继续复核', detail: check.awaitingReview ? '先将本次结果纳入复核，再确认人工判断和决定。' : '检查中或受理结果待核对，尚不能作出决定。'}
  const relevant = input.evidence.filter(row => row.result !== '不适用')
  const reviewed = relevant.filter(row => judgmentComplete(input, row, draft.judgments[row.id]))
  const complete = relevant.length > 0 && reviewed.length === relevant.length
  const satisfied = relevant.every(row => requirementSatisfied(input, row, draft.judgments[row.id]))
  return {recorded: false, state: complete ? '待决定' : draft.followup?.requests.length ? '补正与复核中' : '待复核', next: complete ? '继续处理' : '继续复核', detail: complete ? satisfied ? '本批依据已确认，决定尚未记录。' : '人工复核已记录，仍有未满足要求；可继续补件或记录不批准。' : `已记录 ${reviewed.length} / ${relevant.length} 项判断，尚未作出决定。`}
}
export function decisionCurrentInput(id: string): DecisionInput | null {
  const draft = readDraft(`decision:${id}`, validDraft)?.value
  return draft?.record?.input ?? draft?.workingInput ?? null
}
// Continuations read the decision's current evidence; never silently fall back
// to its initial files when a saved record cannot be read or belongs elsewhere.
export function decisionContinuation(id: string, initial: DecisionInput, caseId = id) {
  const loaded = loadDraft(`decision:${id}`, validDraft), draft = loaded.saved?.value
  const error = loaded.error || (draft && draft.inputKey !== JSON.stringify(initial) ? '复核记录与本轮原始依据不一致，请先返回核对。' : '')
  const input = error ? initial : draft?.record?.input ?? draft?.workingInput ?? initial
  const check = readReceiptCheck(caseId, id, input, draft?.appliedCheck)
  return {input, documents: draft?.followup?.documents ?? [], pendingReply: Boolean(draft?.followup?.draft),
    pendingCheck: !draft?.record && check.pending, error: error || (!draft?.record ? check.error : '')}
}
export function decisionReviewStatus(id: string) {
  const draft = readDraft(`decision:${id}`, validDraft)?.value
  if (!draft) return null
  const judgments = draft.record?.judgments ?? draft.judgments
  let input: DecisionInput
  try { input = draft.record?.input ?? draft.workingInput ?? JSON.parse(draft.inputKey) } catch {return null}
  if (!Array.isArray(input.evidence)) return null
  const reviewed = input.evidence.filter(row => judgmentComplete(input, row, judgments[row.id])).map(row => row.id)
  const resolved = input.evidence.filter(row => requirementSatisfied(input, row, judgments[row.id])).map(row => row.id)
  return {reviewed, resolved}
}
const escape = (value: string) => value.replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]!))
function humanReport(record: RecordedDecision, row: DecisionEvidence) {
  const judgment = record.judgments[row.id]
  const cites = judgment?.value.startsWith('manual-') ? judgment.citations ?? [] : []
  return `<p>人工复核：${escape(judgmentLabels[judgment?.value ?? 'pending'])}</p><pre>${escape(judgment?.reason ?? '')}</pre>${cites.map(cite => {
    const file = record.input.files.find(item => sourceKey(item) === cite.source)
    return `<h3>人工引用：${escape(file ? file.name + ' · Rev ' + file.revision + ' · 文件 v' + file.sourceVersion : '版本不在本次范围')}</h3><p>${escape(cite.location)}</p><pre>${escape(cite.excerpt)}</pre>`
  }).join('')}`
}
function reportHTML(record: RecordedDecision) {
  const e = escape
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${e(record.id)} 审核报告</title><style>body{max-width:900px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;color:#192535}h1{font-size:28px}section{padding:20px 0;border-top:1px solid #ccd3db}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small{color:#586575}table{border-collapse:collapse;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid #ccd3db}@media print{body{margin:0}}</style><h1>${e(record.outcome)}</h1><p>交互原型 · 检查执行与身份为模拟，不构成正式批准。上传原件与合成示例分别标识。</p><p>${e(record.input.scope)}</p><p>决定编号 ${e(record.id)} · 收件 ${e(record.input.receiptId)}</p><p>决定人：${e(record.by)} · ${e(record.at)}</p><pre>${e(record.reason)}</pre><section><h2>决定范围</h2><p>仅限下列已确认要求与文件版本，不代表 PPAP 全项或产线能力批准。未知与失败不视为满足。</p><p>${e(record.input.basis?.source.title ?? '未确认')} ${e(record.input.basis?.source.version ?? '')}</p>${record.input.basis?.conflict.active ? `<p>要求冲突取舍：${e(record.input.basis.conflict.choice)}；${e(record.input.basis.conflict.reason)}</p>` : ''}${record.input.basis?.clauses.map(clause => `<h3>${e(clause.id)} · ${e(clause.title)}</h3><pre>${e(clause.text)}</pre><p>${e(clause.source)} · ${e(clause.location)} · ${clause.applicability === 'exclude' ? `不适用：${e(clause.reason)}` : '适用'}</p>`).join('') ?? ''}</section><section><h2>文件版本</h2><ul>${record.input.files.map(file => `<li>${e(file.name)} · Rev ${e(file.revision)} · 原件 v${file.sourceVersion}</li>`).join('')}</ul></section>${record.input.receipts?.length ? `<section><h2>收件与答复历史</h2>${record.input.receipts.map(receipt => `<h3>${e(receipt.id)} · ${e(receipt.source)}</h3><ul>${receipt.files.map(file => `<li>${e(file.name)} · Rev ${e(file.revision)} · 原件 v${file.sourceVersion}</li>`).join('')}</ul><pre>${e(receipt.notes || '无补充说明')}</pre>`).join('')}</section>` : ''}${record.input.evidence.map(row => `<section><h2>${e(row.id)} · ${e(row.title)}</h2><p>机器检查：${e(row.result)}</p><pre>${e(row.explanation)}</pre>${row.sources.map(source => `<h3>${e(source.file)} · ${e(source.location)}</h3><pre>${e(source.excerpt)}</pre>`).join('')}${humanReport(record, row)}</section>`).join('')}<section><h2>收到的澄清</h2><pre>${e(record.input.clarification ?? '无')}</pre></section><footer>文件内容、要求、机器结果与人工判断固定于本次决定。报告生成或下载不代表已送达他人。</footer></html>`
}
function download(content: string, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], {type}))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function DecisionWorkspace({id, caseId = id, input: initialInput, onBack, backLabel, latestReceiptId, pendingChange = false, documents: initialDocuments = [], allowFollowup = false}: {documents?: DemoDocument[]; id: string; caseId?: string; input: DecisionInput; onBack: () => void; backLabel?: string; latestReceiptId?: string; pendingChange?: boolean; allowFollowup?: boolean}) {
  const inputKey = JSON.stringify(initialInput)
  const {value, field, saveError, update} = useSavedDraft<DecisionDraft>(`decision:${id}`, () => ({inputKey, judgments: {}, signer: '', reason: '', choice: '', acknowledged: false, record: null, report: 'idle', downloadRequested: null, delivery: null}), validDraft)
  const input = value.workingInput ?? initialInput
  const followup = value.followup ?? emptyFollowup()
  const documents = mergeDocuments(initialDocuments, followup.documents ?? [])
  const receiptCheck = useReceiptRecheck(caseId, id, input, documents)
  const checkReview = receiptReviewState(receiptCheck.round, value.appliedCheck)
  const checking = allowFollowup && checkReview.pending
  const [showFollowup, setShowFollowup] = useState(Boolean(value.followup?.draft || value.followup?.composer))
  const [judgments, setJudgments] = field('judgments')
  const reviewSession = useContext(RoundReviewContext)
  useEffect(() => {
    if (!reviewSession || value.record) return
    const observed = serverJudgments(reviewSession.round, input)
    setJudgments(current => {
      const merged = {...current}
      for (const [key, judgment] of Object.entries(observed)) {
        const local = current[key]
        if (!local || !local.pendingSave && (local.confirmed || local.value === 'pending' && !local.reason.trim() && !local.citations?.length)) merged[key] = judgment
      }
      return JSON.stringify(merged) === JSON.stringify(current) ? current : merged
    })
  }, [reviewSession?.round, input.roundId])
  const [signer, setSigner] = field('signer'); const [reason, setReason] = field('reason')
  const [choice, setChoice] = field('choice'); const [acknowledged, setAcknowledged] = field('acknowledged')
  const [record, setRecord] = field('record'); const [report, setReport] = field('report')
  const [downloadRequested, setDownloadRequested] = field('downloadRequested')
  const [fulfillment, setFulfillment] = field('fulfillment')
  useEffect(() => {setFulfillment(current => current ? recoveredDelivery(current) : current)}, [])
  const taskFromRoute = () => {
    const task = new URLSearchParams(location.search).get('reviewTask')
    return task === 'decision' ? 1 : task === 'delivery' ? 2 : task === 'review' ? 0 : value.record ? 2 : 0
  }
  const [step, setStep] = useState(taskFromRoute), taskHeading = useRef<HTMLHeadingElement>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  useEffect(() => {
    const sync = () => setStep(taskFromRoute())
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [Boolean(value.record)])
  function openTask(next: number) {
    setStep(next)
    const url = new URL(location.href)
    url.searchParams.set('reviewTask', next === 1 ? 'decision' : next === 2 ? 'delivery' : 'review')
    history.pushState(null, '', url)
    requestAnimationFrame(() => {taskHeading.current?.scrollIntoView({block: 'nearest'}); taskHeading.current?.focus({preventScroll: true})})
  }
  const [failReport, setFailReport] = useState(false)
  const snapshot = record?.input ?? input
  const changed = value.inputKey !== inputKey && (() => {
    try {return decisionInputKey(JSON.parse(value.inputKey)) !== decisionInputKey(initialInput)}
    catch {return true}
  })()
  const historical = Boolean(pendingChange || latestReceiptId && latestReceiptId !== initialInput.receiptId)
  const relevant = input.evidence.filter(row => row.result !== '不适用')
  const missingReview = relevant.filter(row => !judgmentComplete(input, row, judgments[row.id]))
  const satisfiedCount = relevant.filter(row => requirementSatisfied(input, row, judgments[row.id])).length
  const blockers = [
    ...(!input.basis?.confirmed || !relevant.length ? ['没有完整的适用要求范围。'] : []),
    ...relevant.filter(row => !requirementSatisfied(input, row, judgments[row.id])).map(row => `${row.displayId || row.id} ${row.title}：${judgmentComplete(input, row, judgments[row.id]) ? '人工未确认满足要求' : '尚未记录完整的人工判断与依据'}`),
    ...(changed ? ['本次资料与已保存复核的版本不同。旧决定仅适用于旧快照。'] : []),
    ...(historical ? ['已有待评估变化或后续资料；请先处理当前范围，不能在旧轮次新增决定。'] : []),
    ...(followup.draft ? ['本批回件尚未登记；请先登记并复审，或放弃本批登记。'] : []),
    ...(checking ? [checkReview.awaitingReview ? '有新的检查结果尚未纳入复核，请先使用本次结果。' : '本批检查尚未结束或受理待核对。'] : []),
  ]
  const canRecord = !record && !changed && !historical && !followup.draft && !checking && !saveError && !receiptCheck.saveError && !missingReview.length && Boolean(signer.trim() && reason.trim() && acknowledged && choice) && (choice !== '批准本次范围' || !blockers.length)
  useEffect(() => {
    if (report !== 'generating' || !record) return
    const timer = window.setTimeout(() => {
      try { if (failReport) throw new Error('simulated'); reportHTML(record); setReport('ready') } catch {setReport('failed')}
    }, 500)
    return () => window.clearTimeout(timer)
  }, [report, record, failReport])
  function recordNow() {
    if (!canRecord || !choice) return
    setRecord({id: `${input.receiptId}-D01`, outcome: choice, by: signer.trim(), reason: reason.trim(), at: new Date().toISOString(), input: structuredClone(input), judgments: structuredClone(judgments), ...(value.followup ? {followup: structuredClone(value.followup)} : {})})
    setConfirmOpen(false); openTask(2)
  }
  const replies = Object.fromEntries(snapshot.evidence.map(row => {
    const all = followup.history.flatMap((item, index) => followup.requests.filter(request => request.clauseId === row.id && item.answers[request.id]?.trim()).map(request => ({request, text: item.answers[request.id], batch: index + 1})))
    return [row.id, all.filter(reply => reply.batch === all.at(-1)?.batch).map(reply => ({title: `第 ${reply.batch} 批答复 · ${requestLabel(reply.request, followup)}${currentRequest(reply.request, followup) ? '' : '（旧版或已撤销）'}`, question: reply.request.question, text: reply.text}))]
  }))
  const evidence = <ReviewWorkspace key={decisionInputKey(snapshot)} input={snapshot} documents={documents} taskActive={step === 0} judgments={record?.judgments ?? judgments} replies={replies} frozen={Boolean(record || changed || historical || followup.draft || checking)} onChange={(row, judgment) => {setJudgments(current => ({...current, [row.id]: judgment})); setAcknowledged(false)}} onRequest={allowFollowup ? row => {if (!followup.requests.some(request => request.clauseId === row.id)) update(current => ({...current, followup: {...(current.followup ?? emptyFollowup()), composer: {clauseId: row.id, text: ''}}})); setShowFollowup(true)} : undefined}/>

  const decision = <section className={"grid gap-3.5"} aria-label="决定"><h2>{record ? '决定已记录' : '作出决定'}</h2>{record ? <><strong className={"text-2xl text-foreground"}>{record.outcome}</strong><p>{record.reason}</p><dl><div><dt>决定人</dt><dd>{record.by}</dd></div><div><dt>记录时间</dt><dd>{new Date(record.at).toLocaleString('zh-CN')}</dd></div><div><dt>决定编号</dt><dd>{record.id}</dd></div></dl><p>此决定固定当时的要求、文件与人工判断。新增资料不能改写本记录。</p></> : <>
    <p>决定仅适用于本次已确认条款和文件版本，不代表 PPAP 全项或实际产线能力批准。</p>
    <div className={"py-3.5 px-4 bg-background border-l [&_ul]:pl-4.5 [&_ul]:mt-2 [&_ul]:mr-0 [&_ul]:mb-0 [&_ul]:ml-0 [&_ul]:text-muted-foreground"} role="status"><strong>{blockers.length ? '批准条件未满足' : '依据已确认，可以作出决定'}</strong>{blockers.length > 0 && <ul>{blockers.map((item, index) => <li key={index}>{item}</li>)}</ul>}</div>
    <fieldset className="space-y-3" disabled={changed || historical}><legend>本轮决定</legend><RadioGroup value={choice} onValueChange={value => {setChoice(value as RecordedDecision['outcome']); setAcknowledged(false)}}>{(['批准本次范围', '不批准'] as const).map(option => <label key={option}><RadioGroupItem value={option} disabled={option === '批准本次范围' && blockers.length > 0}/>{option}</label>)}</RadioGroup></fieldset>
    <label className="grid gap-1.5">决定人<Input value={signer} disabled={changed || historical} onChange={event => {setSigner(event.target.value); setAcknowledged(false)}} placeholder="填写审核员姓名"/></label>
    <label className="grid gap-1.5">决定理由<Textarea value={reason} disabled={changed || historical} onChange={event => {setReason(event.target.value); setAcknowledged(false)}} placeholder="说明本次范围、依据与决定"/></label>
    <label className={"[&_[role=checkbox]]:mt-1 [&_[role=checkbox]]:shrink-0"}><Checkbox checked={acknowledged} disabled={changed || historical} onCheckedChange={state => setAcknowledged(state === true)}/><span>我已核对本次要求、证据和复核记录，并以有权决定的审核员身份记录此模拟决定。</span></label>
    <Button className={""} disabled={!canRecord} onClick={() => setConfirmOpen(true)}>核对并记录决定<ArrowRight/></Button>{missingReview.length > 0 && <p>先记录剩余 {missingReview.length} 项的人工判断及理由；暂不决定时可返回工作台，草稿会保留。</p>}
  </>}</section>
  const output = <section className={""} aria-label="报告与交付"><h2>报告与交付</h2>{!record ? <p>记录决定后，生成对应快照的报告。</p> : <>
    <p role="status">{fulfillment ? deliveryLabel(fulfillment, Boolean(value.delivery)) : value.delivery ? '已登记系统外交付' : report === 'ready' ? '报告已就绪 · 待交付' : '准备固定版本的报告与资料包，再选择交付方式。'}</p>
    <DeliveryWorkspace record={record} documents={documents} saved={fulfillment} setSaved={setFulfillment} report={report} reportHTML={reportHTML(record)} onGenerateReport={() => setReport('generating')} onReportPrepared={() => setReport('ready')} onDownloadReport={() => {download(reportHTML(record), `${record.id}.html`, 'text/html;charset=utf-8'); setDownloadRequested(new Date().toISOString())}} onDownloadRecord={() => download(JSON.stringify(record, null, 2), `${record.id}.json`, 'application/json')} failReport={failReport} setFailReport={setFailReport} downloadRequested={downloadRequested} legacyDelivery={value.delivery} legacyDraft={value.deliveryDraft} historical={historical} saveError={saveError}/>
  </>}</section>
  const followupDisabled = Boolean(record || changed || historical || saveError || checking || receiptCheck.saveError)
  if (allowFollowup && receiptCheck.preparing && !historical && !record) return <ReceiptCheckPreparation controller={receiptCheck}/>
  if (allowFollowup && showFollowup) return <div className={"min-w-0 pb-12 text-sm leading-relaxed [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-2 [&_h2]:ml-0 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:m-0 [&_p]:my-2 [&_p]:mx-0 [&_p]:text-muted-foreground [&_p]:break-all [&_article]:border-t [&_article]:py-5 [&_article]:px-0 [&_article_header]:flex [&_article_header]:items-center [&_article_header]:justify-between [&_article_header]:gap-3 [&_label]:grid [&_label]:gap-1.5 [&_label]:text-sm [&_fieldset]:border-0 [&_fieldset]:flex [&_fieldset]:flex-wrap [&_fieldset]:gap-4 [&_fieldset]:p-0 [&_legend]:mb-2 [&_legend]:font-semibold [&_fieldset_label]:flex [&_fieldset_label]:items-center [&_fieldset_label]:gap-2 [&_fieldset_label]:min-h-10 [&_dl]:grid [&_dl]:gap-2.5 [&_dl]:m-0 [&_dl_div]:grid [&_dl_div]:grid-cols-[80px_minmax(0,1fr)] [&_dl_div]:gap-2.5 [&_dt]:text-muted-foreground [&_dd]:m-0 [&_dd]:break-all max-sm:[&_label:has([role=checkbox])]:min-h-11 max-sm:[&_article_header]:items-start"}>{saveError && <p role="alert">{saveError}</p>}{historical && <p role="status" className={""}>历史轮次只读。新的回件请在当前轮次登记。</p>}{record && !historical && <p role="status">本轮决定已记录，补正历史只供查看。收到新资料时，请登记新的变化。</p>}<FollowupWorkspace input={input} state={followup} judgments={judgments} disabled={followupDisabled} documents={documents} onClose={() => setShowFollowup(false)} onChange={next => update(current => ({...current, followup: next, acknowledged: false}))} onReceive={() => {
    if (followupDisabled) return
    update(current => {
      if (current.record) return current
      const received = receiveReply(current.workingInput ?? initialInput, current.followup ?? emptyFollowup(), current.judgments)
      return received ? {...current, workingInput: received.input, followup: received.state, judgments: {}, choice: '', reason: '', acknowledged: false} : current
    }); setShowFollowup(false); openTask(0)
  }}/></div>
  // Stitch: projects/13261964020862199279/screens/34f7796e5001437da045ab5abdd66a89.
  return <div className="min-w-0 pb-6 text-sm leading-relaxed">
    <header className="mb-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Button variant="ghost" size="icon-sm" onClick={onBack} aria-label={backLabel ?? (initialInput.receipts ? '返回补件复审' : '返回事项')} title={backLabel ?? (initialInput.receipts ? '返回补件复审' : '返回事项')}><ArrowLeft/></Button>
          <h1 ref={taskHeading} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none">{step === 1 ? `审核决定${input.roundId ? '（原型）' : ''}` : step === 2 ? `报告与交付${input.roundId ? '（原型）' : ''}` : '人工复核'}</h1>
        </div>
        <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" size="sm">{step === 0 ? '后续流程' : '切换任务'}{input.roundId ? '（原型）' : ''}<ChevronDown/></Button></DropdownMenuTrigger><DropdownMenuContent align="end">{step !== 1 && <DropdownMenuItem onSelect={() => openTask(1)}>审核决定</DropdownMenuItem>}{step !== 2 && <DropdownMenuItem onSelect={() => openTask(2)}>报告与交付</DropdownMenuItem>}</DropdownMenuContent></DropdownMenu>
      </div>
      <p className="break-words text-muted-foreground">{snapshot.scope}</p>
    </header>
    {step !== 0 && <div className="mb-3"><Button variant="ghost" size="sm" onClick={() => openTask(0)}><ArrowLeft/>返回人工复核</Button>{input.roundId && <p className="mt-2 text-sm text-muted-foreground">此后续流程仅保存本机原型记录，未接入正式批准或交付。</p>}</div>}
    {latestReceiptId && latestReceiptId !== initialInput.receiptId && <p role="status" className={""}>正在查看历史批次 {snapshot.receiptId}。本页决定不适用于后续轮次。</p>}
    {saveError && <p role="alert">{saveError}</p>}{changed && <p role="alert">资料快照已变化。这里只保留原复核和决定，不能将旧结论用于新版本。</p>}
    <div className="mb-3 flex flex-wrap items-center gap-x-4 border-b pb-2"><BasisSummary basis={snapshot.basis} compact/><Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">资料与澄清 · {snapshot.files.length} 份<ChevronDown/></Button></CollapsibleTrigger><CollapsibleContent><div className="space-y-3 py-3">{snapshot.files.map(file => <p key={sourceKey(file)}>{file.name}{file.material ? ' · 本轮固定版本' : ` · Rev ${file.revision} · 原件 v${file.sourceVersion}`}</p>)}<SourceReaderButton input={snapshot} documents={documents} label="阅读本次资料"/>{snapshot.clarification && <p>收到的说明：{snapshot.clarification}</p>}</div></CollapsibleContent></Collapsible>
      {snapshot.receipts && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">收件历史 · {snapshot.receipts.length} 批</Button></CollapsibleTrigger><CollapsibleContent>{snapshot.receipts.map(receipt => <section key={receipt.id}><h3>{receipt.source}</h3>{receipt.files.map((file, i) => <p key={i}>{file.name} · Rev {file.revision} · 原件 v{file.sourceVersion}</p>)}<p className="whitespace-pre-wrap">{receipt.notes}</p></section>)}</CollapsibleContent></Collapsible>}
    </div>
    {allowFollowup && followup.requests.length > 0 && <div className={"flex flex-wrap gap-2 my-3 mx-0"}><Button variant="outline" onClick={() => setShowFollowup(true)}>{record || historical ? '查看补正历史' : followup.draft ? '继续登记本批回件' : '补正与回件'}<ArrowRight/></Button><span role="status">{followup.history.length} 批回件{followup.draft ? ' · 本批待登记，暂不能决定' : ''}</span></div>}
    {allowFollowup && followup.history.length > 0 && !record && !historical && <ReceiptCheckActions controller={receiptCheck} applied={value.appliedCheck} disabled={Boolean(changed || followup.draft || saveError)} onApply={(result, token) => update(current => {
      const active = current.workingInput ?? initialInput
      if (current.record || current.followup?.draft || receiptInputKey(active) !== receiptInputKey(result) || current.appliedCheck === token) return current
      return {...current, workingInput: structuredClone(result), appliedCheck: token, judgments: judgmentsAfterCheck(active, result, current.judgments), choice: '', reason: '', acknowledged: false}
    })}/>}
    <div hidden={step !== 0} className="mt-4 min-w-0">{evidence}</div>
    {step === 1 && decision}{step === 2 && output}
    <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}><DialogContent><DialogHeader><DialogTitle>确认{choice}</DialogTitle><DialogDescription>记录后将固定本次范围、要求、证据和人工判断；不会自动发送报告。</DialogDescription></DialogHeader><p>{input.scope}</p><p>{input.basis?.source.title} · {input.basis?.source.version}</p><p>决定人：{signer}</p><p>{reason}</p><div className={"flex flex-wrap gap-2 my-3 mx-0"}><Button variant="outline" onClick={() => setConfirmOpen(false)}>返回修改</Button><Button disabled={!canRecord} onClick={recordNow}>确认记录</Button></div></DialogContent></Dialog>
  </div>
}
