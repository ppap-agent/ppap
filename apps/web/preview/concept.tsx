// Throwaway UI prototype: three overview structures, one synthetic PPAP case, no Product API.
import {useEffect, useRef, useState} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {
  ArrowLeft, ArrowRight, ChevronRight, CircleAlert,
  CircleHelp, ClipboardCheck, FileClock, FileText, Files, GitCompareArrows,
  Layers3, Send, ShieldAlert, ShieldCheck,
} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Checkbox} from '../src/components/ui/checkbox'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import '../src/index.css'
import './concept.css'
import {SupplierAmendment, amendmentVariants, fileMatchesRequest, initialReplies, replyReady, type AmendmentVariant, type ConflictReview, type ReceiptStep, type ReceivedFile, type ReplyMap, type Submission} from './amendment'
import {isObject, readDraft, useSavedDraft} from './saved-draft'
import {BasisSummary} from './requirements'
import type {RequirementBasis} from './requirements-state'
import {DecisionWorkspace, decisionCurrentInput, decisionSummary, type DecisionInput} from './decision'
import {ReassessmentWorkspace, readReassessment, reassessInput, reassessmentDocuments} from './reassessment'
import {PrototypeAccount} from './access'
import {ChangeWorkspace} from './change-review'
import {savedChangeSummary, type ChangeScope} from './change-review-state'
import type {RequirementCollection} from './requirement-library-state'
import {SourceReaderButton} from './source-reader'
import type {DemoDocument} from './source-documents'
import {optionalLocalFile} from './local-files'

type Stage = 'overview' | 'evidence' | 'request' | 'response' | 'delta' | 'decision' | 'history' | 'changes'
type OverviewVariant = 'action' | 'matrix' | 'relationship'
type Variant = OverviewVariant | AmendmentVariant
type RequestKey = 'conflict' | 'scan' | 'report'
type ReviewOutcome = 'pending' | 'maintain' | 'overturn' | 'defer'
type ReviewEntry = {outcome: ReviewOutcome; reason: string}
type ReviewMap = Record<RequestKey, ReviewEntry>
type DecisionRecord = {id: string; outcome: '不批准'; signer: string; reason: string; receiptId: string; revision: string; requirements: string}
export type IntakeHandoff = {
  sample?: 'complete' | 'outlier'
  documents?: DemoDocument[]
  receiptId: string
  files: import('./source-documents').SourceFile[]
  disclosures: {fileName: string; issue: string; note: string}[]
  clarification: string | null
  matchesFixedReview: boolean
  basis?: RequirementBasis | null
  decisionInput?: DecisionInput
  check?: {id: string; method: string}
}

const stages: {id: Stage; label: string; icon: typeof Layers3}[] = [
  {id: 'overview', label: '事项概览', icon: Layers3},
  {id: 'evidence', label: '证据复核', icon: Files},
  {id: 'request', label: '定向补正', icon: Send},
  {id: 'response', label: '登记回件', icon: FileText},
  {id: 'delta', label: '补件复审', icon: GitCompareArrows},
  {id: 'decision', label: '批准决定', icon: ShieldCheck},
  {id: 'history', label: '历史记录', icon: FileClock},
  {id: 'changes', label: '变化与重审', icon: GitCompareArrows},
]
const variants: {id: OverviewVariant; label: string}[] = [
  {id: 'action', label: '行动清单'},
  {id: 'matrix', label: '要求矩阵'},
  {id: 'relationship', label: '证据关系'},
]
const requestDefaults: Record<RequestKey, string> = {
  conflict: '流程图工序 17 写“热处理”，控制计划写“表面处理”。请核对并修正不一致的文件，说明修改原因。本条要求核对文件一致性。',
  scan: '请补交清晰的过程流程图原件，确认当前扫描件中无法辨认的工序号。',
  report: '请补交对应图纸 Rev B 的尺寸报告。现有报告为 Rev A，不能证明本轮要求。',
}
const requestRows: {id: RequestKey; name: string; tag: string; source: string; observation: string}[] = [
  {id: 'conflict', name: '工序 17 描述冲突', tag: '确认不一致', source: '过程流程图_B.pdf · 控制计划_B.xlsx', observation: '同一工序分别写“热处理”和“表面处理”。'},
  {id: 'scan', name: '扫描件工序号不清', tag: '待核实', source: '过程流程图_B.pdf · 第 3 页', observation: '有一处工序号无法可靠辨认；尚无合格或不合格结论。'},
  {id: 'report', name: '缺少 Rev B 尺寸报告', tag: '需补交', source: '尺寸报告_A.xlsx · Rev A', observation: '报告修订 A 不覆盖本轮图纸修订 B；不是尺寸超差结论。'},
]
type ReviewDraft = {
  route: {stage: Stage; variant: Variant}; sent: boolean; selected: Record<RequestKey, boolean>
  review: ReviewMap; messages: Record<RequestKey, string>; due: string; replies: ReplyMap
  receivedFiles: ReceivedFile[]; receiptStep: ReceiptStep; replySource: string; submission: Submission | null
  rechecked: boolean; scanVerified: boolean; conflictReview: ConflictReview
  decisionRecord: DecisionRecord | null; changeRecord: {revision: string; reason: string} | null
  priorSubmissions?: Submission[]
  decisionBatch?: {id: string; input: DecisionInput} | null
}
function freshReview(handoff?: IntakeHandoff): ReviewDraft {
  return {route: handoff ? {stage: 'overview', variant: 'action'} : readRoute(), sent: !handoff && ['response', 'delta'].includes(readRoute().stage), selected: {conflict: true, scan: true, report: true}, review: {conflict: {outcome: 'pending', reason: ''}, scan: {outcome: 'pending', reason: ''}, report: {outcome: 'pending', reason: ''}}, messages: requestDefaults, due: '2026-10-20', replies: initialReplies, receivedFiles: [], receiptStep: 'upload', replySource: '', submission: null, rechecked: false, scanVerified: false, conflictReview: 'pending', decisionRecord: null, changeRecord: null}
}
function isReceivedFile(value: unknown): value is ReceivedFile {
  return isObject(value) && ['id', 'name', 'revision'].every(key => typeof value[key] === 'string')
    && optionalLocalFile(value.localFile)
    && ['待分类', '控制计划', '过程流程图', '尺寸报告'].includes(String(value.kind))
    && ['demo', 'local'].includes(String(value.source)) && typeof value.associated === 'boolean' && typeof value.checked === 'boolean'
}
function isReply(value: unknown): boolean {
  return isObject(value) && [null, 'explain', 'replace', 'dispute'].includes(value.mode as null | string)
    && typeof value.note === 'string' && typeof value.evidence === 'boolean' && (value.fileId === null || typeof value.fileId === 'string')
    && [null, 'flow', 'control'].includes(value.conflictFile as null | string)
}
export function isReviewDraft(value: unknown): value is ReviewDraft {
  if (!isObject(value) || !isObject(value.route) || !stages.some(stage => stage.id === (value.route as Record<string, unknown>).stage)) return false
  if (![...variants, ...amendmentVariants].some(variant => variant.id === (value.route as Record<string, unknown>).variant)) return false
  if (!['sent', 'rechecked', 'scanVerified'].every(key => typeof value[key] === 'boolean') || typeof value.due !== 'string' || typeof value.replySource !== 'string') return false
  if (!['upload', 'verify', 'reply'].includes(String(value.receiptStep)) || !['pending', 'file-consistent', 'needs-clarification'].includes(String(value.conflictReview))) return false
  if (!isObject(value.selected) || !isObject(value.review) || !isObject(value.messages) || !isObject(value.replies)) return false
  if (!requestRows.every(({id}) => {
    const review = (value.review as Record<string, unknown>)[id]
    return typeof (value.selected as Record<string, unknown>)[id] === 'boolean' && typeof (value.messages as Record<string, unknown>)[id] === 'string'
      && isObject(review) && ['pending', 'maintain', 'overturn', 'defer'].includes(String(review.outcome)) && typeof review.reason === 'string'
      && isReply((value.replies as Record<string, unknown>)[id])
  })) return false
  if (!Array.isArray(value.receivedFiles) || !value.receivedFiles.every(isReceivedFile)) return false
  if (value.priorSubmissions !== undefined && (!Array.isArray(value.priorSubmissions) || !value.priorSubmissions.every(item => isObject(item) && typeof item.id === 'string' && typeof item.source === 'string' && Array.isArray(item.files) && item.files.every(isReceivedFile) && isObject(item.replies) && Object.values(item.replies).every(isReply)))) return false
  if (value.decisionBatch !== undefined && value.decisionBatch !== null && (!isObject(value.decisionBatch) || typeof value.decisionBatch.id !== 'string' || !isObject(value.decisionBatch.input) || !Array.isArray(value.decisionBatch.input.evidence))) return false
  if (value.submission !== null && (!isObject(value.submission) || typeof value.submission.id !== 'string' || typeof value.submission.source !== 'string'
    || !Array.isArray(value.submission.files) || !value.submission.files.every(isReceivedFile) || !isObject(value.submission.replies) || !Object.values(value.submission.replies).every(isReply))) return false
  if (value.decisionRecord !== null && (!isObject(value.decisionRecord) || value.decisionRecord.outcome !== '不批准' || !['id', 'signer', 'reason', 'receiptId', 'revision', 'requirements'].every(key => typeof (value.decisionRecord as Record<string, unknown>)[key] === 'string'))) return false
  return value.changeRecord === null || isObject(value.changeRecord) && typeof value.changeRecord.revision === 'string' && typeof value.changeRecord.reason === 'string'
}
export function savedReviewSummary(id: string) {
  const saved = readDraft(`review:${id}`, isReviewDraft)?.value
  const latest = saved?.submission ?? saved?.priorSubmissions?.at(-1)
  const decision = decisionSummary(latest ? `${id}:${latest.id}` : id, id)
  if (saved?.priorSubmissions?.length && !saved.submission) return {state: '补件登记中', next: '继续登记', detail: `已保留 ${saved.priorSubmissions.length} 批历史回件，本次草稿尚未登记。`}
  if (decision) return decision
  if (!saved) return null
  if (saved.decisionRecord) return {state: '不批准已记录', next: '查看决定', detail: saved.decisionRecord.reason}
  if (latest) {const check = readReassessment(id, latest.id); return {state: check?.state === 'done' ? '补件待人工复核' : check?.state === 'failed' ? '补件检查部分失败' : '回件待复审', next: '继续复审', detail: `已收到 ${(saved.priorSubmissions?.length ?? 0) + Number(Boolean(saved.submission))} 批回件；收到、检查与解决分别记录。`}}
  if (saved.sent) return {state: '待补正', next: '继续处理', detail: '对外问题清单已记录，可登记收到的文件或说明。'}
  const recorded = requestRows.filter(({id}) => saved.review[id].outcome !== 'pending' && saved.review[id].reason.trim()).length
  return {state: '待复核', next: '继续复核', detail: `已记录 ${recorded} / 3 项人工判断 · 上次停在${stages.find(item => item.id === saved.route.stage)?.label ?? '事项概览'}`}
}

function readRoute(): {stage: Stage; variant: Variant} {
  const query = new URLSearchParams(window.location.search)
  const stage = query.get('stage')
  const variant = query.get('variant')
  const resolvedStage = stages.some(item => item.id === stage) ? stage as Stage : 'overview'
  const available = resolvedStage === 'response' ? amendmentVariants : variants
  return {stage: resolvedStage, variant: available.some(item => item.id === variant) ? variant as Variant : available[0].id}
}

function Status({tone, children}: {tone: 'bad' | 'wait' | 'missing' | 'good'; children: React.ReactNode}) {
  return <span className={`gf-status gf-status--${tone}`}>{children}</span>
}

function CaseHeading({eyebrow, title, scope, children}: {eyebrow: string; title: string; scope?: string; children?: React.ReactNode}) {
  return <header className="gf-page-heading">
    <div><div className="gf-eyebrow">{eyebrow}</div><h1>{title}</h1><p className="gf-case-meta">{scope ?? '北辰汽车 · 明川精密 · 图纸 Rev B · 苏州'}</p></div>
    {children}
  </header>
}

function HandoffSummary({handoff}: {handoff: IntakeHandoff}) {
  return <section className="gf-handoff" aria-label="初次收件与供应商说明">
    <div className="gf-handoff-heading"><strong>初次收件 {handoff.receiptId}</strong><span>{handoff.files.length} 份原件 · {handoff.disclosures.length} 项已知缺口</span></div>
    {handoff.check && <p className="gf-case-meta">{handoff.check.method} · 检查号 {handoff.check.id}</p>}
    {handoff.clarification && <div className="gf-handoff-clarification"><span>供应商澄清 · 流程图第 3 页</span><p>{handoff.clarification}</p><small>说明已收到；原件和该项判断尚未改变。</small></div>}
    <details><summary>查看收件原件与已知缺口</summary><div className="gf-handoff-details"><div>{handoff.files.map(file => <p key={file.name}><strong>{file.name}</strong><span>原件 v{file.sourceVersion} · Rev {file.revision}</span></p>)}</div><div>{handoff.disclosures.map(item => <p key={`${item.fileName}-${item.issue}`}><strong>{item.fileName} · {item.issue}</strong><span>{item.note}</span></p>)}</div></div></details>
    <BasisSummary basis={handoff.basis}/>
  </section>
}

function Decision({go}: {go: (stage: Stage) => void}) {
  return <section className="gf-decision" aria-label="当前批准状态">
    <div className="gf-decision-icon"><ShieldAlert size={23} strokeWidth={1.8} /></div>
    <div className="gf-decision-copy">
      <span className="gf-kicker">当前判断</span>
      <h2>暂不能批准</h2>
      <p>1 项确认不一致 · 1 项待核实 · 1 份资料未覆盖</p>
    </div>
    <Button className="gf-primary" onClick={() => go('evidence')}>复核差异 <ArrowRight size={16} /></Button>
  </section>
}

function OverviewAction({go}: {go: (stage: Stage) => void}) {
  return <>
    <Decision go={go} />
    <section className="gf-section">
      <div className="gf-section-head"><div><h2>需要处理</h2><p>先核对确定的差异，再向供应商提出准确要求。</p></div><span className="gf-count">3 项</span></div>
      <div className="gf-issue-list">
        <div className="gf-issue-row"><Status tone="bad">确认不一致</Status><div><strong>工序 17 的工艺描述不同</strong><p>流程图：热处理　／　控制计划：表面处理</p></div><button onClick={() => go('evidence')}>查看证据 <ChevronRight size={16} /></button></div>
        <div className="gf-issue-row"><Status tone="wait">待核实</Status><div><strong>扫描件有一处工序号无法辨认</strong><p>尚不能判断该工序是否符合要求。</p></div><button onClick={() => go('evidence')}>确认原件 <ChevronRight size={16} /></button></div>
        <div className="gf-issue-row"><Status tone="missing">需补交</Status><div><strong>尺寸报告版本不对应</strong><p>现有 Rev A 报告，不能覆盖本案图纸 Rev B。</p></div><button onClick={() => go('request')}>准备补件 <ChevronRight size={16} /></button></div>
      </div>
    </section>
    <section className="gf-section gf-docs">
      <div className="gf-section-head"><div><h2>本轮资料</h2><p>三份文件，均可追溯原始版本。</p></div></div>
      <div className="gf-doc-list">
        <div><FileText size={18} /><span>过程流程图_B.pdf</span><small>扫描件 · 有待核实内容</small></div>
        <div><FileText size={18} /><span>控制计划_B.xlsx</span><small>Rev B · 工序 17 存在差异</small></div>
        <div><FileClock size={18} /><span>尺寸报告_A.xlsx</span><small>Rev A · 不覆盖本轮</small></div>
      </div>
    </section>
  </>
}

function MatrixCell({children, tone}: {children: React.ReactNode; tone?: 'bad' | 'wait' | 'missing'}) {
  return <td className={tone ? `gf-cell--${tone}` : ''}>{children}</td>
}

function OverviewMatrix({go}: {go: (stage: Stage) => void}) {
  return <>
    <Decision go={go} />
    <section className="gf-section gf-matrix">
      <div className="gf-section-head"><div><h2>要求与资料</h2><p>按要求检查每份资料的覆盖情况。</p></div></div>
      <div className="gf-table-scroll"><table>
        <thead><tr><th>审核要求</th><th>过程流程图</th><th>控制计划</th><th>尺寸报告</th><th>本轮结论</th></tr></thead>
        <tbody>
          <tr><th>工序描述一致</th><MatrixCell tone="bad">工序 17<br/><strong>热处理</strong></MatrixCell><MatrixCell tone="bad">工序 17<br/><strong>表面处理</strong></MatrixCell><MatrixCell>—</MatrixCell><MatrixCell><Status tone="bad">不一致</Status></MatrixCell></tr>
          <tr><th>工序号可核对</th><MatrixCell tone="wait">扫描处无法辨认</MatrixCell><MatrixCell>工序号可读</MatrixCell><MatrixCell>—</MatrixCell><MatrixCell><Status tone="wait">待核实</Status></MatrixCell></tr>
          <tr><th>尺寸报告对应 Rev B</th><MatrixCell>—</MatrixCell><MatrixCell>—</MatrixCell><MatrixCell tone="missing">仅有 Rev A</MatrixCell><MatrixCell><Status tone="missing">未覆盖</Status></MatrixCell></tr>
        </tbody>
      </table></div>
      <Button variant="outline" className="gf-inline-action" onClick={() => go('evidence')}>查看工序 17 的原文 <ArrowRight size={16} /></Button>
    </section>
  </>
}

function OverviewRelationship({go}: {go: (stage: Stage) => void}) {
  return <>
    <Decision go={go} />
    <section className="gf-section">
      <div className="gf-section-head"><div><h2>资料之间的关系</h2><p>结论来自文件中的具体内容，不来自文件名。</p></div></div>
      <div className="gf-relation-pair">
        <div className="gf-relation-source"><span className="gf-file-label">过程流程图_B.pdf</span><small>第 3 页 · 工序 17</small><strong>热处理</strong></div>
        <div className="gf-relation-link"><span>同一工序</span><CircleAlert size={19}/><span>描述不同</span></div>
        <div className="gf-relation-source"><span className="gf-file-label">控制计划_B.xlsx</span><small>工序 17</small><strong>表面处理</strong></div>
      </div>
      <div className="gf-relation-tail"><div><CircleHelp size={18}/><span>流程图另有一处工序号无法辨认</span><Status tone="wait">待核实</Status></div><div><FileClock size={18}/><span>尺寸报告只有 Rev A，本轮要求 Rev B</span><Status tone="missing">未覆盖</Status></div></div>
      <Button variant="outline" className="gf-inline-action" onClick={() => go('evidence')}>核对两处原文 <ArrowRight size={16} /></Button>
    </section>
  </>
}

function Evidence({go, review, onReview, frozen, input, documents}: {input: DecisionInput; documents: DemoDocument[]; go: (stage: Stage) => void; review: ReviewMap; onReview: (id: RequestKey, entry: ReviewEntry) => void; frozen: boolean}) {
  const [showContext, setShowContext] = useState(false)
  const complete = requestRows.every(row => review[row.id].outcome !== 'pending' && review[row.id].reason.trim())
  return <>
    <button className="gf-back" onClick={() => go('overview')}><ArrowLeft size={16}/> 事项概览</button>
    <CaseHeading eyebrow="证据复核" title="核对原件与发现"><Status tone="bad">机器发现待复核</Status></CaseHeading>
    <div className="gf-requirement"><ClipboardCheck size={19}/><div><span>适用要求</span><strong>流程图与控制计划的工序名称应一致。</strong></div></div>
    <section className="gf-evidence-grid" aria-label="两份原件的摘录">
      <article className="gf-excerpt"><header><FileText size={18}/><div><h2>过程流程图_B.pdf</h2><p>第 3 页 · 工序 17 · Rev B</p></div></header><div className="gf-excerpt-body"><span>原件摘录</span><p>工序 17　<mark>热处理</mark></p>{showContext && <small>前一道：机加工　·　后一道：表面检查</small>}</div></article>
      <article className="gf-excerpt"><header><FileText size={18}/><div><h2>控制计划_B.xlsx</h2><p>工作表「控制计划」· 工序 17 · Rev B</p></div></header><div className="gf-excerpt-body"><span>原件摘录</span><p>工序名称　<mark>表面处理</mark></p>{showContext && <small>对应工序号：17　·　同一零件 P-204</small>}</div></article>
    </section>
    <button className="gf-text-button" onClick={() => setShowContext(!showContext)}>{showContext ? '收起上下文' : '展开相邻内容'}</button>
    <section className="gf-conclusion"><div><Status tone="bad">机器发现</Status><p>两份原件的工序名称不同。人工结论需要逐项记录。</p></div></section>
    <section className="gf-human-review" aria-label="逐项人工复核"><header><h2>逐项复核</h2><p>选择处理方式并说明依据。推翻发现不会改写原始证据。</p></header>{requestRows.map(row => <article key={row.id}><div><strong>{row.name}</strong><small>{row.source}</small><p>{row.observation}</p><SourceReaderButton input={input} documents={documents} clauseId={({conflict: 'R1', scan: 'R2', report: 'R3'})[row.id]}/></div><label>复核动作<select aria-label={`${row.name}的复核动作`} value={review[row.id].outcome} disabled={frozen} onChange={event => onReview(row.id, {...review[row.id], outcome: event.target.value as ReviewOutcome})}><option value="pending">尚未判断</option><option value="maintain">维持发现</option><option value="overturn">推翻发现</option><option value="defer">暂缓判断</option></select></label><label>判断依据<Textarea aria-label={`${row.name}的判断依据`} value={review[row.id].reason} disabled={frozen} onChange={event => onReview(row.id, {...review[row.id], reason: event.target.value})} placeholder="记录核对了什么，以及为什么这样判断"/></label></article>)}<div className="gf-human-review-footer"><span>{requestRows.filter(row => review[row.id].outcome !== 'pending' && review[row.id].reason.trim()).length} / {requestRows.length} 项已复核</span><Button className="gf-primary" disabled={!complete} onClick={() => go('request')}>{frozen ? '查看补正请求' : '准备补正请求'} <ArrowRight size={16}/></Button></div></section>
  </>
}

function Request({go, sent, setSent, selected, setSelected, messages, setMessages, due, setDue, clarification, review}: {
  go: (stage: Stage) => void
  sent: boolean
  setSent: (value: boolean) => void
  selected: Record<RequestKey, boolean>
  setSelected: (value: Record<RequestKey, boolean>) => void
  messages: Record<RequestKey, string>
  setMessages: (value: Record<RequestKey, string>) => void
  due: string
  setDue: (value: string) => void
  clarification?: string | null
  review: ReviewMap
}) {
  const count = requestRows.filter(row => selected[row.id] && review[row.id].outcome !== 'overturn').length
  return <>
    <button className="gf-back" onClick={() => go('evidence')}><ArrowLeft size={16}/> 证据复核</button>
    <CaseHeading eyebrow="对外沟通 · 草稿" title="准备补正请求"><Status tone={sent ? 'good' : 'wait'}>{sent ? '已模拟记录' : '待联系供应商'}</Status></CaseHeading>
    <p className="gf-page-intro">审核员核对请求后，通过现有渠道联系供应商。本原型不发送消息。</p>
    <section className="gf-request-list" aria-label="补正条目">
      {requestRows.map(row => <div className="gf-request-row" key={row.id}>
        <Checkbox aria-label={`包含${row.name}`} checked={selected[row.id] && review[row.id].outcome !== 'overturn'} disabled={sent || review[row.id].outcome === 'overturn'} onCheckedChange={checked => setSelected({...selected, [row.id]: checked === true})}/>
        <div className="gf-request-content"><div className="gf-request-title"><h2>{row.name}</h2><span>{review[row.id].outcome === 'overturn' ? '已推翻，不发送' : row.tag}</span></div><small>依据：{row.source} · 人工判断：{review[row.id].reason}</small>{row.id === 'scan' && clarification && <p className="gf-request-prior-note">供应商此前澄清：{clarification}</p>}<Textarea aria-label={`${row.name}的请求内容`} value={messages[row.id]} disabled={sent || !selected[row.id] || review[row.id].outcome === 'overturn'} onChange={event => setMessages({...messages, [row.id]: event.target.value})}/></div>
      </div>)}
    </section>
    <div className="gf-request-footer"><div className="gf-field"><span>联系对象</span><strong>明川精密 · 供应商质量负责人</strong></div><label className="gf-field"><span>希望回复日期</span><Input type="date" value={due} disabled={sent} onChange={event => setDue(event.target.value)}/></label><div className="gf-request-actions"><span>已选 {count} / 3 项</span>{sent ? <Button className="gf-primary" onClick={() => go('response')}>登记收到的回件 <ArrowRight size={16}/></Button> : <Button className="gf-primary" disabled={!count || !due || requestRows.some(row => selected[row.id] && review[row.id].outcome !== 'overturn' && !messages[row.id].trim())} onClick={() => setSent(true)}>模拟记录对外请求 <Send size={16}/></Button>}</div></div>
  </>
}


export function ConceptApp({handoff, draftId, onHome, changeContext}: {handoff?: IntakeHandoff; draftId?: string; onHome?: () => void; changeContext?: {scope: ChangeScope; collections: RequirementCollection[]}} = {}) {
  const [recordKey] = useState(() => `review:${draftId ?? `standalone-${readRoute().stage === 'response' ? 'amendment' : 'concept'}`}`)
  const {field, saveError} = useSavedDraft(recordKey, () => freshReview(handoff), isReviewDraft)
  const [route, setRoute] = field('route')
  const [sent, setSent] = field('sent')
  const [selected, setSelected] = field('selected')
  const [review, setReview] = field('review')
  const [messages, setMessages] = field('messages')
  const [due, setDue] = field('due')
  const [replies, setReplies] = field('replies')
  const [receivedFiles, setReceivedFiles] = field('receivedFiles')
  const [receiptStep, setReceiptStep] = field('receiptStep')
  const [replySource, setReplySource] = field('replySource')
  const [submission, setSubmission] = field('submission')
  const [priorSubmissions, setPriorSubmissions] = field('priorSubmissions')
  const [decisionBatch, setDecisionBatch] = field('decisionBatch')
  const [decisionRecord] = field('decisionRecord')
  const allSubmissions = [...(priorSubmissions ?? []), ...(submission ? [submission] : [])]
  const caseId = draftId ?? recordKey
  const original = {receiptId: handoff?.receiptId ?? 'DEMO-P204', files: handoff?.files ?? [], basis: handoff?.basis ?? null, clarification: handoff?.clarification ?? null}
  const documents = reassessmentDocuments(original, allSubmissions, handoff?.documents ?? [])
  const originalReading = handoff?.decisionInput ?? reassessInput(original, [])
  const directDecision = Boolean(handoff?.decisionInput)
  const caseTitle = changeContext?.scope.part || '支架 P-204'
  const recordedSummary = draftId ? decisionSummary(draftId) : null
  const currentDecisionInput = (draftId ? decisionCurrentInput(draftId) : null) ?? handoff?.decisionInput
  const changeSummary = savedChangeSummary(caseId)
  const visibleStages = (directDecision ? stages.filter(item => ['overview', 'decision', 'changes'].includes(item.id)) : stages.filter(item => item.id !== 'history')).filter(item => item.id !== 'changes' || changeContext)
  const stageDisabled = (id: Stage) => id === 'changes' ? !changeContext : !directDecision && Boolean(handoff && !handoff.matchesFixedReview && id !== 'overview' || id === 'request' && !reviewComplete || ['response', 'delta', 'decision'].includes(id) && !sent || ['delta', 'decision'].includes(id) && !allSubmissions.length || id === 'decision' && !decisionBatch)
  const changeInput: DecisionInput = allSubmissions.length ? reassessInput(original, allSubmissions) : handoff?.decisionInput ?? {receiptId: original.receiptId, scope: original.basis?.confirmed?.scope ?? '', basis: original.basis, files: original.files, clarification: original.clarification, evidence: []}
  const reviewComplete = requestRows.every(row => review[row.id].outcome !== 'pending' && review[row.id].reason.trim())
  const mobileNav = useRef<HTMLElement>(null)
  useEffect(() => {
    const resume = new URLSearchParams(location.search).get('resume')
    if (changeContext && changeSummary && resume === 'changes' || allSubmissions.length && resume === 'delta' || directDecision && resume === 'decision') {
      setRoute(current => ({...current, stage: resume === 'changes' ? 'changes' : resume === 'decision' ? 'decision' : 'delta'}))
      const url = new URL(location.href); url.searchParams.delete('resume'); history.replaceState(null, '', url)
    }
  }, [])
  useEffect(() => {
    const reveal = () => {
      const nav = mobileNav.current
      const active = nav?.querySelector<HTMLElement>('[aria-current="page"]')
      if (nav && active && nav.clientWidth) nav.scrollTo({left: active.offsetLeft - nav.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2})
    }
    reveal()
    window.addEventListener('resize', reveal)
    return () => window.removeEventListener('resize', reveal)
  }, [route.stage])
  const effectiveSelected = Object.fromEntries(requestRows.map(row => [row.id, selected[row.id] && review[row.id].outcome !== 'overturn'])) as Record<RequestKey, boolean>
  useEffect(() => {
    const sync = () => {if (!handoff) setRoute(readRoute())}
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [handoff])
  const navigate = (next: Partial<{stage: Stage; variant: Variant}>) => {
    const target = next.stage ?? route.stage
    const stage: Stage = target === 'changes' && changeContext ? 'changes' : directDecision ? target === 'overview' ? 'overview' : 'decision' : target === 'request' && !reviewComplete ? 'evidence' : ['response', 'delta', 'decision', 'history'].includes(target) && !sent ? reviewComplete ? 'request' : 'evidence' : ['delta', 'decision', 'history'].includes(target) && !allSubmissions.length ? 'response' : target === 'history' ? 'delta' : target
    const available = stage === 'response' ? amendmentVariants : variants
    const requested = next.variant ?? (stage === route.stage ? route.variant : available[0].id)
    const updated = {stage, variant: available.some(item => item.id === requested) ? requested : available[0].id}
    setRoute(updated)
    if (!handoff) {
      const url = new URL(window.location.href)
      url.searchParams.set('stage', updated.stage)
      url.searchParams.set('variant', updated.variant)
      window.history.pushState(null, '', url)
    }
    window.scrollTo({top: 0, behavior: 'smooth'})
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!['overview', 'response'].includes(route.stage) || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      if ((event.target as HTMLElement)?.closest('input, textarea, select, button, [role="dialog"], [contenteditable="true"]')) return
      const available = route.stage === 'response' ? amendmentVariants : variants
      const index = available.findIndex(item => item.id === route.variant)
      navigate({variant: available[(index + (event.key === 'ArrowRight' ? 1 : available.length - 1)) % available.length].id})
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  const submitReplies = () => {
    if (changeSummary) return
    if (receiptStep !== 'reply' || receivedFiles.some(file => !file.checked)) return
    const accepted = Object.fromEntries(requestRows.filter(row => effectiveSelected[row.id] && replyReady(replies[row.id]) && (replies[row.id].mode !== 'replace' || receivedFiles.some(file => file.id === replies[row.id].fileId && fileMatchesRequest(file, row.id)))).map(row => [row.id, {...replies[row.id]}])) as Submission['replies']
    if (!Object.keys(accepted).length || !replySource.trim() || submission) return
    setSubmission({id: `${handoff?.receiptId ?? 'DEMO-P204'}-R${(priorSubmissions?.length ?? 0) + 2}`, source: replySource.trim(), files: receivedFiles.map(file => ({...file})), replies: accepted})
    setDecisionBatch(null)
    setRoute({stage: 'delta', variant: 'action'})
    window.scrollTo({top: 0})
  }
  const startNextReceipt = () => {
    if (changeSummary) {navigate({stage: 'changes'}); return}
    if (submission) {
      setPriorSubmissions([...(priorSubmissions ?? []), submission])
      setSubmission(null); setReceivedFiles([]); setReplies(structuredClone(initialReplies)); setReplySource(''); setReceiptStep('upload'); setDecisionBatch(null)
    }
    navigate({stage: 'response'})
  }
  const openBatchDecision = (id: string, input: DecisionInput) => {setDecisionBatch({id, input: structuredClone(input)}); navigate({stage: 'decision'})}
  const changeReceivedFiles = (next: ReceivedFile[]) => {
    setReceivedFiles(next)
    setReplies(current => Object.fromEntries((Object.entries(current) as [RequestKey, ReplyMap[RequestKey]][]).map(([key, reply]) => {
      const file = next.find(item => item.id === reply.fileId)
      const matching = file && fileMatchesRequest(file, key)
      return [key, matching ? reply : {...reply, evidence: false, fileId: null, conflictFile: null}]
    })) as ReplyMap)
  }
  const activeVariants = route.stage === 'response' ? amendmentVariants : variants
  return <div className="gf-app">
    <aside className="gf-sidebar">
      <div className="gf-brand"><div className="gf-brand-mark"><ClipboardCheck size={19}/></div><span>PPAP</span><small>概念原型</small></div>
      {onHome && <Button variant="ghost" onClick={onHome}><ArrowLeft/>工作台</Button>}
      <div className="gf-sidebar-label">审核团队{changeContext?.scope.customer ? ` · ${changeContext.scope.customer}` : ''}</div>
      <nav aria-label="原型流程">{visibleStages.map(({id, label, icon: Icon}) => <button key={id} aria-current={route.stage === id ? 'page' : undefined} disabled={stageDisabled(id)} onClick={() => navigate({stage: id})}><Icon size={18}/><span>{directDecision && id === 'decision' ? '复核与决定' : label}</span></button>)}</nav>
      <div className="gf-sidebar-foot"><PrototypeAccount/></div>
    </aside>
    <div className="gf-workspace">
      <header className="gf-topline"><div><span className="gf-topline-dot"/><span>量产批准工作台</span><span className="gf-topline-sep">/</span><strong>{caseTitle}</strong></div><span className="gf-demo-label">交互原型 · 非正式批准</span><PrototypeAccount compact className="gf-account-mobile"/></header>
      <nav ref={mobileNav} className="gf-mobile-nav" aria-label="事项流程">{visibleStages.map(({id, label}) => <button key={id} aria-current={route.stage === id ? 'page' : undefined} disabled={stageDisabled(id)} onClick={() => navigate({stage: id})}>{directDecision && id === 'decision' ? '复核与决定' : label}</button>)}</nav>
      <main className="gf-main">
        {onHome && !(directDecision && route.stage === 'decision') && <button className="gf-back" onClick={onHome}><ArrowLeft size={16}/> 工作台</button>}
        {saveError && <p role="alert">{saveError}</p>}
        {route.stage !== 'changes' && changeContext && changeSummary && <div className="gf-change-banner"><span>{changeSummary.state} · 此页是此前依据</span><Button variant="outline" onClick={() => navigate({stage: 'changes'})}>查看变化与新轮次<ArrowRight/></Button></div>}
        {route.stage === 'changes' && changeContext && <ChangeWorkspace documents={documents} id={caseId} original={changeInput} sourceDecisionId={directDecision ? draftId : undefined} onSourceReview={() => navigate({stage: 'decision'})} scope={changeContext.scope} collections={changeContext.collections} onBack={() => navigate({stage: 'overview'})}/>}
        {route.stage === 'overview' && <>
          <CaseHeading eyebrow="量产批准事项 · 人工复核" title={caseTitle} scope={currentDecisionInput?.scope}><span className="gf-round">{currentDecisionInput?.receiptId ?? handoff?.receiptId ?? '第 2 次收件'}</span></CaseHeading>
          {handoff && <HandoffSummary handoff={handoff}/>}
          {directDecision && currentDecisionInput ? <section className="gf-section">
            <div className="gf-section-head"><div><h2>{recordedSummary?.state ?? '核对原件，记录人工判断'}</h2>{recordedSummary && <p>{recordedSummary.detail}</p>}</div></div>
            <h3>当前机器检查</h3><div className="gf-issue-list">{currentDecisionInput.evidence.map(row => <div className="gf-issue-row" key={row.id}><Status tone={row.result === '满足' ? 'good' : row.result === '不满足' ? 'bad' : 'wait'}>{row.result}</Status><div><strong>{row.id} · {row.title}</strong><p>{row.explanation}</p></div></div>)}</div>
            <Button className="gf-primary gf-inline-action" onClick={() => navigate({stage: 'decision'})}>{recordedSummary?.recorded ? '查看决定与报告' : '复核依据与决定'}<ArrowRight/></Button>
          </section> : allSubmissions.length ? <section className="gf-section"><h2>{savedReviewSummary(caseId)?.state ?? '补件处理中'}</h2><p>已收到 {allSubmissions.length} 批回件。原始发现保留在证据复核中；当前依据见补件复审。</p><Button className="gf-primary" onClick={() => navigate({stage: submission ? 'delta' : 'response'})}>{submission ? '继续补件复审' : '继续登记本批补件'}<ArrowRight/></Button></section> : handoff && !handoff.matchesFixedReview ? <section className="gf-handoff-outside"><h2>本次来件与固定演示样例不同</h2><p>原件及修订已保留在上方。本原型没有对应的检查结果，因此不能套用预设的工序差异、缺件结论或补正请求。</p></section> : route.variant === 'action' ? <OverviewAction go={stage => navigate({stage})}/> : route.variant === 'matrix' ? <OverviewMatrix go={stage => navigate({stage})}/> : <OverviewRelationship go={stage => navigate({stage})}/>}
        </>}
        {route.stage === 'evidence' && <Evidence input={originalReading} documents={documents} go={stage => navigate({stage})} review={review} frozen={sent || Boolean(changeSummary)} onReview={(id, entry) => setReview(current => ({...current, [id]: entry}))}/>}
        {route.stage === 'request' && <fieldset className="gf-history-controls" disabled={Boolean(changeSummary)}>{(reviewComplete ? <Request go={stage => navigate({stage})} sent={sent} setSent={setSent} selected={selected} setSelected={setSelected} review={review} messages={messages} setMessages={setMessages} due={due} setDue={setDue} clarification={handoff?.clarification}/> : <section className="gf-final-block"><h2>请先逐项复核</h2><p>机器发现尚未由审核员逐项判断并记录依据，不能形成对外请求。</p><Button variant="outline" onClick={() => navigate({stage: 'evidence'})}>去复核证据 <ArrowRight size={16}/></Button></section>)}</fieldset>}
        {route.stage === 'response' && <fieldset className="gf-history-controls" disabled={Boolean(changeSummary)}>{(submission ? <section className="gf-amend-page"><header className="gf-page-heading"><div><div className="gf-eyebrow">回件已登记</div><h1>第 {allSubmissions.length} 批回件</h1><p>{submission.source} · {Object.keys(submission.replies).length} 项答复</p></div></header><div className="gf-amend-stage-actions"><Button variant="outline" onClick={startNextReceipt}>继续登记下一批补件</Button><Button onClick={() => navigate({stage: 'delta'})}>查看补件复审<ArrowRight/></Button></div></section> : <SupplierAmendment selected={effectiveSelected} requests={messages} replies={replies} source={replySource} files={receivedFiles} step={receiptStep} onStep={setReceiptStep} onFilesChange={changeReceivedFiles} onSourceChange={setReplySource} onChange={(key, reply) => setReplies(current => ({...current, [key]: reply}))} onSubmit={submitReplies} variant={route.variant as AmendmentVariant} batchNumber={(priorSubmissions?.length ?? 0) + 1}/>)}</fieldset>}
        {route.stage === 'delta' && <ReassessmentWorkspace documents={documents} key={allSubmissions.at(-1)?.id} id={caseId} original={original} batches={allSubmissions} readonly={Boolean(changeSummary)} onReceive={startNextReceipt} onDecision={openBatchDecision}/>}
        {route.stage === 'decision' && (handoff?.decisionInput && draftId ? <DecisionWorkspace allowFollowup documents={documents} id={draftId} input={handoff.decisionInput} pendingChange={Boolean(changeSummary)} onBack={() => navigate({stage: 'overview'})}/> : decisionBatch ? <DecisionWorkspace documents={documents} key={decisionBatch.id} latestReceiptId={allSubmissions.at(-1)?.id} pendingChange={Boolean(changeSummary)} id={`${caseId}:${decisionBatch.id}`} input={decisionBatch.input} onBack={() => navigate({stage: 'delta'})}/> : <section className="gf-final-block"><h2>先核对补件检查结果</h2><Button onClick={() => navigate({stage: 'delta'})}>查看补件复审</Button></section>)}
        {route.stage === 'history' && <section><h2>历史记录</h2>{decisionRecord && <p>{decisionRecord.id} · {decisionRecord.outcome} · {decisionRecord.reason}</p>}<Button onClick={() => navigate({stage: 'delta'})}>查看回件与决定历史</Button></section>}
      </main>
    </div>
    {import.meta.env.DEV && !directDecision && ['overview', 'response'].includes(route.stage) && !submission && <aside className="gf-switcher" aria-label="布局方案切换"><button aria-label="上一种布局" onClick={() => navigate({variant: activeVariants[(activeVariants.findIndex(item => item.id === route.variant) + activeVariants.length - 1) % activeVariants.length].id})}><ArrowLeft size={16}/></button><span>布局方案　{activeVariants.findIndex(item => item.id === route.variant) + 1} / 3 · {activeVariants.find(item => item.id === route.variant)?.label}</span><button aria-label="下一种布局" onClick={() => navigate({variant: activeVariants[(activeVariants.findIndex(item => item.id === route.variant) + 1) % activeVariants.length].id})}><ArrowRight size={16}/></button></aside>}
    {!directDecision && <span className="gf-state-readout" aria-live="polite">原型状态：{stages.find(item => item.id === route.stage)?.label} · {sent ? '对外请求已模拟记录' : '对外请求未记录'} · {submission ? '回件已固定' : '尚无回件'}</span>}
  </div>
}

if (window.location.pathname.endsWith('/preview/concept.html')) {
  const container = document.getElementById('app')!
  const root = (import.meta.hot?.data.root as Root | undefined) ?? createRoot(container)
  root.render(<ConceptApp/>)
  if (import.meta.hot) import.meta.hot.data.root = root
}
