// Throwaway internal receipt prototype. Three layouts share one in-memory reply snapshot.
import {useState} from 'react'
import {ArrowLeft, ArrowRight, FileText, Send} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {SourceReaderButton} from './source-reader'
import {demoDocument, type DemoFlavor} from './source-documents'
import './amendment.css'
import {fileAccept, useLocalUploads, type LocalFileRef} from './local-files'

export type RequestKey = 'conflict' | 'scan' | 'report'
export type ReplyMode = 'explain' | 'replace' | 'dispute'
export type Reply = {mode: ReplyMode | null; note: string; evidence: boolean; fileId: string | null; conflictFile: 'control' | 'flow' | null}
export type ReplyMap = Record<RequestKey, Reply>
export type ReceivedFile = {id: string; name: string; kind: '待分类' | '控制计划' | '过程流程图' | '尺寸报告'; revision: string; associated: boolean; checked: boolean; source: 'demo' | 'local'; localFile?: LocalFileRef; demoFact?: string; demoKey?: 'control' | 'flow' | 'scan' | 'report' | 'outlier'}
export type ReceiptStep = 'upload' | 'verify' | 'reply'
export type Submission = {id: string; source: string; files: ReceivedFile[]; replies: Partial<Record<RequestKey, Reply>>}
export type AmendmentVariant = 'tasks' | 'files' | 'guided'
export type ConflictReview = 'pending' | 'file-consistent' | 'needs-clarification'

export const amendmentVariants: {id: AmendmentVariant; label: string}[] = [
  {id: 'tasks', label: '请求优先'},
  {id: 'files', label: '原件优先'},
  {id: 'guided', label: '逐项引导'},
]
export const initialReplies: ReplyMap = {
  conflict: {mode: null, note: '', evidence: false, fileId: null, conflictFile: null},
  scan: {mode: null, note: '', evidence: false, fileId: null, conflictFile: null},
  report: {mode: null, note: '', evidence: false, fileId: null, conflictFile: null},
}
const conflictFiles = {
  control: {name: '控制计划_B_修正.xlsx', fact: '工序 17 改为“热处理”；过程流程图仍写“热处理”。'},
  flow: {name: '过程流程图_B_修正.pdf', fact: '工序 17 改为“表面处理”；控制计划仍写“表面处理”。'},
}
export const demoReceivedFiles: ReceivedFile[] = [
  {id: 'demo-control', name: conflictFiles.control.name, kind: '控制计划', revision: 'B', associated: true, checked: false, source: 'demo', demoKey: 'control', demoFact: conflictFiles.control.fact},
  {id: 'demo-flow', name: conflictFiles.flow.name, kind: '过程流程图', revision: 'B', associated: true, checked: false, source: 'demo', demoKey: 'flow', demoFact: conflictFiles.flow.fact},
  {id: 'demo-scan', name: '过程流程图_B_清晰版.pdf', kind: '过程流程图', revision: 'B', associated: true, checked: false, source: 'demo', demoKey: 'scan', demoFact: '第 3 页：16 机加工、17 热处理、18 表面检查，工序号清晰可读。'},
  {id: 'demo-report', name: '尺寸报告_B.xlsx', kind: '尺寸报告', revision: 'B', associated: true, checked: false, source: 'demo', demoKey: 'report', demoFact: 'Rev B；孔径实测 10.02、10.04、9.98 mm，限值 9.90–10.10 mm。'},
  {id: 'demo-outlier', name: '尺寸报告_B_超差.xlsx', kind: '尺寸报告', revision: 'B', associated: true, checked: false, source: 'demo', demoKey: 'outlier', demoFact: 'Rev B；孔径实测 10.02、10.14、9.98 mm，第 2 件超过 10.10 mm。'},
]
export function isIntactDemoFile(file: ReceivedFile | undefined): boolean {
  if (!file || file.source !== 'demo') return false
  const expected = demoReceivedFiles.find(item => item.id === file.id)
  return Boolean(expected && file.checked && file.associated && file.name === expected.name && file.kind === expected.kind && file.revision === expected.revision && file.demoKey === expected.demoKey)
}
const rows: {id: RequestKey; title: string; original: string; issue: string; replacement: string; replacementFact: string}[] = [
  {id: 'conflict', title: '工序 17 描述冲突', original: '控制计划_B.xlsx · 过程流程图_B.pdf', issue: '同一工序在两份原件中分别写为“表面处理”和“热处理”。', replacement: conflictFiles.control.name, replacementFact: conflictFiles.control.fact},
  {id: 'scan', title: '扫描件工序号不清', original: '过程流程图_B.pdf · 第 3 页', issue: '另有一处工序号无法可靠辨认，尚无业务结论。', replacement: '过程流程图_B_清晰版.pdf', replacementFact: '模拟新原件的对应位置可读，仍需核对工序内容。'},
  {id: 'report', title: '尺寸报告未覆盖 Rev B', original: '尺寸报告_A.xlsx · Rev A', issue: '现有报告不对应本轮图纸修订，不能据此判断尺寸合格。', replacement: '尺寸报告_B.xlsx', replacementFact: '模拟新原件标为 Rev B，实测内容仍需审核。'},
]
const modeLabels: {id: ReplyMode; label: string}[] = [
  {id: 'explain', label: '收到说明'},
  {id: 'replace', label: '收到新原件'},
  {id: 'dispute', label: '收到异议'},
]

export function replyReady(reply: Reply) {
  return reply.mode !== null && reply.note.trim().length > 0 && (reply.mode !== 'replace' || reply.evidence && Boolean(reply.fileId))
}
export function fileMatchesRequest(file: ReceivedFile, id: RequestKey): boolean {
  if (!file.checked || !file.associated || !file.revision.trim() || file.kind === '待分类') return false
  const kindMatches = id === 'conflict' ? file.kind === '控制计划' || file.kind === '过程流程图' : id === 'scan' ? file.kind === '过程流程图' : file.kind === '尺寸报告'
  if (!kindMatches) return false
  return true
}

function ReplyEditor({id, request, value, files, onChange}: {id: RequestKey; request: string; value: Reply; files: ReceivedFile[]; onChange: (reply: Reply) => void}) {
  const row = rows.find(item => item.id === id)!
  const candidateFiles = files.filter(file => fileMatchesRequest(file, id))
  return <section className="gf-amend-editor" aria-label={`${row.title}答复`}>
    <div className="gf-amend-editor-top"><span className="gf-amend-number">{rows.findIndex(item => item.id === id) + 1}</span><div><h2>{row.title}</h2><p>{row.original}</p></div><span className={`gf-amend-state ${replyReady(value) ? 'gf-amend-state--ready' : ''}`}>{replyReady(value) ? '可登记' : '未收到'}</span></div>
    <div className="gf-amend-source"><span>已记录的对外请求</span><p>{request}</p><small>{row.issue}</small></div>
    <div className="gf-amend-response"><span>收到的答复类型</span><div className="gf-amend-modes" role="group" aria-label={`${row.title}的答复类型`}>{modeLabels.map(mode => <button type="button" key={mode.id} aria-pressed={value.mode === mode.id} onClick={() => onChange({...value, mode: mode.id, evidence: mode.id === 'replace' ? value.evidence : false, fileId: mode.id === 'replace' ? value.fileId : null, conflictFile: mode.id === 'replace' ? value.conflictFile : null})}>{mode.label}</button>)}</div></div>
    {value.mode === 'replace' && <label className="gf-amend-file-choice"><span>关联已核对的文件</span><select aria-label={`${row.title}关联补件`} value={value.fileId ?? ''} onChange={event => {const file = files.find(item => item.id === event.target.value); onChange({...value, fileId: file?.id ?? null, evidence: Boolean(file), conflictFile: file?.demoKey === 'control' || file?.demoKey === 'flow' ? file.demoKey : null})}}><option value="">选择文件</option>{candidateFiles.map(file => <option key={file.id} value={file.id}>{file.name} · {file.source === 'demo' ? '模拟' : '内容未解析'}</option>)}</select><small>没有合适文件时，返回文件选择；关联不等于问题已解决。</small></label>}
    {value.mode && <label className="gf-amend-note"><span>{value.mode === 'dispute' ? '供应商异议原话' : '供应商说明原话'}</span><Textarea aria-label={`${row.title}的供应商说明`} placeholder={value.mode === 'replace' ? '记录供应商称修改了什么；随后对照新原件核实' : value.mode === 'dispute' ? '记录异议和可核对依据' : '记录收到的说明；说明不能改写原件'} value={value.note} onChange={event => onChange({...value, note: event.target.value})}/></label>}
  </section>
}

export function SupplierAmendment({selected, requests, replies, source, files, step, onStep, onFilesChange, onSourceChange, onChange, onSubmit, variant, batchNumber = 1}: {
  batchNumber?: number
  selected: Record<RequestKey, boolean>
  requests: Record<RequestKey, string>
  replies: ReplyMap
  source: string
  files: ReceivedFile[]
  step: ReceiptStep
  onStep: (step: ReceiptStep) => void
  onFilesChange: (files: ReceivedFile[]) => void
  onSourceChange: (source: string) => void
  onChange: (key: RequestKey, reply: Reply) => void
  onSubmit: () => void
  variant: AmendmentVariant
}) {
  const keys = rows.filter(row => selected[row.id]).map(row => row.id)
  const [focused, setFocused] = useState<RequestKey>('conflict')
  const upload = useLocalUploads(incoming => onFilesChange([...files, ...incoming.map(({file, ref}) => ({id: ref.id, localFile: ref, name: file.name, kind: '待分类' as const, revision: '', associated: false, checked: false, source: 'local' as const}))]), `batch:${batchNumber}`)
  const sources = files.map(file => {
    const fixture = file.source === 'demo' ? demoReceivedFiles.find(item => item.id === file.id && item.name === file.name && item.demoKey === file.demoKey) : undefined
    const ref = {name: file.name, revision: fixture?.revision ?? '未读取', sourceVersion: 0, pending: true, localFile: file.localFile}
    const flavors: Record<string, DemoFlavor> = {control: 'control-clear', flow: 'flow-changed', scan: 'flow-clear', report: 'dimension-clear', outlier: 'dimension-outlier'}
    return {id: file.id, ref, doc: fixture?.demoKey ? demoDocument(ref, flavors[fixture.demoKey], `第 ${batchNumber} 批候选资料 · 尚未登记`) : null}
  })
  const readingInput = {receiptId: `第 ${batchNumber} 批 · 待登记`, scope: '支架 P-204 · 图纸 Rev B', files: sources.map(item => item.ref), evidence: [], clarification: null}
  const documents = sources.flatMap(item => item.doc ? [item.doc] : [])
  const active = keys.includes(focused) ? focused : keys[0]
  const ready = keys.filter(key => replyReady(replies[key]) && (replies[key].mode !== 'replace' || files.some(file => file.id === replies[key].fileId && fileMatchesRequest(file, key))))
  const remaining = keys.length - ready.length
  const editor = (key: RequestKey) => <ReplyEditor key={key} id={key} request={requests[key]} value={replies[key]} files={files} onChange={reply => onChange(key, reply)}/>
  const addFiles = (list: FileList | null) => {
    if (!list?.length) return
    void upload.upload(Array.from(list))
  }
  const updateFile = (id: string, change: Partial<ReceivedFile>) => onFilesChange(files.map(file => file.id === id ? {...file, ...change, checked: false} : file))
  const submit = <div className="gf-amend-submit"><div><strong>本次收到 {ready.length} 项答复</strong><span>{remaining ? `${remaining} 项尚未收到，可留待下次登记` : '请求均有答复；答复不等于问题解决'}</span></div><Button className="gf-primary" disabled={!ready.length || !source.trim()} onClick={onSubmit}>模拟登记回件 <Send size={16}/></Button></div>
  const heading = <header className="gf-page-heading"><div><div className="gf-eyebrow">审核团队 · 外部回件</div><h1>{step === 'upload' ? '选择补件文件' : step === 'verify' ? '核对补件信息' : '登记供应商答复'}</h1><p className="gf-case-meta">明川精密 <span>·</span> 支架 P-204 <span>·</span> 图纸 Rev B</p></div><span className="gf-round">第 {batchNumber} 批补件</span></header>
  const steps = <nav className="gf-amend-steps" aria-label="回件登记步骤">{([['upload', '选择文件'], ['verify', '核对'], ['reply', '登记答复']] as const).map(([id, label], index) => <span key={id} aria-current={step === id ? 'step' : undefined}><b>{String(index + 1).padStart(2, '0')}</b>{label}</span>)}</nav>

  if (step === 'upload') return <div className="gf-amend-page">
    {heading}{steps}
    <p className="gf-amend-intro">先选择本次收到的新原件。原件保存在本机，随后核对归属与修订。</p>
    <section className="gf-amend-upload" aria-label="选择补件文件"><div className="gf-amend-upload-head"><div><h2>本次新原件</h2><p>支持 Excel、Word、PDF、图片和文本；稍后逐份核对类型、修订和事项归属。</p></div><label>选择文件<input type="file" accept={fileAccept} disabled={upload.busy} multiple onChange={event => {addFiles(event.target.files); event.target.value = ''}}/></label></div>
      <details className="gf-amend-demo-options"><summary>选择演示补件</summary><p>按批次选择；加载不会确认登记信息或解决问题。</p><div>{[{label: '仅补控制计划', keys: ['control']}, {label: '补清晰流程图与尺寸报告', keys: ['scan', 'report']}, {label: '流程图改为表面处理', keys: ['flow']}, {label: '尺寸超差报告', keys: ['outlier']}].map(option => <Button key={option.label} variant="outline" onClick={() => onFilesChange([...files, ...demoReceivedFiles.filter(file => option.keys.includes(file.demoKey!) && !files.some(existing => existing.id === file.id)).map(file => ({...file}))])}>{option.label}</Button>)}</div></details>
      {files.length > 0 && <div className="gf-amend-upload-list gf-amend-upload-list--names">{files.map(file => <article key={file.id}><div><strong>{file.name}</strong><small>{file.source === 'demo' ? '模拟文件' : '本机原件 · 待核对'}</small></div><span className="gf-amend-file-status">{file.checked ? '已核对' : '待核对'}</span><button type="button" aria-label={`移除${file.name}`} onClick={() => onFilesChange(files.filter(item => item.id !== file.id))}>移除</button></article>)}</div>}
      {upload.busy && <p role="status">正在保存本机原件…</p>}{upload.error && <p role="alert" className="gf-amend-file-error">{upload.error}</p>}
    </section>
    <div className="gf-amend-stage-actions"><Button variant="outline" disabled={files.length > 0} onClick={() => onStep('reply')}>没有新文件，仅登记说明或异议</Button><Button className="gf-primary" disabled={!files.length || upload.busy} onClick={() => onStep('verify')}>继续核对 <ArrowRight size={16}/></Button></div>
  </div>

  if (step === 'verify') return <div className="gf-amend-page">
    {heading}{steps}
    <p className="gf-amend-intro">逐份确认文件类型、修订和是否属于本事项。这是人工登记，不代表文件内容已经检查。</p>
    <section className="gf-amend-upload" aria-label="核对补件登记信息"><div className="gf-amend-upload-head"><div><h2>待核对文件</h2><p>{files.filter(file => file.checked).length} / {files.length} 份已核对</p></div></div>
      <div className="gf-amend-upload-list">{files.map(file => <article key={file.id}><div><strong>{file.name}</strong><small>{file.source === 'demo' ? '模拟文件 · 预设事实仅供后续演示' : '本机原件 · 内容未自动识别'}</small><SourceReaderButton input={readingInput} documents={documents} file={sources.find(item => item.id === file.id)?.ref} label="查看资料"/></div><label>类型<select aria-label={`${file.name}的资料类型`} value={file.kind} onChange={event => updateFile(file.id, {kind: event.target.value as ReceivedFile['kind']})}><option>待分类</option><option>控制计划</option><option>过程流程图</option><option>尺寸报告</option></select></label><label>修订<Input aria-label={`${file.name}的修订`} value={file.revision} placeholder="如 B" onChange={event => updateFile(file.id, {revision: event.target.value})}/></label><label className="gf-amend-associated"><input type="checkbox" aria-label={`${file.name}属于本事项`} checked={file.associated} onChange={event => updateFile(file.id, {associated: event.target.checked})}/> 属于本事项</label><button type="button" className="gf-amend-check-file" aria-label={`确认${file.name}的登记信息`} disabled={file.checked || !file.associated || file.kind === '待分类' || !file.revision.trim()} onClick={() => onFilesChange(files.map(item => item.id === file.id ? {...item, checked: true} : item))}>{file.checked ? '已确认' : '确认'}</button></article>)}</div>
      <small>修改任何登记信息后，该文件必须重新确认。关联文件不等于问题解决。</small>
    </section>
    <div className="gf-amend-stage-actions"><Button variant="outline" onClick={() => onStep('upload')}><ArrowLeft size={16}/> 返回选择文件</Button><Button className="gf-primary" disabled={!files.length || files.some(file => !file.checked)} onClick={() => onStep('reply')}>完成核对，登记答复 <ArrowRight size={16}/></Button></div>
  </div>

  return <div className="gf-amend-page">
    {heading}{steps}
    <p className="gf-amend-intro">审核员记录已收到的答复；未答复项保留。新原件不覆盖旧版，也不自动解决问题。</p>
    <label className="gf-amend-receipt-source"><span>回件来源</span><Input aria-label="回件来源" placeholder="例如：供应商邮件，10 月 13 日" value={source} onChange={event => onSourceChange(event.target.value)}/></label>
    <div className="gf-amend-files-summary"><span>{files.length ? `${files.length} 份已核对文件` : '本次无新文件'}</span><button type="button" onClick={() => onStep(files.length ? 'verify' : 'upload')}>{files.length ? '返回文件核对' : '选择补件文件'}</button>{files.length > 0 && <button type="button" onClick={() => onStep('upload')}>补充文件</button>}</div>
    {variant === 'tasks' && <div className="gf-amend-layout gf-amend-layout--tasks"><div className="gf-amend-stack">{keys.map(editor)}</div><aside className="gf-amend-rail"><h2>本次收件</h2>{keys.map(key => {const row = rows.find(item => item.id === key)!; return <div key={key}><span className={replyReady(replies[key]) ? 'gf-amend-dot--ready' : ''}/><strong>{row.title}</strong><small>{replyReady(replies[key]) ? '可登记' : '未收到'}</small></div>})}<p>说明只作为新记录附加；不能替换旧原件。收到新原件后仍须重新检查。</p></aside></div>}
    {variant === 'files' && <div className="gf-amend-layout gf-amend-layout--files"><nav aria-label="按原件选择补正请求"><h2>受影响原件</h2>{keys.map(key => {const row = rows.find(item => item.id === key)!; return <button type="button" key={key} aria-current={active === key ? 'true' : undefined} onClick={() => setFocused(key)}><FileText size={17}/><span><strong>{row.title}</strong><small>{row.original}</small></span><span className={replyReady(replies[key]) ? 'gf-amend-dot--ready' : ''}/></button>})}</nav><div>{active && editor(active)}<p className="gf-amend-quiet">选中左侧其他原件可继续登记；已填写内容会保留在本次草稿中。</p></div></div>}
    {variant === 'guided' && <div className="gf-amend-layout gf-amend-layout--guided"><div className="gf-amend-guide-head"><span>第 {Math.max(1, keys.indexOf(active) + 1)} / {keys.length} 项</span><strong>一次登记一项</strong></div>{active && editor(active)}<div className="gf-amend-guide-nav"><button disabled={keys.indexOf(active) <= 0} onClick={() => setFocused(keys[keys.indexOf(active) - 1])}><ArrowLeft size={16}/> 上一项</button><span>{ready.length} 项可登记</span><button disabled={keys.indexOf(active) >= keys.length - 1} onClick={() => setFocused(keys[keys.indexOf(active) + 1])}>下一项 <ArrowRight size={16}/></button></div></div>}
    {submit}<span className="gf-state-readout" aria-live="polite">模拟回件草稿：来源{source ? '已填' : '未填'}；{keys.map(key => `${key} ${replies[key].mode ?? '未选'} ${replyReady(replies[key]) ? '可登记' : '未收到'}`).join('；')}</span>
  </div>
}
