import {isPreview} from './access'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Throwaway UI prototype: three overview structures, one synthetic PPAP case, no Product API.
import {useEffect, useRef, useState} from 'react'
import {
  ArrowLeft, ArrowRight, ChevronRight, CircleAlert,
  CircleHelp, ClipboardCheck, FileClock, FileText, Files, GitCompareArrows,
  Layers3, Send, ShieldAlert, ShieldCheck,
} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import '../index.css'
import {SupplierAmendment, amendmentVariants, fileMatchesRequest, initialReplies, replyReady, type AmendmentVariant, type ConflictReview, type ReceiptStep, type ReceivedFile, type ReplyMap, type Submission} from './amendment'
import {isObject, readDraft, useSavedDraft} from './saved-draft'
import {BasisSummary} from './requirements'
import type {RequirementBasis} from './requirements-state'
import {DecisionWorkspace, decisionCurrentInput, decisionSummary, type DecisionInput} from './decision'
import {ReassessmentWorkspace, readReassessment, reassessInput, reassessmentDocuments} from './reassessment'
import {AccountMenu} from './access'
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
  return <span className={["inline-flex items-center justify-center w-fit min-h-6.5 py-0.5 px-2 rounded-lg text-xs font-semibold whitespace-nowrap",({"bad":"bg-destructive/10 text-destructive","wait":"bg-muted text-foreground","missing":"bg-muted text-primary","good":"bg-muted text-foreground"} as Record<string,string>)[String(tone)] ?? "",""].filter(Boolean).join("")}>{children}</span>
}

function CaseHeading({eyebrow, title, scope, children}: {eyebrow: string; title: string; scope?: string; children?: React.ReactNode}) {
  return <header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl"}>
    <div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>{eyebrow}</div><h1>{title}</h1><p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>{scope ?? '北辰汽车 · 明川精密 · 图纸 Rev B · 苏州'}</p></div>
    {children}
  </header>
}

function HandoffSummary({handoff}: {handoff: IntakeHandoff}) {
  return <section className={"-mt-1.5 mr-0 mb-7.5 ml-0 border-t border-b"} aria-label="初次收件与供应商说明">
    <div className={"flex flex-wrap items-baseline justify-between gap-y-1.5 gap-x-4 py-3.5 px-0 [&_strong]:text-sm [&_span]:text-muted-foreground [&_span]:text-xs"}><strong>初次收件 {handoff.receiptId}</strong><span>{handoff.files.length} 份原件 · {handoff.disclosures.length} 项已知缺口</span></div>
    {handoff.check && <p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>{handoff.check.method} · 检查号 {handoff.check.id}</p>}
    {handoff.clarification && <div className={"py-3.5 px-4 border-l bg-muted [&_span]:text-primary [&_span]:text-xs [&_span]:font-semibold [&_p]:my-1.5 [&_p]:mx-0 [&_p]:text-sm [&_p]:leading-normal [&_p]:break-all [&_small]:text-muted-foreground [&_small]:text-xs"}><span>供应商澄清 · 流程图第 3 页</span><p>{handoff.clarification}</p><small>说明已收到；原件和该项判断尚未改变。</small></div>}
    <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看收件原件与已知缺口</Button></CollapsibleTrigger><CollapsibleContent><div className={"grid grid-cols-[repeat(2,_minmax(0,_1fr))] gap-5 mt-2.5 [&_p]:grid [&_p]:gap-0.5 [&_p]:mt-0 [&_p]:mr-0 [&_p]:mb-2 [&_p]:ml-0 [&_p]:break-all [&_strong]:text-foreground [&_strong]:font-semibold max-sm:grid-cols-1 max-sm:gap-2"}><div>{handoff.files.map(file => <p key={file.name}><strong>{file.name}</strong><span>原件 v{file.sourceVersion} · Rev {file.revision}</span></p>)}</div><div>{handoff.disclosures.map(item => <p key={`${item.fileName}-${item.issue}`}><strong>{item.fileName} · {item.issue}</strong><span>{item.note}</span></p>)}</div></div></CollapsibleContent></Collapsible>
    <BasisSummary basis={handoff.basis}/>
  </section>
}

function Decision({go}: {go: (stage: Stage) => void}) {
  return <section className={"flex items-center gap-4.5 py-6 px-6.5 border rounded-lg bg-destructive/10 [&_h2]:mt-1 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm max-sm:flex-wrap max-sm:gap-3 max-sm:p-5 max-sm:[&_h2]:text-xl max-sm:[&_p]:text-sm max-sm:[&_p]:leading-normal"} aria-label="当前批准状态">
    <div className={"grid flex-[0_0_42px] place-items-center w-10.5 h-10.5 rounded-lg bg-muted text-destructive"}><ShieldAlert size={23} strokeWidth={1.8} /></div>
    <div className={"flex-1 min-w-0 max-sm:basis-[calc(100%_-_55px)]"}>
      <span className={"text-destructive text-xs font-semibold"}>当前判断</span>
      <h2>暂不能批准</h2>
      <p>1 项确认不一致 · 1 项待核实 · 1 份资料未覆盖</p>
    </div>
    <Button className={""} onClick={() => go('evidence')}>复核差异 <ArrowRight size={16} /></Button>
  </section>
}

function OverviewAction({go}: {go: (stage: Stage) => void}) {
  return <>
    <Decision go={go} />
    <section className={"mt-9.5"}>
      <div className={"flex items-end justify-between gap-5 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><div><h2>需要处理</h2><p>先核对确定的差异，再向供应商提出准确要求。</p></div><span className={"text-muted-foreground text-sm"}>3 项</span></div>
      <div className={"border-t"}>
        <div className={"grid grid-cols-[126px_minmax(0,_1fr)_auto] items-center gap-4.5 min-h-23 py-4.5 px-1.5 border-b [&_strong]:text-base [&_strong]:font-semibold [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm max-sm:grid-cols-[1fr_auto] max-sm:gap-y-2.5 max-sm:gap-x-1.5 max-sm:py-4.5 max-sm:px-0.5 max-sm:[&_strong]:text-sm max-sm:[&_p]:text-xs max-sm:[&_p]:leading-normal"}><Status tone="bad">确认不一致</Status><div><strong>工序 17 的工艺描述不同</strong><p>流程图：热处理　／　控制计划：表面处理</p></div><Button variant="ghost" onClick={() => go('evidence')}>查看证据 <ChevronRight size={16} /></Button></div>
        <div className={"grid grid-cols-[126px_minmax(0,_1fr)_auto] items-center gap-4.5 min-h-23 py-4.5 px-1.5 border-b [&_strong]:text-base [&_strong]:font-semibold [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm max-sm:grid-cols-[1fr_auto] max-sm:gap-y-2.5 max-sm:gap-x-1.5 max-sm:py-4.5 max-sm:px-0.5 max-sm:[&_strong]:text-sm max-sm:[&_p]:text-xs max-sm:[&_p]:leading-normal"}><Status tone="wait">待核实</Status><div><strong>扫描件有一处工序号无法辨认</strong><p>尚不能判断该工序是否符合要求。</p></div><Button variant="ghost" onClick={() => go('evidence')}>确认原件 <ChevronRight size={16} /></Button></div>
        <div className={"grid grid-cols-[126px_minmax(0,_1fr)_auto] items-center gap-4.5 min-h-23 py-4.5 px-1.5 border-b [&_strong]:text-base [&_strong]:font-semibold [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm max-sm:grid-cols-[1fr_auto] max-sm:gap-y-2.5 max-sm:gap-x-1.5 max-sm:py-4.5 max-sm:px-0.5 max-sm:[&_strong]:text-sm max-sm:[&_p]:text-xs max-sm:[&_p]:leading-normal"}><Status tone="missing">需补交</Status><div><strong>尺寸报告版本不对应</strong><p>现有 Rev A 报告，不能覆盖本案图纸 Rev B。</p></div><Button variant="ghost" onClick={() => go('request')}>准备补件 <ChevronRight size={16} /></Button></div>
      </div>
    </section>
    <section className={"mt-9.5 mt-10"}>
      <div className={"flex items-end justify-between gap-5 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><div><h2>本轮资料</h2><p>三份文件，均可追溯原始版本。</p></div></div>
      <div className={"grid grid-cols-[repeat(3,_1fr)] gap-2.5 [&_>_div]:grid [&_>_div]:grid-cols-[20px_1fr] [&_>_div]:gap-y-2 [&_>_div]:gap-x-2 [&_>_div]:min-h-25 [&_>_div]:p-4.5 [&_>_div]:border [&_>_div]:rounded-lg [&_>_div]:bg-background [&_svg]:text-muted-foreground [&_span]:min-w-0 [&_span]:break-all [&_span]:text-sm [&_span]:font-semibold [&_small]:col-start-2 [&_small]:text-muted-foreground [&_small]:text-xs max-sm:grid-cols-1 max-sm:gap-0 max-sm:[&_>_div]:min-h-18 max-sm:[&_>_div]:py-4 max-sm:[&_>_div]:px-0 max-sm:[&_>_div]:border-0 max-sm:[&_>_div]:border-b max-sm:[&_>_div]:rounded-lg max-sm:[&_>_div]:bg-muted"}>
        <div><FileText size={18} /><span>过程流程图_B.pdf</span><small>扫描件 · 有待核实内容</small></div>
        <div><FileText size={18} /><span>控制计划_B.xlsx</span><small>Rev B · 工序 17 存在差异</small></div>
        <div><FileClock size={18} /><span>尺寸报告_A.xlsx</span><small>Rev A · 不覆盖本轮</small></div>
      </div>
    </section>
  </>
}

function MatrixCell({children, tone}: {children: React.ReactNode; tone?: 'bad' | 'wait' | 'missing'}) {
  return <td className={tone === 'bad' ? 'text-destructive' : tone ? 'text-muted-foreground' : ''}>{children}</td>
}

function OverviewMatrix({go}: {go: (stage: Stage) => void}) {
  return <>
    <Decision go={go} />
    <section className={"mt-9.5 [&_table]:w-full [&_table]:min-w-175 [&_table]:border-collapse [&_table]:text-left [&_th]:py-4.5 [&_th]:px-3.5 [&_th]:border-b [&_th]:border-r [&_th]:text-sm [&_th]:align-top [&_td]:py-4.5 [&_td]:px-3.5 [&_td]:border-b [&_td]:border-r [&_td]:text-sm [&_td]:align-top [&_th:last-child]:border-r-0 [&_td:last-child]:border-r-0 [&_tbody_tr:last-child_th]:border-b-0 [&_tbody_tr:last-child_td]:border-b-0 [&_thead_th]:bg-background [&_thead_th]:text-muted-foreground [&_thead_th]:text-xs [&_thead_th]:font-semibold [&_tbody_th]:min-w-39 [&_tbody_th]:font-semibold [&_td]:text-muted-foreground [&_td_strong]:text-foreground [&_td_strong]:font-semibold"}>
      <div className={"flex items-end justify-between gap-5 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><div><h2>要求与资料</h2><p>按要求检查每份资料的覆盖情况。</p></div></div>
      <div className={"overflow-x-auto border rounded-lg bg-background"}><table>
        <thead><tr><th>审核要求</th><th>过程流程图</th><th>控制计划</th><th>尺寸报告</th><th>本轮结论</th></tr></thead>
        <tbody>
          <tr><th>工序描述一致</th><MatrixCell tone="bad">工序 17<br/><strong>热处理</strong></MatrixCell><MatrixCell tone="bad">工序 17<br/><strong>表面处理</strong></MatrixCell><MatrixCell>—</MatrixCell><MatrixCell><Status tone="bad">不一致</Status></MatrixCell></tr>
          <tr><th>工序号可核对</th><MatrixCell tone="wait">扫描处无法辨认</MatrixCell><MatrixCell>工序号可读</MatrixCell><MatrixCell>—</MatrixCell><MatrixCell><Status tone="wait">待核实</Status></MatrixCell></tr>
          <tr><th>尺寸报告对应 Rev B</th><MatrixCell>—</MatrixCell><MatrixCell>—</MatrixCell><MatrixCell tone="missing">仅有 Rev A</MatrixCell><MatrixCell><Status tone="missing">未覆盖</Status></MatrixCell></tr>
        </tbody>
      </table></div>
      <Button variant="outline" className={"mt-4.5 min-h-9.5"} onClick={() => go('evidence')}>查看工序 17 的原文 <ArrowRight size={16} /></Button>
    </section>
  </>
}

function OverviewRelationship({go}: {go: (stage: Stage) => void}) {
  return <>
    <Decision go={go} />
    <section className={"mt-9.5"}>
      <div className={"flex items-end justify-between gap-5 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><div><h2>资料之间的关系</h2><p>结论来自文件中的具体内容，不来自文件名。</p></div></div>
      <div className={"grid grid-cols-[minmax(0,_1fr)_136px_minmax(0,_1fr)] items-center gap-3 max-lg:grid-cols-[minmax(0,_1fr)_105px_minmax(0,_1fr)] max-sm:grid-cols-1"}>
        <div className={"grid gap-2.5 min-h-42.5 p-5.5 border rounded-lg bg-background [&_small]:text-muted-foreground [&_small]:text-xs [&_strong]:text-2xl [&_strong]:font-bold max-sm:min-h-31.5"}><span className={"text-muted-foreground text-sm font-medium"}>过程流程图_B.pdf</span><small>第 3 页 · 工序 17</small><strong>热处理</strong></div>
        <div className={"grid justify-items-center gap-1.5 text-destructive text-xs font-semibold text-center       max-sm:grid-cols-[1fr_auto_1fr] max-sm:items-center  "}><span>同一工序</span><CircleAlert size={19}/><span>描述不同</span></div>
        <div className={"grid gap-2.5 min-h-42.5 p-5.5 border rounded-lg bg-background [&_small]:text-muted-foreground [&_small]:text-xs [&_strong]:text-2xl [&_strong]:font-bold max-sm:min-h-31.5"}><span className={"text-muted-foreground text-sm font-medium"}>控制计划_B.xlsx</span><small>工序 17</small><strong>表面处理</strong></div>
      </div>
      <div className={"grid gap-0.5 mt-6.5 border-t [&_>_div]:flex [&_>_div]:items-center [&_>_div]:gap-3 [&_>_div]:min-h-15.5 [&_>_div]:border-b [&_svg]:text-muted-foreground [&_span:nth-child(2)]:flex-1 [&_span:nth-child(2)]:text-sm max-sm:[&_>_div]:items-start max-sm:[&_>_div]:flex-wrap max-sm:[&_>_div]:gap-y-2 max-sm:[&_>_div]:gap-x-2.5 max-sm:[&_>_div]:py-4 max-sm:[&_>_div]:px-0"}><div><CircleHelp size={18}/><span>流程图另有一处工序号无法辨认</span><Status tone="wait">待核实</Status></div><div><FileClock size={18}/><span>尺寸报告只有 Rev A，本轮要求 Rev B</span><Status tone="missing">未覆盖</Status></div></div>
      <Button variant="outline" className={"mt-4.5 min-h-9.5"} onClick={() => go('evidence')}>核对两处原文 <ArrowRight size={16} /></Button>
    </section>
  </>
}

function Evidence({go, review, onReview, frozen, input, documents}: {input: DecisionInput; documents: DemoDocument[]; go: (stage: Stage) => void; review: ReviewMap; onReview: (id: RequestKey, entry: ReviewEntry) => void; frozen: boolean}) {
  const [showContext, setShowContext] = useState(false)
  const complete = requestRows.every(row => review[row.id].outcome !== 'pending' && review[row.id].reason.trim())
  return <>
    <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={() => go('overview')}><ArrowLeft size={16}/> 事项概览</Button>
    <CaseHeading eyebrow="证据复核" title="核对原件与发现"><Status tone="bad">机器发现待复核</Status></CaseHeading>
    <div className={"flex items-center gap-3.5 py-4 px-0 border-t border-b [&_svg]:text-primary [&_div]:grid [&_div]:gap-1 [&_span]:text-muted-foreground [&_span]:text-xs [&_strong]:text-base [&_strong]:font-semibold"}><ClipboardCheck size={19}/><div><span>适用要求</span><strong>流程图与控制计划的工序名称应一致。</strong></div></div>
    <section className={"grid grid-cols-[repeat(2,_minmax(0,_1fr))] gap-4.5 mt-6.5 max-sm:grid-cols-1"} aria-label="两份原件的摘录">
      <article className={"overflow-hidden border rounded-lg bg-background [&_header]:flex [&_header]:items-start [&_header]:gap-3 [&_header]:py-5 [&_header]:px-5.5 [&_header]:border-b [&_header_svg]:text-primary [&_h2]:m-0 [&_h2]:break-all [&_h2]:text-base [&_h2]:font-semibold [&_header_p]:mt-1.5 [&_header_p]:mr-0 [&_header_p]:mb-0 [&_header_p]:ml-0 [&_header_p]:text-muted-foreground [&_header_p]:text-xs"}><header><FileText size={18}/><div><h2>过程流程图_B.pdf</h2><p>第 3 页 · 工序 17 · Rev B</p></div></header><div className={"min-h-42.5 p-6 [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_p]:my-6 [&_p]:mx-0 [&_p]:text-xl [&_p]:font-semibold [&_mark]:py-1 [&_mark]:px-1.5 [&_mark]:rounded-lg [&_mark]:bg-muted [&_mark]:text-foreground [&_small]:text-muted-foreground max-sm:min-h-32.5"}><span>原件摘录</span><p>工序 17　<mark>热处理</mark></p>{showContext && <small>前一道：机加工　·　后一道：表面检查</small>}</div></article>
      <article className={"overflow-hidden border rounded-lg bg-background [&_header]:flex [&_header]:items-start [&_header]:gap-3 [&_header]:py-5 [&_header]:px-5.5 [&_header]:border-b [&_header_svg]:text-primary [&_h2]:m-0 [&_h2]:break-all [&_h2]:text-base [&_h2]:font-semibold [&_header_p]:mt-1.5 [&_header_p]:mr-0 [&_header_p]:mb-0 [&_header_p]:ml-0 [&_header_p]:text-muted-foreground [&_header_p]:text-xs"}><header><FileText size={18}/><div><h2>控制计划_B.xlsx</h2><p>工作表「控制计划」· 工序 17 · Rev B</p></div></header><div className={"min-h-42.5 p-6 [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_p]:my-6 [&_p]:mx-0 [&_p]:text-xl [&_p]:font-semibold [&_mark]:py-1 [&_mark]:px-1.5 [&_mark]:rounded-lg [&_mark]:bg-muted [&_mark]:text-foreground [&_small]:text-muted-foreground max-sm:min-h-32.5"}><span>原件摘录</span><p>工序名称　<mark>表面处理</mark></p>{showContext && <small>对应工序号：17　·　同一零件 P-204</small>}</div></article>
    </section>
    <Button variant="ghost" className={""} onClick={() => setShowContext(!showContext)}>{showContext ? '收起上下文' : '展开相邻内容'}</Button>
    <section className={"flex items-center justify-between gap-4.5 mt-6.5 py-5 px-0 border-t border-b [&_>_div]:flex [&_>_div]:items-center [&_>_div]:gap-3 [&_p]:m-0 [&_p]:text-sm max-sm:items-stretch max-sm:flex-col max-sm:[&_>_div]:items-start max-sm:[&_>_div]:flex-col max-sm:[&_>_div]:gap-2"}><div><Status tone="bad">机器发现</Status><p>两份原件的工序名称不同。人工结论需要逐项记录。</p></div></section>
    <section className={"mt-8.5 border-t [&_header]:pt-5.5 [&_header]:pr-0 [&_header]:pb-2 [&_header]:pl-0 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1.5 [&_h2]:ml-0 [&_h2]:text-lg [&_header_p]:m-0 [&_header_p]:text-muted-foreground [&_header_p]:text-sm [&_header_p]:leading-normal [&_article]:grid [&_article]:grid-cols-[minmax(0,_1fr)_180px] [&_article]:gap-y-3.5 [&_article]:gap-x-5.5 [&_article]:py-5 [&_article]:px-0 [&_article]:border-b [&_article_>_div]:grid [&_article_>_div]:gap-1.5 [&_article_>_div]:min-w-0 [&_label]:grid [&_label]:gap-1.5 [&_label]:min-w-0 [&_strong]:text-sm [&_small]:text-muted-foreground [&_small]:text-xs [&_article_>_div_p]:mt-0.5 [&_article_>_div_p]:mr-0 [&_article_>_div_p]:mb-0 [&_article_>_div_p]:ml-0 [&_article_>_div_p]:text-foreground [&_article_>_div_p]:text-xs [&_article_>_div_p]:leading-normal [&_label]:text-muted-foreground [&_label]:text-xs [&_label]:font-semibold [&_article_>_label:last-child]:col-span-full max-sm:[&_article]:grid-cols-1 max-sm:[&_article_>_label:last-child]:col-[auto] grid gap-4 mt-5 max-w-190 [&[data-readonly=true]]:gap-2"} aria-label="逐项人工复核"><header><h2>逐项复核</h2><p>选择处理方式并说明依据。推翻发现不会改写原始证据。</p></header>{requestRows.map(row => <article key={row.id}><div><strong>{row.name}</strong><small>{row.source}</small><p>{row.observation}</p><SourceReaderButton input={input} documents={documents} clauseId={({conflict: 'R1', scan: 'R2', report: 'R3'})[row.id]}/></div><label>复核动作<NativeSelect aria-label={`${row.name}的复核动作`} value={review[row.id].outcome} disabled={frozen} onChange={event => onReview(row.id, {...review[row.id], outcome: event.target.value as ReviewOutcome})}><NativeSelectOption value="pending">尚未判断</NativeSelectOption><NativeSelectOption value="maintain">维持发现</NativeSelectOption><NativeSelectOption value="overturn">推翻发现</NativeSelectOption><NativeSelectOption value="defer">暂缓判断</NativeSelectOption></NativeSelect></label><label>判断依据<Textarea aria-label={`${row.name}的判断依据`} value={review[row.id].reason} disabled={frozen} onChange={event => onReview(row.id, {...review[row.id], reason: event.target.value})} placeholder="记录核对了什么，以及为什么这样判断"/></label></article>)}<div className={"flex items-center justify-between gap-4 mt-5 [&_span]:text-muted-foreground [&_span]:text-sm max-sm:items-stretch max-sm:flex-col"}><span>{requestRows.filter(row => review[row.id].outcome !== 'pending' && review[row.id].reason.trim()).length} / {requestRows.length} 项已复核</span><Button className={""} disabled={!complete} onClick={() => go('request')}>{frozen ? '查看补正请求' : '准备补正请求'} <ArrowRight size={16}/></Button></div></section>
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
    <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={() => go('evidence')}><ArrowLeft size={16}/> 证据复核</Button>
    <CaseHeading eyebrow="对外沟通 · 草稿" title="准备补正请求"><Status tone={sent ? 'good' : 'wait'}>{sent ? '已模拟记录' : '待联系供应商'}</Status></CaseHeading>
    <p className={"-mt-3 mr-0 mb-6 ml-0 text-muted-foreground"}>审核员核对请求后，通过现有渠道联系供应商。本原型不发送消息。</p>
    <section className={"border-t"} aria-label="补正条目">
      {requestRows.map(row => <div className={"flex items-start gap-4.5 py-5.5 px-1 border-b max-sm:gap-3"} key={row.id}>
        <Checkbox aria-label={`包含${row.name}`} checked={selected[row.id] && review[row.id].outcome !== 'overturn'} disabled={sent || review[row.id].outcome === 'overturn'} onCheckedChange={checked => setSelected({...selected, [row.id]: checked === true})}/>
        <div className={"flex-1 min-w-0 [&_>_small]:block [&_>_small]:mt-1.5 [&_>_small]:mr-0 [&_>_small]:mb-3 [&_>_small]:ml-0 [&_>_small]:text-muted-foreground [&_>_small]:text-xs"}><div className={"flex items-center gap-3 [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold [&_span]:text-muted-foreground [&_span]:text-xs"}><h2>{row.name}</h2><span>{review[row.id].outcome === 'overturn' ? '已推翻，不发送' : row.tag}</span></div><small>依据：{row.source} · 人工判断：{review[row.id].reason}</small>{row.id === 'scan' && clarification && <p className={"mt-0 mr-0 mb-3 ml-0 text-primary text-xs leading-normal"}>供应商此前澄清：{clarification}</p>}<Textarea aria-label={`${row.name}的请求内容`} value={messages[row.id]} disabled={sent || !selected[row.id] || review[row.id].outcome === 'overturn'} onChange={event => setMessages({...messages, [row.id]: event.target.value})}/></div>
      </div>)}
    </section>
    <div className={"flex items-end flex-wrap gap-y-5.5 gap-x-7.5 mt-7 max-sm:grid max-sm:gap-4"}><div className={"grid gap-2 [&_span]:text-muted-foreground [&_span]:text-xs [&_strong]:py-2.5 [&_strong]:px-0 [&_strong]:text-sm [&_strong]:font-semibold"}><span>联系对象</span><strong>明川精密 · 供应商质量负责人</strong></div><label className={"grid gap-2 [&_span]:text-muted-foreground [&_span]:text-xs [&_strong]:py-2.5 [&_strong]:px-0 [&_strong]:text-sm [&_strong]:font-semibold"}><span>希望回复日期</span><Input type="date" value={due} disabled={sent} onChange={event => setDue(event.target.value)}/></label><div className={"flex items-center gap-3.5 ml-auto [&_>_span]:text-muted-foreground [&_>_span]:text-sm max-sm:justify-between max-sm:m-0"}><span>已选 {count} / 3 项</span>{sent ? <Button className={""} onClick={() => go('response')}>登记收到的回件 <ArrowRight size={16}/></Button> : <Button className={""} disabled={!count || !due || requestRows.some(row => selected[row.id] && review[row.id].outcome !== 'overturn' && !messages[row.id].trim())} onClick={() => setSent(true)}>模拟记录对外请求 <Send size={16}/></Button>}</div></div>
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
    const onKey = (event: KeyboardEvent) => {if (!isPreview() || !import.meta.env.DEV) return;
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
  return <div className={"flex min-h-dvh bg-background text-foreground text-base"}>
    <aside className={"sticky top-0 self-start flex flex-[0_0_224px] flex-col min-h-dvh pt-7 pr-3.5 pb-4.5 pl-3.5 border-r bg-background [&_nav]:grid [&_nav]:gap-1.5 max-xl:basis-47 max-lg:hidden"}>
      <div className={"flex items-center gap-2.5 pt-0 pr-3 pb-14 pl-3 text-lg font-bold tracking-tight [&_small]:ml-auto [&_small]:text-muted-foreground [&_small]:text-xs [&_small]:font-medium [&_small]:tracking-wide"}><div className={"grid place-items-center w-7.5 h-7.5 rounded-lg bg-muted text-foreground"}><ClipboardCheck size={19}/></div><span>PPAP</span><small>{isPreview() ? '交互预览' : '审核工作台'}</small></div>
      {onHome && <Button variant="ghost" onClick={onHome}><ArrowLeft/>工作台</Button>}
      <div className={"pt-0 pr-3 pb-3 pl-3 text-muted-foreground text-xs font-semibold tracking-wide"}>审核团队{changeContext?.scope.customer ? ` · ${changeContext.scope.customer}` : ''}</div>
      <nav aria-label="事项流程">{visibleStages.map(({id, label, icon: Icon}) => <Button variant="ghost" key={id} aria-current={route.stage === id ? 'page' : undefined} disabled={stageDisabled(id)} onClick={() => navigate({stage: id})}><Icon size={18}/><span>{directDecision && id === 'decision' ? '复核与决定' : label}</span></Button>)}</nav>
      <div className={"flex items-center gap-2.5 mt-auto pt-4.5 pr-2.5 pb-1 pl-2.5 border-t"}><AccountMenu/></div>
    </aside>
    <div className={"flex-1 min-w-0"}>
      <header className={"flex items-center justify-between gap-3 h-15 py-0 px-10.5 border-b bg-background text-muted-foreground text-sm [&_>_div]:flex [&_>_div]:items-center [&_>_div]:gap-2.5 [&_>_div]:min-w-0 [&_strong]:overflow-hidden [&_strong]:text-foreground [&_strong]:font-semibold [&_strong]:text-ellipsis [&_strong]:whitespace-nowrap max-xl:py-0 max-xl:px-7 max-lg:h-13.5 max-lg:py-0 max-lg:px-5.5 max-sm:py-0 max-sm:px-4.5"}><div><span className={"w-2 h-2 rounded-full bg-primary"}/><span>量产批准工作台</span><span className={"text-foreground"}>/</span><strong>{caseTitle}</strong></div><span className={"text-muted-foreground whitespace-nowrap max-sm:hidden"}>正式批准与交付尚为原型</span><AccountMenu compact className={"hidden max-lg:inline-flex"}/></header>
      <nav ref={mobileNav} className={"hidden max-lg:flex max-lg:gap-1.5 max-lg:overflow-x-auto max-lg:py-2 max-lg:px-4.5 max-lg:border-b max-lg:bg-background"} aria-label="事项流程">{visibleStages.map(({id, label}) => <Button variant="ghost" key={id} aria-current={route.stage === id ? 'page' : undefined} disabled={stageDisabled(id)} onClick={() => navigate({stage: id})}>{directDecision && id === 'decision' ? '复核与决定' : label}</Button>)}</nav>
      <main className={"w-full max-w-280 my-0 mx-auto pt-10.5 pr-12 pb-30 pl-12 max-xl:pr-7 max-xl:pl-7 max-lg:pt-8 max-sm:pt-6.5 max-sm:pr-4.5 max-sm:pb-29 max-sm:pl-4.5"}>
        {onHome && !(directDecision && route.stage === 'decision') && <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={onHome}><ArrowLeft size={16}/> 工作台</Button>}
        {saveError && <p role="alert">{saveError}</p>}
        {route.stage !== 'changes' && changeContext && changeSummary && <div className={"flex gap-3.5 items-center justify-between py-3.5 px-0 mb-4 border-b text-primary max-md:flex-col max-md:items-start"}><span>{changeSummary.state} · 此页是此前依据</span><Button variant="outline" onClick={() => navigate({stage: 'changes'})}>查看变化与新轮次<ArrowRight/></Button></div>}
        {route.stage === 'changes' && changeContext && <ChangeWorkspace documents={documents} id={caseId} original={changeInput} sourceDecisionId={directDecision ? draftId : undefined} onSourceReview={() => navigate({stage: 'decision'})} scope={changeContext.scope} collections={changeContext.collections} onBack={() => navigate({stage: 'overview'})}/>}
        {route.stage === 'overview' && <>
          <CaseHeading eyebrow="量产批准事项 · 人工复核" title={caseTitle} scope={currentDecisionInput?.scope}><span className={"self-center text-muted-foreground text-sm whitespace-nowrap max-sm:mt-1"}>{currentDecisionInput?.receiptId ?? handoff?.receiptId ?? '第 2 次收件'}</span></CaseHeading>
          {handoff && <HandoffSummary handoff={handoff}/>}
          {directDecision && currentDecisionInput ? <section className={"mt-9.5"}>
            <div className={"flex items-end justify-between gap-5 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><div><h2>{recordedSummary?.state ?? '核对原件，记录人工判断'}</h2>{recordedSummary && <p>{recordedSummary.detail}</p>}</div></div>
            <h3>当前机器检查</h3><div className={"border-t"}>{currentDecisionInput.evidence.map(row => <div className={"grid grid-cols-[126px_minmax(0,_1fr)_auto] items-center gap-4.5 min-h-23 py-4.5 px-1.5 border-b [&_strong]:text-base [&_strong]:font-semibold [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm max-sm:grid-cols-[1fr_auto] max-sm:gap-y-2.5 max-sm:gap-x-1.5 max-sm:py-4.5 max-sm:px-0.5 max-sm:[&_strong]:text-sm max-sm:[&_p]:text-xs max-sm:[&_p]:leading-normal"} key={row.id}><Status tone={row.result === '满足' ? 'good' : row.result === '不满足' ? 'bad' : 'wait'}>{row.result}</Status><div><strong>{row.displayId || row.id} · {row.title}</strong><p>{row.explanation}</p></div></div>)}</div>
            <Button className={"mt-4.5 min-h-9.5"} onClick={() => navigate({stage: 'decision'})}>{recordedSummary?.recorded ? '查看决定与报告' : '复核依据与决定'}<ArrowRight/></Button>
          </section> : allSubmissions.length ? <section className={"mt-9.5"}><h2>{savedReviewSummary(caseId)?.state ?? '补件处理中'}</h2><p>已收到 {allSubmissions.length} 批回件。原始发现保留在证据复核中；当前依据见补件复审。</p><Button className={""} onClick={() => navigate({stage: submission ? 'delta' : 'response'})}>{submission ? '继续补件复审' : '继续登记本批补件'}<ArrowRight/></Button></section> : handoff && !handoff.matchesFixedReview ? <section className={"py-6 px-0 border-t [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-2 [&_h2]:ml-0 [&_h2]:text-lg [&_p]:max-w-167.5 [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-relaxed"}><h2>本次来件与固定演示样例不同</h2><p>原件及修订已保留在上方。本原型没有对应的检查结果，因此不能套用预设的工序差异、缺件结论或补正请求。</p></section> : route.variant === 'action' ? <OverviewAction go={stage => navigate({stage})}/> : route.variant === 'matrix' ? <OverviewMatrix go={stage => navigate({stage})}/> : <OverviewRelationship go={stage => navigate({stage})}/>}
        </>}
        {route.stage === 'evidence' && <Evidence input={originalReading} documents={documents} go={stage => navigate({stage})} review={review} frozen={sent || Boolean(changeSummary)} onReview={(id, entry) => setReview(current => ({...current, [id]: entry}))}/>}
        {route.stage === 'request' && <fieldset className={"border-0 p-0 m-0 min-w-0"} disabled={Boolean(changeSummary)}>{(reviewComplete ? <Request go={stage => navigate({stage})} sent={sent} setSent={setSent} selected={selected} setSelected={setSelected} review={review} messages={messages} setMessages={setMessages} due={due} setDue={setDue} clarification={handoff?.clarification}/> : <section className={"[&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1.5 [&_h2]:ml-0 [&_h2]:text-lg [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal mt-8 pb-6.5 border-b [&_ul]:my-3.5 [&_ul]:mx-0 [&_ul]:pl-5 [&_ul]:text-sm [&_ul]:leading-relaxed"}><h2>请先逐项复核</h2><p>机器发现尚未由审核员逐项判断并记录依据，不能形成对外请求。</p><Button variant="outline" onClick={() => navigate({stage: 'evidence'})}>去复核证据 <ArrowRight size={16}/></Button></section>)}</fieldset>}
        {route.stage === 'response' && <fieldset className={"border-0 p-0 m-0 min-w-0"} disabled={Boolean(changeSummary)}>{(submission ? <section className={"pb-7.5"}><header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>回件已登记</div><h1>第 {allSubmissions.length} 批回件</h1><p>{submission.source} · {Object.keys(submission.replies).length} 项答复</p></div></header><div className={"flex justify-between items-center flex-wrap gap-3 max-w-212.5 max-sm:items-stretch"}><Button variant="outline" onClick={startNextReceipt}>继续登记下一批补件</Button><Button onClick={() => navigate({stage: 'delta'})}>查看补件复审<ArrowRight/></Button></div></section> : <SupplierAmendment selected={effectiveSelected} requests={messages} replies={replies} source={replySource} files={receivedFiles} step={receiptStep} onStep={setReceiptStep} onFilesChange={changeReceivedFiles} onSourceChange={setReplySource} onChange={(key, reply) => setReplies(current => ({...current, [key]: reply}))} onSubmit={submitReplies} variant={route.variant as AmendmentVariant} batchNumber={(priorSubmissions?.length ?? 0) + 1}/>)}</fieldset>}
        {route.stage === 'delta' && <ReassessmentWorkspace documents={documents} key={allSubmissions.at(-1)?.id} id={caseId} original={original} batches={allSubmissions} readonly={Boolean(changeSummary)} onReceive={startNextReceipt} onDecision={openBatchDecision}/>}
        {route.stage === 'decision' && (handoff?.decisionInput && draftId ? <DecisionWorkspace allowFollowup documents={documents} id={draftId} input={handoff.decisionInput} pendingChange={Boolean(changeSummary)} onBack={() => navigate({stage: 'overview'})}/> : decisionBatch ? <DecisionWorkspace documents={documents} key={decisionBatch.id} latestReceiptId={allSubmissions.at(-1)?.id} pendingChange={Boolean(changeSummary)} id={`${caseId}:${decisionBatch.id}`} input={decisionBatch.input} onBack={() => navigate({stage: 'delta'})}/> : <section className={"[&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1.5 [&_h2]:ml-0 [&_h2]:text-lg [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal mt-8 pb-6.5 border-b [&_ul]:my-3.5 [&_ul]:mx-0 [&_ul]:pl-5 [&_ul]:text-sm [&_ul]:leading-relaxed"}><h2>先核对补件检查结果</h2><Button onClick={() => navigate({stage: 'delta'})}>查看补件复审</Button></section>)}
        {route.stage === 'history' && <section><h2>历史记录</h2>{decisionRecord && <p>{decisionRecord.id} · {decisionRecord.outcome} · {decisionRecord.reason}</p>}<Button onClick={() => navigate({stage: 'delta'})}>查看回件与决定历史</Button></section>}
      </main>
    </div>
    {isPreview() && import.meta.env.DEV && !directDecision && ['overview', 'response'].includes(route.stage) && !submission && <aside className={"fixed z-10 bottom-5 left-[50%] flex items-center gap-2.5 p-1.5 border rounded-lg bg-muted shadow-sm text-foreground text-xs whitespace-nowrap max-sm:bottom-3.5"} aria-label="布局方案切换"><Button variant="ghost" aria-label="上一种布局" onClick={() => navigate({variant: activeVariants[(activeVariants.findIndex(item => item.id === route.variant) + activeVariants.length - 1) % activeVariants.length].id})}><ArrowLeft size={16}/></Button><span>布局方案　{activeVariants.findIndex(item => item.id === route.variant) + 1} / 3 · {activeVariants.find(item => item.id === route.variant)?.label}</span><Button variant="ghost" aria-label="下一种布局" onClick={() => navigate({variant: activeVariants[(activeVariants.findIndex(item => item.id === route.variant) + 1) % activeVariants.length].id})}><ArrowRight size={16}/></Button></aside>}
    {!directDecision && <span className={"sr-only"} aria-live="polite">原型状态：{stages.find(item => item.id === route.stage)?.label} · {sent ? '对外请求已模拟记录' : '对外请求未记录'} · {submission ? '回件已固定' : '尚无回件'}</span>}
  </div>
}
