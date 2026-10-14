// Stitch: projects/13261964020862199279/screens/2886cf1575be44bbb169efc64abc14fe.
// Three structures on the same case: evidence-first, decision desk, and guided steps.
// Uploaded originals and synthetic examples stay distinct. Not a production approval system.
import {useEffect, useState} from 'react'
import {ArrowLeft, ArrowRight, FileCheck2} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {Checkbox} from '../src/components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../src/components/ui/dialog'
import {BasisSummary} from './requirements'
import {isKnownDemoClause, type RequirementBasis} from './requirements-state'
import {isObject, loadDraft, readDraft, useSavedDraft} from './saved-draft'
import {SourceReaderButton} from './source-reader'
import {mergeDocuments, sourceKey, type DemoDocument} from './source-documents'
import {ReceiptCheckActions, ReceiptCheckPreparation, readReceiptCheck, receiptReviewState, receiptInputKey, useReceiptRecheck} from './receipt-recheck'
import {JudgmentEditor, judgmentComplete, judgmentLabels, judgmentsAfterCheck, requirementSatisfied, validJudgment, type Judgment} from './judgment'
import {DeliveryWorkspace} from './delivery'
import {deliveryLabel, recoveredDelivery, validDelivery, type DeliveryState} from './delivery-state'
import {FollowupWorkspace, currentRequest, requestLabel, emptyFollowup, receiveReply, validDecisionInput, validFollowup, type FollowupState} from './followup'
import './decision.css'

export type DecisionEvidence = {
  id: string; title: string; requirement: string
  result: '满足' | '不满足' | '无法判断' | '未覆盖' | '执行失败' | '不适用'
  explanation: string; sources: {file: string; location: string; excerpt: string}[]
}
export type DecisionInput = {
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
const layouts = ['依据优先', '决定工作台', '分步确认']
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
  const [signer, setSigner] = field('signer'); const [reason, setReason] = field('reason')
  const [choice, setChoice] = field('choice'); const [acknowledged, setAcknowledged] = field('acknowledged')
  const [record, setRecord] = field('record'); const [report, setReport] = field('report')
  const [downloadRequested, setDownloadRequested] = field('downloadRequested')
  const [fulfillment, setFulfillment] = field('fulfillment')
  useEffect(() => {setFulfillment(current => current ? recoveredDelivery(current) : current)}, [])
  const [layout, setLayout] = useState(() => Math.max(0, ['evidence', 'desk', 'steps'].indexOf(new URLSearchParams(location.search).get('decisionLayout') ?? 'evidence')))
  const [step, setStep] = useState(0); const [confirmOpen, setConfirmOpen] = useState(false)
  const [failReport, setFailReport] = useState(false)
  const snapshot = record?.input ?? input
  const changed = value.inputKey !== inputKey
  const historical = Boolean(pendingChange || latestReceiptId && latestReceiptId !== initialInput.receiptId)
  const relevant = input.evidence.filter(row => row.result !== '不适用')
  const missingReview = relevant.filter(row => !judgmentComplete(input, row, judgments[row.id]))
  const satisfiedCount = relevant.filter(row => requirementSatisfied(input, row, judgments[row.id])).length
  const blockers = [
    ...(!input.basis?.confirmed || !relevant.length ? ['没有完整的适用要求范围。'] : []),
    ...relevant.filter(row => !requirementSatisfied(input, row, judgments[row.id])).map(row => `${row.id} ${row.title}：${judgmentComplete(input, row, judgments[row.id]) ? '人工未确认满足要求' : '尚未记录完整的人工判断与依据'}`),
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
  function selectLayout(next: number) {
    setLayout(next); const url = new URL(location.href); url.searchParams.set('decisionLayout', ['evidence', 'desk', 'steps'][next]); history.replaceState(null, '', url)
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (showFollowup) return
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement)?.closest('input,textarea,select,button,[role="dialog"],[contenteditable]')) return
      selectLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key)
  }, [layout, showFollowup])
  function recordNow() {
    if (!canRecord || !choice) return
    setRecord({id: `${input.receiptId}-D01`, outcome: choice, by: signer.trim(), reason: reason.trim(), at: new Date().toISOString(), input: structuredClone(input), judgments: structuredClone(judgments), ...(value.followup ? {followup: structuredClone(value.followup)} : {})})
    setConfirmOpen(false); setStep(2)
  }
  const evidence = <section className="gf-dec-evidence" aria-label="逐条依据与复核"><h2>依据与复核</h2><p>{snapshot.evidence.length} 条要求 · {record ? '已固定人工判断' : checking ? '此前复核暂存，待处理本次检查结果' : `${relevant.length - missingReview.length} / ${relevant.length} 项判断已记录 · ${satisfiedCount} 项已确认满足`}</p>{snapshot.evidence.map(row => {
    const judgment = (record?.judgments ?? judgments)[row.id] ?? {value: 'pending', reason: ''}
    const frozen = Boolean(record || changed || historical || followup.draft || checking)
    const replies = followup.history.flatMap((item, index) => followup.requests.filter(request => request.clauseId === row.id && item.answers[request.id]?.trim()).map(request => ({request, text: item.answers[request.id], batch: index + 1})))
    const latest = replies.filter(reply => reply.batch === replies.at(-1)?.batch)
    return <article key={row.id}><header><h3>{row.id} · {row.title}</h3><span className={`gf-status gf-status--${row.result === '满足' ? 'good' : row.result === '不满足' ? 'bad' : 'wait'}`}>机器：{row.result}</span></header><p>{row.requirement}</p>{latest.map(reply => <div key={reply.request.id} className="gf-dec-reply"><strong>第 {reply.batch} 批答复 · {requestLabel(reply.request, followup)}{currentRequest(reply.request, followup) ? '' : '（旧版或已撤销）'}</strong><p>针对：{reply.request.question}</p><p>{reply.text}</p></div>)}<details><summary>机器检查依据</summary><p>{row.explanation}</p>{row.sources.map((source, index) => <div className="gf-dec-source" key={index}><strong>{source.file}</strong><small>{source.location}</small><pre>{source.excerpt}</pre></div>)}</details>{row.result !== '不适用' && <JudgmentEditor input={snapshot} row={row} judgment={judgment} frozen={frozen} documents={documents} onChange={next => {setJudgments(current => ({...current, [row.id]: next})); setAcknowledged(false)}}/>}
      {allowFollowup && !frozen && row.result !== '不适用' && !requirementSatisfied(input, row, judgment) && <Button variant="outline" onClick={() => {if (!followup.requests.some(request => request.clauseId === row.id)) update(current => ({...current, followup: {...(current.followup ?? emptyFollowup()), composer: {clauseId: row.id, text: ''}}})); setShowFollowup(true)}}>{followup.requests.some(request => request.clauseId === row.id) ? '查看补正与答复' : `请求补正 ${row.id}`}</Button>}
    </article>
  })}</section>
  const decision = <section className="gf-dec-form" aria-label="决定"><h2>{record ? '决定已记录' : '作出决定'}</h2>{record ? <><strong className="gf-dec-outcome">{record.outcome}</strong><p>{record.reason}</p><dl><div><dt>决定人</dt><dd>{record.by}</dd></div><div><dt>记录时间</dt><dd>{new Date(record.at).toLocaleString('zh-CN')}</dd></div><div><dt>决定编号</dt><dd>{record.id}</dd></div></dl><p>此决定固定当时的要求、文件与人工判断。新增资料不能改写本记录。</p></> : <>
    <p>决定仅适用于本次已确认条款和文件版本，不代表 PPAP 全项或实际产线能力批准。</p>
    <div className="gf-dec-gate" role="status"><strong>{blockers.length ? '批准条件未满足' : '依据已确认，可以作出决定'}</strong>{blockers.length > 0 && <ul>{blockers.map((item, index) => <li key={index}>{item}</li>)}</ul>}</div>
    <fieldset disabled={changed || historical}><legend>本轮决定</legend>{(['批准本次范围', '不批准'] as const).map(option => <label key={option}><input type="radio" name="decision" value={option} checked={choice === option} disabled={option === '批准本次范围' && blockers.length > 0} onChange={() => {setChoice(option); setAcknowledged(false)}}/>{option}</label>)}</fieldset>
    <label>决定人<Input value={signer} disabled={changed || historical} onChange={event => {setSigner(event.target.value); setAcknowledged(false)}} placeholder="填写模拟审核员姓名"/></label>
    <label>决定理由<Textarea value={reason} disabled={changed || historical} onChange={event => {setReason(event.target.value); setAcknowledged(false)}} placeholder="说明本次范围、依据与决定"/></label>
    <label className="gf-dec-ack"><Checkbox checked={acknowledged} disabled={changed || historical} onCheckedChange={state => setAcknowledged(state === true)}/><span>我已核对本次要求、证据和复核记录，并以有权决定的审核员身份记录此模拟决定。</span></label>
    <Button className="gf-primary" disabled={!canRecord} onClick={() => setConfirmOpen(true)}>核对并记录决定<ArrowRight/></Button>{missingReview.length > 0 && <p>先记录剩余 {missingReview.length} 项的人工判断及理由；暂不决定时可返回工作台，草稿会保留。</p>}
  </>}</section>
  const output = <section className="gf-dec-delivery" aria-label="报告与交付"><h2>报告与交付</h2>{!record ? <p>记录决定后，生成对应快照的报告。</p> : <>
    <p role="status">{fulfillment ? deliveryLabel(fulfillment, Boolean(value.delivery)) : value.delivery ? '已登记系统外交付' : report === 'ready' ? '报告已就绪 · 待交付' : '准备固定版本的报告与资料包，再选择交付方式。'}</p>
    <DeliveryWorkspace record={record} documents={documents} saved={fulfillment} setSaved={setFulfillment} report={report} reportHTML={reportHTML(record)} onGenerateReport={() => setReport('generating')} onReportPrepared={() => setReport('ready')} onDownloadReport={() => {download(reportHTML(record), `${record.id}.html`, 'text/html;charset=utf-8'); setDownloadRequested(new Date().toISOString())}} onDownloadRecord={() => download(JSON.stringify(record, null, 2), `${record.id}.json`, 'application/json')} failReport={failReport} setFailReport={setFailReport} downloadRequested={downloadRequested} legacyDelivery={value.delivery} legacyDraft={value.deliveryDraft} historical={historical} saveError={saveError}/>
  </>}</section>
  const followupDisabled = Boolean(record || changed || historical || saveError || checking || receiptCheck.saveError)
  if (allowFollowup && receiptCheck.preparing && !historical && !record) return <ReceiptCheckPreparation controller={receiptCheck}/>
  if (allowFollowup && showFollowup) return <div className="gf-dec-page">{saveError && <p role="alert">{saveError}</p>}{historical && <p role="status" className="gf-dec-historical">历史轮次只读。新的回件请在当前轮次登记。</p>}{record && !historical && <p role="status">本轮决定已记录，补正历史只供查看。收到新资料时，请登记新的变化。</p>}<FollowupWorkspace input={input} state={followup} judgments={judgments} disabled={followupDisabled} documents={documents} onClose={() => setShowFollowup(false)} onChange={next => update(current => ({...current, followup: next, acknowledged: false}))} onReceive={() => {
    if (followupDisabled) return
    update(current => {
      if (current.record) return current
      const received = receiveReply(current.workingInput ?? initialInput, current.followup ?? emptyFollowup(), current.judgments)
      return received ? {...current, workingInput: received.input, followup: received.state, judgments: {}, choice: '', reason: '', acknowledged: false} : current
    }); setShowFollowup(false); setStep(0)
  }}/></div>
  return <div className="gf-dec-page"><Button variant="ghost" onClick={onBack}><ArrowLeft/>{backLabel ?? (initialInput.receipts ? '返回补件复审' : '返回事项')}</Button><header className="gf-page-heading"><div><h1>{record ? '决定与交付' : '复核与决定'}</h1><p>{snapshot.scope}</p></div><FileCheck2 aria-hidden="true"/></header>
    {latestReceiptId && latestReceiptId !== initialInput.receiptId && <p role="status" className="gf-dec-historical">正在查看历史批次 {snapshot.receiptId}。本页决定不适用于后续轮次。</p>}
    {saveError && <p role="alert">{saveError}</p>}{changed && <p role="alert">资料快照已变化。这里只保留原复核和决定，不能将旧结论用于新版本。</p>}
    <div className="gf-dec-context"><BasisSummary basis={snapshot.basis}/><details><summary>资料与澄清 · {snapshot.files.length} 份</summary>{snapshot.files.map(file => <p key={sourceKey(file)}>{file.name} · Rev {file.revision} · 原件 v{file.sourceVersion}</p>)}<SourceReaderButton input={snapshot} documents={documents} label="阅读本次资料"/>{snapshot.clarification && <p>收到的说明：{snapshot.clarification}</p>}</details>
      {snapshot.receipts && <details><summary>收件历史 · {snapshot.receipts.length} 批</summary>{snapshot.receipts.map(receipt => <section key={receipt.id}><h3>{receipt.source}</h3>{receipt.files.map((file, i) => <p key={i}>{file.name} · Rev {file.revision} · 原件 v{file.sourceVersion}</p>)}<p style={{whiteSpace: 'pre-wrap'}}>{receipt.notes}</p></section>)}</details>}
    </div>
    {allowFollowup && followup.requests.length > 0 && <div className="gf-dec-downloads"><Button variant="outline" onClick={() => setShowFollowup(true)}>{record || historical ? '查看补正历史' : followup.draft ? '继续登记本批回件' : '补正与回件'}<ArrowRight/></Button><span role="status">{followup.history.length} 批回件{followup.draft ? ' · 本批待登记，暂不能决定' : ''}</span></div>}
    {allowFollowup && followup.history.length > 0 && !record && !historical && <ReceiptCheckActions controller={receiptCheck} applied={value.appliedCheck} disabled={Boolean(changed || followup.draft || saveError)} onApply={(result, token) => update(current => {
      const active = current.workingInput ?? initialInput
      if (current.record || current.followup?.draft || receiptInputKey(active) !== receiptInputKey(result) || current.appliedCheck === token) return current
      return {...current, workingInput: structuredClone(result), appliedCheck: token, judgments: judgmentsAfterCheck(active, result, current.judgments), choice: '', reason: '', acknowledged: false}
    })}/>}
    {layout === 0 && <>{evidence}<div className="gf-dec-bottom">{decision}{output}</div></>}
    {layout === 1 && <div className="gf-dec-desk"><div>{decision}{output}</div>{evidence}</div>}
    {layout === 2 && <><nav className="gf-dec-steps" aria-label="决定步骤">{['核对依据', '记录决定', '交付结果'].map((label, index) => <Button key={label} variant={step === index ? 'secondary' : 'ghost'} aria-current={step === index ? 'step' : undefined} onClick={() => setStep(index)}>{index + 1} · {label}</Button>)}</nav>{[evidence, decision, output][step]}{step < 2 && <Button variant="outline" onClick={() => setStep(step + 1)}>继续：{step === 0 ? '记录决定' : '交付结果'}<ArrowRight/></Button>}</>}
    {import.meta.env.DEV && <div className="gf-switcher" role="toolbar" aria-label="决定页布局"><button aria-label="上一种决定布局" onClick={() => selectLayout((layout + 2) % 3)}><ArrowLeft size={16}/></button><span>{layout + 1} / 3 · {layouts[layout]}</span><button aria-label="下一种决定布局" onClick={() => selectLayout((layout + 1) % 3)}><ArrowRight size={16}/></button></div>}
    <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}><DialogContent><DialogHeader><DialogTitle>确认{choice}</DialogTitle><DialogDescription>记录后将固定本次范围、要求、证据和人工判断；不会自动发送报告。</DialogDescription></DialogHeader><p>{input.scope}</p><p>{input.basis?.source.title} · {input.basis?.source.version}</p><p>决定人：{signer}</p><p>{reason}</p><div className="gf-dec-downloads"><Button variant="outline" onClick={() => setConfirmOpen(false)}>返回修改</Button><Button disabled={!canRecord} onClick={recordNow}>确认记录</Button></div></DialogContent></Dialog>
  </div>
}
