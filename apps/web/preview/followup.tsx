// Stitch: projects/13261964020862199279/screens/e9ecc53d96384857be318fdc39372e9c.
// Request revisions: screens/74006c3b07ac42758cac671c2659fafb in the same project.
// Request-led, split receipt desk, and guided layouts share the existing decision draft.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, Download, Plus, Upload, X} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {Checkbox} from '../src/components/ui/checkbox'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../src/components/ui/select'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../src/components/ui/dialog'
import type {DecisionInput} from './decision'
import {JudgmentEditor, judgmentComplete, requirementSatisfied, validJudgment, type Judgment} from './judgment'
import {fileAccept, optionalLocalFile, useLocalUploads, validLocalFile} from './local-files'
import {isObject} from './saved-draft'
import {isRequirementBasis} from './requirements-state'
import {demoDocument, mergeDocuments, sourceKey, validDemoDocument, type DemoDocument, type DemoFlavor, type SourceFile} from './source-documents'
import {SourceReaderButton} from './source-reader'
import './followup.css'

export type FollowupRequest = {id: string; clauseId: string; title: string; requirement: string; question: string; at: string
  replaces?: string; reason?: string; withdrawn?: {at: string; reason: string}}
type RequestComposer = {clauseId: string; text: string; action?: 'append' | 'revise' | 'withdraw'; requestId?: string; reason?: string}
type ReplyFile = {file: SourceFile; replaces: string; requests: string[]; checked: boolean; demo?: DemoFlavor}
export type ReplyDraft = {id: string; source: string; files: ReplyFile[]; answers: Record<string, string>; step: number}
type ReplyHistory = {id: string; at: string; answers: Record<string, string>; previous: DecisionInput; judgments: Record<string, Judgment>}
export type FollowupState = {requests: FollowupRequest[]; composer: RequestComposer | null; draft: ReplyDraft | null; history: ReplyHistory[]; documents?: DemoDocument[]}
const demoReplies: {flavor: DemoFlavor; name: string; label: string}[] = [
  {flavor: 'control-clear', name: '控制计划_B_补正.xlsx', label: '修正控制计划：热处理'},
  {flavor: 'flow-clear', name: '过程流程图_B_清晰.pdf', label: '清晰流程图：热处理'},
  {flavor: 'flow-changed', name: '过程流程图_B_变更.pdf', label: '变更流程图：表面处理'},
  {flavor: 'dimension-clear', name: '尺寸报告_B_复测.xlsx', label: 'B 版尺寸：三件满足'},
  {flavor: 'dimension-outlier', name: '尺寸报告_B_超差.xlsx', label: 'B 版尺寸：第二件超差'},
]
const validDemoReply = (row: ReplyFile) => !row.file.localFile && demoReplies.some(item => item.flavor === row.demo && item.name === row.file.name)
export const emptyFollowup = (): FollowupState => ({requests: [], composer: null, draft: null, history: []})
const strings = (value: unknown): value is Record<string, string> => isObject(value) && Object.values(value).every(item => typeof item === 'string')
function validSource(value: unknown): value is SourceFile {
  return isObject(value) && typeof value.name === 'string' && typeof value.revision === 'string' && Number.isInteger(value.sourceVersion)
    && Number(value.sourceVersion) >= 0 && (value.pending === undefined || typeof value.pending === 'boolean') && optionalLocalFile(value.localFile)
}
export function validDecisionInput(value: unknown): value is DecisionInput {
  return isObject(value) && typeof value.receiptId === 'string' && typeof value.scope === 'string'
    && (value.basis === null || isRequirementBasis(value.basis)) && (value.clarification === null || typeof value.clarification === 'string')
    && Array.isArray(value.files) && value.files.every(validSource) && Array.isArray(value.evidence)
    && value.evidence.every(row => isObject(row) && ['id', 'title', 'requirement', 'explanation'].every(key => typeof row[key] === 'string')
      && ['满足', '不满足', '无法判断', '未覆盖', '执行失败', '不适用'].includes(String(row.result)) && Array.isArray(row.sources)
      && row.sources.every(source => isObject(source) && ['file', 'location', 'excerpt'].every(key => typeof source[key] === 'string')))
    && (value.receipts === undefined || Array.isArray(value.receipts) && value.receipts.every(receipt => isObject(receipt)
      && ['id', 'source', 'notes'].every(key => typeof receipt[key] === 'string') && Array.isArray(receipt.files) && receipt.files.every(validSource)))
}
export function validFollowup(value: unknown): value is FollowupState {
  if (!isObject(value) || !Array.isArray(value.requests) || !Array.isArray(value.history)) return false
  if (value.documents !== undefined && (!Array.isArray(value.documents) || !value.documents.every(validDemoDocument))) return false
  const ids = new Set<string>(), replaced = new Set<string>()
  for (const row of value.requests) {
    if (!isObject(row) || !['id', 'clauseId', 'title', 'requirement', 'question', 'at'].every(key => typeof row[key] === 'string') || ids.has(String(row.id))) return false
    if (row.reason !== undefined && typeof row.reason !== 'string') return false
    if (row.withdrawn !== undefined && (!isObject(row.withdrawn) || typeof row.withdrawn.at !== 'string' || typeof row.withdrawn.reason !== 'string' || !row.withdrawn.reason.trim())) return false
    if (row.replaces !== undefined) {
      const previous = value.requests.find(item => isObject(item) && item.id === row.replaces)
      if (typeof row.replaces !== 'string' || !ids.has(row.replaces) || replaced.has(row.replaces) || !previous || previous.withdrawn || previous.clauseId !== row.clauseId || previous.requirement !== row.requirement || !String(row.reason ?? '').trim()) return false
      replaced.add(row.replaces)
    }
    ids.add(String(row.id))
  }
  if (value.composer !== null && (!isObject(value.composer) || typeof value.composer.clauseId !== 'string' || typeof value.composer.text !== 'string'
    || value.composer.action !== undefined && !['append', 'revise', 'withdraw'].includes(String(value.composer.action))
    || value.composer.requestId !== undefined && (typeof value.composer.requestId !== 'string' || !ids.has(value.composer.requestId))
    || value.composer.reason !== undefined && typeof value.composer.reason !== 'string')) return false
  const draft = value.draft
  if (draft !== null && (!isObject(draft) || typeof draft.id !== 'string' || typeof draft.source !== 'string' || ![0, 1, 2].includes(Number(draft.step))
    || !strings(draft.answers) || Object.keys(draft.answers).some(id => !ids.has(id)) || !Array.isArray(draft.files) || !draft.files.every(row => isObject(row) && validSource(row.file) && (validLocalFile(row.file.localFile) || validDemoReply(row as unknown as ReplyFile))
      && typeof row.replaces === 'string' && typeof row.checked === 'boolean' && Array.isArray(row.requests) && row.requests.every(id => typeof id === 'string' && ids.has(id))))) return false
  return value.history.every(row => isObject(row) && typeof row.id === 'string' && typeof row.at === 'string' && strings(row.answers)
    && Object.keys(row.answers).every(id => ids.has(id)) && validDecisionInput(row.previous) && isObject(row.judgments) && Object.values(row.judgments).every(validJudgment))
}
export function replyIssues(input: DecisionInput, state: FollowupState) {
  const draft = state.draft
  if (!draft) return ['尚未开始登记回件。']
  const issues: string[] = []
  const requests = new Set(state.requests.map(row => row.id))
  const answered = Object.entries(draft.answers).filter(([, text]) => text.trim())
  if (!draft.source.trim()) issues.push('记录这批回件的来源。')
  if (!answered.length) issues.push('至少记录一条答复；没有文字说明时可如实记录“仅收到文件，未附说明”。')
  if (answered.some(([id]) => !requests.has(id))) issues.push('答复对应的请求已不在当前范围。')
  const replaced = draft.files.filter(row => row.replaces !== 'additional').map(row => row.replaces)
  if (new Set(replaced).size !== replaced.length) issues.push('同一旧文件不能被本批两份文件同时替代。')
  for (const row of draft.files) {
    if (row.demo && (!validDemoReply(row) || row.file.revision !== 'B')) issues.push(`${row.file.name}：示例原件为 Rev B，修改登记值不能改变原件内容。`)
    if (!row.file.revision.trim() || !row.checked || !row.requests.length || row.requests.some(id => !requests.has(id))) issues.push(`${row.file.name}：核对修订与对应要求。`)
    if (row.replaces !== 'additional' && !input.files.some(file => sourceKey(file) === row.replaces)) issues.push(`${row.file.name}：选择补充依据或要替代的旧文件。`)
    if (row.requests.some(id => !draft.answers[id]?.trim())) issues.push(`${row.file.name}：记录关联请求的答复。`)
  }
  return issues
}
// A single parent state update owns new input, history, and invalidation of old judgments.
export function receiveReply(input: DecisionInput, state: FollowupState, judgments: Record<string, Judgment>) {
  if (replyIssues(input, state).length || !state.draft) return null
  const draft = state.draft
  const files = draft.files.map(row => ({...row.file, pending: false,
    sourceVersion: row.replaces === 'additional' ? 1 : (input.files.find(file => sourceKey(file) === row.replaces)!.sourceVersion + 1)}))
  const replaced = new Set(draft.files.map(row => row.replaces))
  const notes = state.requests.flatMap(request => draft.answers[request.id]?.trim()
    ? [`${requestLabel(request, state)} · ${request.clauseId} · ${request.title}${currentRequest(request, state) ? '' : '（旧版或已撤销请求）'}\n请求：${request.question}\n供应商答复：${draft.answers[request.id].trim()}\n关联文件：${draft.files.filter(row => row.requests.includes(request.id)).map(row => row.file.name).join('、') || '无新文件'}`] : []).join('\n\n')
  const next: DecisionInput = {...input, sample: undefined, receiptId: draft.id,
    files: [...input.files.filter(file => !replaced.has(sourceKey(file))), ...files],
    evidence: input.evidence.map(row => row.result === '不适用' ? row : {...row, result: '未覆盖', explanation: '本批回件尚无机器检查结果；请结合新旧原件重新记录人工判断。', sources: []}),
    receipts: [...(input.receipts ?? [{id: input.receiptId, source: '初次资料', files: input.files, notes: input.clarification ?? ''}]), {id: draft.id, source: draft.source.trim(), files, notes}]}
  const documents = mergeDocuments(state.documents ?? [], draft.files.flatMap((row, index) => row.demo && validDemoReply(row) ? [demoDocument(files[index], row.demo, draft.id)] : []))
  return {input: next, state: {...state, documents, draft: null, history: [...state.history,
    {id: draft.id, at: new Date().toISOString(), answers: structuredClone(draft.answers), previous: structuredClone(input), judgments: structuredClone(judgments)}]}}
}
export function requestStatus(request: FollowupRequest, input: DecisionInput, judgments: Record<string, Judgment>, state: FollowupState) {
  if (request.withdrawn) return '已撤销'
  if (state.requests.some(row => row.replaces === request.id)) return '已有新版本'
  const row = input.evidence.find(item => item.id === request.clauseId)
  if (row && requirementSatisfied(input, row, judgments[row.id])) return '已复核满足'
  if (row && judgmentComplete(input, row, judgments[row.id])) return judgments[row.id].value === 'unknown' ? '复核仍无法判断' : '复核未满足，需继续补正'
  return '尚未确认满足'
}

export const currentRequest = (request: FollowupRequest, state: FollowupState) => !request.withdrawn && !state.requests.some(row => row.replaces === request.id)
export function requestLabel(request: FollowupRequest, state: FollowupState) {
  let root = request, version = 1
  while (root.replaces) {const previous = state.requests.find(row => row.id === root.replaces); if (!previous) break; root = previous; version++}
  return `请求 ${state.requests.filter(row => !row.replaces).findIndex(row => row.id === root.id) + 1} · v${version}`
}
// Request edits do not alter the clause, evidence, or human judgment. Each reply
// remains keyed by its recorded request identity, including superseded versions.
export function saveRequest(input: DecisionInput, state: FollowupState): FollowupState | null {
  const edit = state.composer
  if (!edit || state.draft) return null
  const clause = input.evidence.find(row => row.id === edit.clauseId && row.result !== '不适用')
  const original = state.requests.find(row => row.id === edit.requestId)
  const action = edit.action ?? 'append'
  if (!clause || action !== 'withdraw' && !edit.text.trim()) return null
  if (action !== 'append' && (!original || !currentRequest(original, state) || !edit.reason?.trim() || original.clauseId !== clause.id)) return null
  if (action === 'revise' && edit.text.trim() === original?.question) return null
  if (action === 'withdraw') return {...state, composer: null, requests: state.requests.map(row => row.id === original!.id ? {...row, withdrawn: {at: new Date().toISOString(), reason: edit.reason!.trim()}} : row)}
  const request: FollowupRequest = {id: crypto.randomUUID(), clauseId: clause.id, title: clause.title, requirement: original?.requirement ?? clause.requirement, question: edit.text.trim(), at: new Date().toISOString(),
    ...(action === 'revise' ? {replaces: original!.id, reason: edit.reason!.trim()} : {})}
  return {...state, composer: null, requests: [...state.requests, request]}
}

function RequestEditor({input, state, disabled, onChange}: {input: DecisionInput; state: FollowupState; disabled: boolean; onChange: (state: FollowupState) => void}) {
  const edit = state.composer, action = edit?.action ?? 'append'
  const original = state.requests.find(row => row.id === edit?.requestId)
  const clause = input.evidence.find(row => row.id === edit?.clauseId && row.result !== '不适用')
  const readOnly = disabled || Boolean(state.draft)
  const patch = (value: Partial<RequestComposer>) => !readOnly && edit && onChange({...state, composer: {...edit, ...value}})
  const close = () => onChange({...state, composer: null})
  const canSave = !disabled && !state.draft && clause && (action === 'withdraw' || edit?.text.trim())
    && (action === 'append' || original && currentRequest(original, state) && edit?.reason?.trim())
    && (action !== 'revise' || original?.question !== edit?.text.trim())
  return <Dialog open={Boolean(edit)} onOpenChange={open => {if (!open) close()}}><DialogContent className="gf-request-editor"><DialogHeader><DialogTitle>{action === 'withdraw' ? '撤销补正请求' : action === 'revise' ? '修订补正请求' : '追加补正请求'}</DialogTitle><DialogDescription>{action === 'withdraw' ? '停止跟进这条请求；不会删除历史，也不会将审核条款标为满足。' : '记录后可下载并通过现有渠道联系供应商，不会自动发送。'}</DialogDescription></DialogHeader>
    {original ? <section><strong>{requestLabel(original, state)} · {original.title}</strong><p className="gf-request-original">{original.question}</p></section> : <label>对应要求<Select value={edit?.clauseId ?? ''} disabled={readOnly} onValueChange={clauseId => patch({clauseId})}><SelectTrigger aria-label="请求对应要求"><SelectValue placeholder="选择需要补正的要求"/></SelectTrigger><SelectContent>{input.evidence.filter(row => row.result !== '不适用').map(row => <SelectItem key={row.id} value={row.id}>{row.id} · {row.title}</SelectItem>)}</SelectContent></Select></label>}
    {clause && <details><summary>要求原文（不随请求修订）</summary><p>{clause.requirement}</p></details>}
    {action !== 'withdraw' && <label>{action === 'revise' ? '新版请求内容' : '请求内容'}<Textarea aria-label="补正请求内容" value={edit?.text ?? ''} disabled={readOnly} onChange={event => patch({text: event.target.value})}/></label>}
    {action !== 'append' && <label>{action === 'withdraw' ? '撤销原因' : '修订原因'}<Textarea aria-label="请求变更原因" value={edit?.reason ?? ''} disabled={readOnly} onChange={event => patch({reason: event.target.value})}/></label>}
    <div className="gf-follow-actions"><Button variant="outline" onClick={close}>取消</Button><Button disabled={!canSave} onClick={() => {if (!canSave) return; const next = saveRequest(input, state); if (next) onChange(next)}}>{action === 'withdraw' ? '确认撤销请求' : action === 'revise' ? '保存新版本' : '记录请求'}</Button></div>
  </DialogContent></Dialog>
}

export function FollowupWorkspace({input, state, judgments, disabled, documents, onChange, onReceive, onClose}: {
  input: DecisionInput; state: FollowupState; judgments: Record<string, Judgment>; disabled: boolean; documents: DemoDocument[]
  onChange: (state: FollowupState) => void; onReceive: () => void; onClose: () => void
}) {
  const picker = useRef<HTMLInputElement>(null)
  const answerRef = useRef<HTMLElement>(null)
  const [layout, setLayout] = useState(() => Math.max(0, ['requests', 'desk', 'steps'].indexOf(new URLSearchParams(location.search).get('followupLayout') ?? 'desk')))
  const draft = state.draft
  const active = state.requests.filter(row => currentRequest(row, state))
  const archived = state.requests.filter(row => !currentRequest(row, state))
  const editDisabled = disabled || Boolean(draft)
  const label = (request: FollowupRequest) => `${requestLabel(request, state)} · ${request.clauseId}`
  const upload = useLocalUploads(rows => {
    if (!draft || disabled) return
    onChange({...state, draft: {...draft, step: 1, files: [...draft.files, ...rows.map(({file, ref}) => ({file: {name: file.name, revision: '', sourceVersion: 0, pending: true, localFile: ref}, replaces: '', requests: [], checked: false}))]}})
  }, draft?.id ?? 'no-reply')
  const changeDraft = (patch: Partial<ReplyDraft>) => draft && onChange({...state, draft: {...draft, ...patch}})
  const changeFile = (index: number, patch: Partial<ReplyFile>) => draft && changeDraft({files: draft.files.map((row, i) => i === index ? {...row, ...patch, checked: false} : row)})
  const issues = replyIssues(input, state)
  const changeLayout = (next: number) => {setLayout(next); const url = new URL(location.href); url.searchParams.set('followupLayout', ['requests', 'desk', 'steps'][next]); history.replaceState(null, '', url)}
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement)?.closest('input,textarea,select,button,[role="dialog"],[contenteditable]')) return
      changeLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key)
  }, [layout])
  const start = () => onChange({...state, draft: {id: `REPLY-${crypto.randomUUID()}`, source: '', files: [], answers: {}, step: 0}})
  function downloadRequests() {
    const text = [input.scope, `要求：${input.basis?.source.title} · ${input.basis?.source.version}`, '当前补正请求（尚未由系统发送）', ...active.map(row => `${label(row)} · ${row.title}\n要求：${row.requirement}\n请求：${row.question}\n记录：${row.at}${row.reason ? `\n修订原因：${row.reason}` : ''}`)].join('\n\n')
    const url = URL.createObjectURL(new Blob([text], {type: 'text/plain;charset=utf-8'})), link = document.createElement('a')
    link.href = url; link.download = `${input.receiptId}-补正请求.txt`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const requestRow = (request: FollowupRequest) => {
    const replies = state.history.flatMap((receipt, index) => receipt.answers[request.id]?.trim() ? [{text: receipt.answers[request.id], batch: index + 1}] : [])
    const latest = replies.at(-1), current = currentRequest(request, state)
    return <article key={request.id}><header><h3>{label(request)} · {request.title}</h3><span className="gf-status gf-status--wait">{current ? latest ? '已收到答复' : '等待答复' : requestStatus(request, input, judgments, state)}</span></header>
      <p className="gf-request-question">{request.question}</p>
      {current && <p>条款复核：{requestStatus(request, input, judgments, state)}</p>}
      {latest && <blockquote><strong>第 {latest.batch} 批答复</strong><p>{latest.text}</p></blockquote>}
      {request.reason && <p>修订原因：{request.reason}</p>}{request.withdrawn && <p>撤销原因：{request.withdrawn.reason}</p>}
      <details><summary>要求与记录</summary><p>{request.requirement}</p><p>{new Date(request.at).toLocaleString('zh-CN')}</p>{replies.slice(0, -1).map(reply => <p key={reply.batch}>第 {reply.batch} 批答复：{reply.text}</p>)}</details>
      {current && <div className="gf-follow-actions gf-request-actions"><Button variant="ghost" disabled={editDisabled} aria-label={`修订 ${label(request)}`} onClick={() => onChange({...state, composer: {action: 'revise', requestId: request.id, clauseId: request.clauseId, text: request.question, reason: ''}})}>修订请求</Button><Button variant="ghost" disabled={editDisabled} aria-label={`撤销 ${label(request)}`} onClick={() => onChange({...state, composer: {action: 'withdraw', requestId: request.id, clauseId: request.clauseId, text: request.question, reason: ''}})}>撤销请求</Button></div>}
    </article>
  }
  const requests = <section aria-label="补正请求"><div className="gf-follow-actions"><h2>当前请求 · {active.length}</h2><Button variant="outline" disabled={editDisabled} onClick={() => onChange({...state, composer: {clauseId: '', text: '', action: 'append'}})}><Plus/>追加请求</Button></div>
    {draft && <p>本批回件登记中；完成或放弃登记后可修订请求。</p>}{!active.length && <p>没有正在跟进的请求。条款是否满足，仍以人工复核为准。</p>}{active.map(requestRow)}
    {archived.length > 0 && <details className="gf-request-history"><summary>旧版与已撤销请求 · {archived.length}</summary>{archived.map(requestRow)}</details>}
    <Button variant="outline" disabled={!active.length} onClick={downloadRequests}><Download/>下载当前请求</Button><p>通过现有渠道联系供应商；下载不代表已发送。</p></section>
  const uploadPanel = <section aria-label="上传回件"><h2>收到什么</h2><p>先上传这次收到的文件，再核对版本与用途。没有新文件时可只记录答复。</p>
    <input ref={picker} className="sr-only" type="file" multiple accept={fileAccept} aria-label="上传补正文件" disabled={disabled || upload.busy || !draft} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    <Button variant="outline" disabled={disabled || upload.busy || !draft} onClick={() => picker.current?.click()}><Upload/>{upload.busy ? '正在保存原件…' : draft?.files.length ? '继续上传文件' : '选择回件文件'}</Button>
    {upload.busy && <Button variant="ghost" onClick={upload.cancel}>停止添加</Button>}{upload.error && <p role="alert">{upload.error}</p>}
    {import.meta.env.DEV && <details><summary>演示资料</summary><p>仅用于 P-204 故事。选择后仍须核对用途、关联请求并登记答复。</p><div className="gf-follow-actions">{demoReplies.map(item => <Button key={item.flavor} variant="outline" disabled={disabled || upload.busy || !draft || draft.files.some(row => row.demo === item.flavor)} onClick={() => draft && changeDraft({step: 1, files: [...draft.files, {demo: item.flavor, file: {name: item.name, revision: 'B', sourceVersion: 0, pending: true}, replaces: '', requests: [], checked: false}]})}>{item.label}</Button>)}</div></details>}
    {!draft?.files.length && <Button variant="ghost" disabled={disabled || upload.busy || !draft} onClick={() => {changeDraft({step: 2}); window.requestAnimationFrame(() => answerRef.current?.scrollIntoView({block: 'start'}))}}>只有答复，没有文件</Button>}
    {draft && draft.files.length > 0 && <p>{draft.files.length} 份文件 · {draft.files.filter(row => row.checked).length} 份已核对</p>}
  </section>
  const verifyPanel = <section aria-label="核对回件"><h2>核对文件</h2>{!draft?.files.length && <p>本批没有新文件。</p>}{draft?.files.map((row, index) => {
    const reading = {...input, files: [row.file], evidence: []}
    return <article key={row.file.localFile?.id ?? row.demo}><header><h3>{row.file.name}</h3><div className="gf-follow-actions"><SourceReaderButton input={reading} documents={row.demo && validDemoReply(row) && row.file.revision === 'B' ? [demoDocument(row.file, row.demo, '待登记示例回件')] : []} file={row.file} label="阅读原件"/><Button size="icon" variant="ghost" aria-label={`移除回件 ${index + 1}`} disabled={disabled || upload.busy} onClick={() => changeDraft({files: draft.files.filter((_, i) => i !== index)})}><X/></Button></div></header>
      <div className="gf-follow-fields"><label>文件修订<Input aria-label={`回件 ${index + 1} 修订`} value={row.file.revision} disabled={disabled} onChange={event => changeFile(index, {file: {...row.file, revision: event.target.value}})}/></label>
      <label>本次用途<Select value={row.replaces} disabled={disabled} onValueChange={replaces => changeFile(index, {replaces})}><SelectTrigger aria-label={`回件 ${index + 1} 用途`}><SelectValue placeholder="选择补充或替代"/></SelectTrigger><SelectContent><SelectItem value="additional">补充依据，保留旧文件</SelectItem>{input.files.map(file => <SelectItem key={sourceKey(file)} value={sourceKey(file)}>替代 {file.name} · Rev {file.revision} · v{file.sourceVersion}</SelectItem>)}</SelectContent></Select></label></div>
      <fieldset disabled={disabled}><legend>对应请求</legend>{state.requests.map(request => <label key={request.id}><Checkbox checked={row.requests.includes(request.id)} aria-label={`回件 ${index + 1} 对应 ${label(request)}`} onCheckedChange={checked => changeFile(index, {requests: checked === true ? [...row.requests, request.id] : row.requests.filter(id => id !== request.id)})}/><span>{label(request)}{currentRequest(request, state) ? '' : '（旧版或已撤销）'}<small className="gf-request-target">{request.question}</small></span></label>)}</fieldset>
      <label className="gf-follow-check"><Checkbox checked={row.checked} disabled={disabled || !row.file.revision.trim() || !row.replaces || !row.requests.length} aria-label={`回件 ${index + 1} 已核对`} onCheckedChange={checked => changeDraft({files: draft.files.map((file, i) => i === index ? {...file, checked: checked === true} : file)})}/>已核对原件归属、修订和用途</label>
    </article>
  })}</section>
  const answerField = (request: FollowupRequest) => <label key={request.id}>{label(request)} · {request.title}<small className="gf-request-target">{request.question}</small><Textarea aria-label={`${label(request)} 供应商答复`} value={draft?.answers[request.id] ?? ''} disabled={disabled} placeholder="记录原话；未答复的项目留空" onChange={event => draft && changeDraft({answers: {...draft.answers, [request.id]: event.target.value}})}/></label>
  const answerPanel = <section ref={answerRef} aria-label="登记供应商答复"><h2>供应商如何答复</h2><label>回件来源<Input value={draft?.source ?? ''} disabled={disabled} onChange={event => changeDraft({source: event.target.value})} placeholder="如：10 月 14 日邮件，质量部王工"/></label>
    {active.map(answerField)}
    {archived.length > 0 && <details><summary>登记旧版或已撤销请求的晚到答复 · {archived.length}</summary><p>答复保留在所选请求下，不会自动计入新版请求。</p>{archived.map(answerField)}</details>}
    <p>{Object.values(draft?.answers ?? {}).filter(text => text.trim()).length} 项本批有答复，未答复项目继续保留。</p>
    <p>登记后重新核对全部条款，防止新资料影响跨文件判断；旧结论保留在历史中。</p>
    {issues.length > 0 && <ul className="gf-follow-issues">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
    <Button disabled={disabled || upload.busy || issues.length > 0} onClick={onReceive}>登记回件并复审<ArrowRight/></Button>
  </section>
  return <section className="gf-follow-page" aria-label="补正与回件"><Button variant="ghost" onClick={onClose}><ArrowLeft/>返回复核</Button><header className="gf-page-heading"><div><h1>补正与回件</h1><p>{input.scope}</p></div></header><p>收到回件不代表问题解决。原件、供应商答复和人工判断分别保留。</p>
    {!draft ? <>{requests}<Button disabled={disabled || !state.requests.length} onClick={start}>登记收到的回件<ArrowRight/></Button></> : <>
      <div className={`gf-follow-body gf-follow-body--${layout}`}>
        {requests}<div className="gf-follow-receipt">
          {layout === 2 && <nav aria-label="回件步骤">{['上传', '核对', '答复'].map((label, index) => <Button variant={draft.step === index ? 'secondary' : 'ghost'} aria-current={draft.step === index ? 'step' : undefined} key={label} onClick={() => changeDraft({step: index})}>{index + 1} · {label}</Button>)}</nav>}
          {layout === 2 ? [uploadPanel, verifyPanel, answerPanel][draft.step] : <>{uploadPanel}{verifyPanel}{answerPanel}</>}
          {layout === 2 && draft.step < 2 && <Button variant="outline" onClick={() => changeDraft({step: draft.step + 1})}>继续：{draft.step === 0 ? '核对文件' : '记录答复'}<ArrowRight/></Button>}
        </div>
      </div><Button variant="ghost" disabled={disabled || upload.busy} onClick={() => onChange({...state, draft: null})}>放弃本批登记</Button>
    </>}
    {state.history.length > 0 && <section className="gf-follow-history"><h2>之前的判断与答复</h2>{state.history.map((receipt, index) => <details key={receipt.id}><summary>第 {index + 1} 批回件 · {new Date(receipt.at).toLocaleString('zh-CN')}</summary>
      {state.requests.filter(request => receipt.answers[request.id]?.trim()).map(request => <div key={request.id}><strong>{label(request)} · {request.title}</strong><p>当时请求：{request.question}</p><p>供应商答复：{receipt.answers[request.id]}</p></div>)}
      <details><summary>登记前的全部判断与原件</summary>{receipt.previous.evidence.map(row => <article key={row.id}><h3>{row.id} · {row.title}</h3><p>当时机器结果：{row.result}</p><JudgmentEditor input={receipt.previous} row={row} judgment={receipt.judgments[row.id] ?? {value: 'pending', reason: ''}} frozen documents={documents} onChange={() => {}}/></article>)}</details>
    </details>)}</section>}
    {import.meta.env.DEV && <div className="gf-switcher" role="toolbar" aria-label="补正布局"><button aria-label="上一种补正布局" onClick={() => changeLayout((layout + 2) % 3)}><ArrowLeft size={16}/></button><span>{layout + 1} / 3 · {['请求优先', '回件工作台', '分步登记'][layout]}</span><button aria-label="下一种补正布局" onClick={() => changeLayout((layout + 1) % 3)}><ArrowRight size={16}/></button></div>}
    <RequestEditor input={input} state={state} disabled={editDisabled} onChange={onChange}/>
  </section>
}
