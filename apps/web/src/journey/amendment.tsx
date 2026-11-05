import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Throwaway internal receipt prototype. Three layouts share one in-memory reply snapshot.
import {useState} from 'react'
import {ArrowLeft, ArrowRight, FileText, Send} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import {SourceReaderButton} from './source-reader'
import {demoDocument, type DemoFlavor} from './source-documents'
import {fileAccept, useLocalUploads, type LocalFileRef} from './local-files'
import {FileList} from './file-collection'
import {FileName} from './file-name'
import {Checkbox} from '../components/ui/checkbox'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'

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
  {id: 'scan', title: '原件工序号不清', original: '过程流程图_B.pdf · 第 3 页', issue: '另有一处工序号无法可靠辨认，尚无业务结论。', replacement: '过程流程图_B_清晰版.pdf', replacementFact: '模拟新原件的对应位置可读，仍需核对工序内容。'},
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
  return <section className={"min-w-0 p-5.5 border rounded-lg bg-background [&_h2]:mt-0.5 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-base [&_h2]:font-bold max-sm:p-4.5"} aria-label={`${row.title}答复`}>
    <div className={"flex items-start gap-3 [&_>_div]:flex-1 [&_>_div]:min-w-0 [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-xs [&_p]:break-all max-sm:flex-wrap"}><span className={"grid flex-[0_0_24px] place-items-center w-6 h-6 rounded-lg bg-muted text-primary text-xs font-bold"}>{rows.findIndex(item => item.id === id) + 1}</span><div><h2>{row.title}</h2><p>{row.original}</p></div><span className={["inline-flex shrink-0 items-center min-h-6 py-1 px-2 rounded-lg bg-muted text-foreground text-xs font-semibold",replyReady(value) ? "bg-muted text-foreground" : "",""].filter(Boolean).join("")}>{replyReady(value) ? '可登记' : '未收到'}</span></div>
    <div className={"my-5 mx-0 py-3.5 px-0 border-t border-b [&_span]:text-muted-foreground [&_span]:text-xs [&_span]:font-semibold [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-1 [&_p]:ml-0 [&_p]:text-sm [&_p]:leading-normal [&_small]:text-muted-foreground [&_small]:text-xs [&_small]:leading-normal"}><span>已记录的对外请求</span><p>{request}</p><small>{row.issue}</small></div>
    <div className={"[&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:font-semibold grid gap-2.5"}><span>收到的答复类型</span><div className={"flex flex-wrap gap-1.5"} role="group" aria-label={`${row.title}的答复类型`}>{modeLabels.map(mode => <Button variant="ghost" type="button" key={mode.id} aria-pressed={value.mode === mode.id} onClick={() => onChange({...value, mode: mode.id, evidence: mode.id === 'replace' ? value.evidence : false, fileId: mode.id === 'replace' ? value.fileId : null, conflictFile: mode.id === 'replace' ? value.conflictFile : null})}>{mode.label}</Button>)}</div></div>
    {value.mode === 'replace' && <label className={"grid gap-1.5 min-w-0 [&_small]:text-muted-foreground [&_small]:text-xs [&_small]:leading-normal text-muted-foreground text-xs font-semibold mt-3.5"}><span>关联已核对的文件</span><NativeSelect aria-label={`${row.title}关联补件`} value={value.fileId ?? ''} onChange={event => {const file = files.find(item => item.id === event.target.value); onChange({...value, fileId: file?.id ?? null, evidence: Boolean(file), conflictFile: file?.demoKey === 'control' || file?.demoKey === 'flow' ? file.demoKey : null})}}><NativeSelectOption value="">选择文件</NativeSelectOption>{candidateFiles.map(file => <NativeSelectOption key={file.id} value={file.id}>{file.name} · {file.source === 'demo' ? '模拟' : '内容未解析'}</NativeSelectOption>)}</NativeSelect><small>没有合适文件时，返回文件选择；关联不等于问题已解决。</small></label>}
    {value.mode && <label className={"[&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:font-semibold grid gap-2 mt-4"}><span>{value.mode === 'dispute' ? '供应商异议原话' : '供应商说明原话'}</span><Textarea aria-label={`${row.title}的供应商说明`} placeholder={value.mode === 'replace' ? '记录供应商称修改了什么；随后对照新原件核实' : value.mode === 'dispute' ? '记录异议和可核对依据' : '记录收到的说明；说明不能改写原件'} value={value.note} onChange={event => onChange({...value, note: event.target.value})}/></label>}
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
  const submit = <div className={"flex items-center justify-between gap-4.5 mt-7.5 py-5.5 px-0 border-t [&_>_div]:grid [&_>_div]:gap-1.5 [&_strong]:text-sm [&_span]:text-muted-foreground [&_span]:text-xs max-sm:items-stretch max-sm:flex-col"}><div><strong>本次收到 {ready.length} 项答复</strong><span>{remaining ? `${remaining} 项尚未收到，可留待下次登记` : '请求均有答复；答复不等于问题解决'}</span></div><Button className={""} disabled={!ready.length || !source.trim()} onClick={onSubmit}>模拟登记回件 <Send size={16}/></Button></div>
  const heading = <header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>审核团队 · 外部回件</div><h1>{step === 'upload' ? '选择补件文件' : step === 'verify' ? '核对补件信息' : '登记供应商答复'}</h1><p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>明川精密 <span>·</span> 支架 P-204 <span>·</span> 图纸 Rev B</p></div><span className={"self-center text-muted-foreground text-sm whitespace-nowrap max-sm:mt-1"}>第 {batchNumber} 批补件</span></header>
  const steps = <nav className={"flex gap-2 mt-1 mr-0 mb-7.5 ml-0 border-b [&_span]:inline-flex [&_span]:items-center [&_span]:gap-2 [&_span]:pt-0 [&_span]:pr-3.5 [&_span]:pb-3 [&_span]:pl-3.5 [&_span]:text-muted-foreground [&_span]:text-sm [&_span]:border-b [&_span:first-child]:pl-0 [&_span[aria-current=\"step\"]]:text-foreground [&_span[aria-current=\"step\"]]:font-semibold [&_b]:text-xs [&_b]:tabular-nums max-sm:gap-0 max-sm:justify-between max-sm:[&_span]:gap-1 max-sm:[&_span]:pt-0 max-sm:[&_span]:pr-1.5 max-sm:[&_span]:pb-2.5 max-sm:[&_span]:pl-1.5 max-sm:[&_span]:text-xs"} aria-label="回件登记步骤">{([['upload', '选择文件'], ['verify', '核对'], ['reply', '登记答复']] as const).map(([id, label], index) => <span key={id} aria-current={step === id ? 'step' : undefined}><b>{String(index + 1).padStart(2, '0')}</b>{label}</span>)}</nav>

  if (step === 'upload') return <div className={"pb-7.5"}>
    {heading}{steps}
    <p className={"-mt-2.5 mr-0 mb-6.5 ml-0 text-muted-foreground text-sm"}>先选择本次收到的新原件。原件保存在本机，随后核对归属与修订。</p>
    <section className={"mt-0 mr-0 mb-7.5 ml-0 py-4.5 px-0 border-t border-b [&_>_small]:m-0 [&_>_small]:text-muted-foreground [&_>_small]:text-xs [&_>_small]:leading-normal"} aria-label="选择补件文件"><div className={"flex items-center justify-between gap-4.5 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-base [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-xs [&_p]:leading-normal [&_label]:inline-flex [&_label]:items-center [&_label]:gap-1.5 [&_label]:min-h-9 [&_label]:py-0 [&_label]:px-3 [&_label]:border [&_label]:rounded-lg [&_label]:bg-background [&_label]:text-xs [&_label]:font-semibold [&_label]:whitespace-nowrap [&_label]:cursor-pointer max-sm:items-start max-sm:flex-col"}><div><h2>本次新原件</h2><p>支持 Excel、Word、PDF、图片和文本；稍后逐份核对类型、修订和事项归属。</p></div><label>选择文件<input type="file" accept={fileAccept} disabled={upload.busy} multiple onChange={event => {addFiles(event.target.files); event.target.value = ''}}/></label></div>
      <Collapsible className={"my-4.5 mx-0"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">选择演示补件</Button></CollapsibleTrigger><CollapsibleContent><div>{[{label: '仅补控制计划', keys: ['control']}, {label: '补清晰流程图与尺寸报告', keys: ['scan', 'report']}, {label: '流程图改为表面处理', keys: ['flow']}, {label: '尺寸超差报告', keys: ['outlier']}].map(option => <Button key={option.label} variant="outline" onClick={() => onFilesChange([...files, ...demoReceivedFiles.filter(file => option.keys.includes(file.demoKey!) && !files.some(existing => existing.id === file.id)).map(file => ({...file}))])}>{option.label}</Button>)}</div></CollapsibleContent></Collapsible>
      {files.length > 0 && <FileList files={files.map(file => ({id: file.id, name: file.name, description: `${file.source === 'demo' ? '模拟文件' : '本机原件'} · ${file.checked ? '已核对' : '待核对'}`, action: <Button variant="ghost" type="button" aria-label={`移除${file.name}`} onClick={() => onFilesChange(files.filter(item => item.id !== file.id))}>移除</Button>}))}/>}
      {upload.busy && <p role="status">正在保存本机原件…</p>}{upload.error && <p role="alert" className={"text-destructive text-xs"}>{upload.error}</p>}
    </section>
    <div className={"flex justify-between items-center flex-wrap gap-3 max-w-212.5 max-sm:items-stretch"}><Button variant="outline" disabled={files.length > 0} onClick={() => onStep('reply')}>没有新文件，仅登记说明或异议</Button><Button className={""} disabled={!files.length || upload.busy} onClick={() => onStep('verify')}>继续核对 <ArrowRight size={16}/></Button></div>
  </div>

  if (step === 'verify') return <div className={"pb-7.5"}>
    {heading}{steps}
    <p className={"-mt-2.5 mr-0 mb-6.5 ml-0 text-muted-foreground text-sm"}>逐份确认文件类型、修订和是否属于本事项。这是人工登记，不代表文件内容已经检查。</p>
    <section className={"mt-0 mr-0 mb-7.5 ml-0 py-4.5 px-0 border-t border-b [&_>_small]:m-0 [&_>_small]:text-muted-foreground [&_>_small]:text-xs [&_>_small]:leading-normal"} aria-label="核对补件登记信息"><div className={"flex items-center justify-between gap-4.5 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-base [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-xs [&_p]:leading-normal [&_label]:inline-flex [&_label]:items-center [&_label]:gap-1.5 [&_label]:min-h-9 [&_label]:py-0 [&_label]:px-3 [&_label]:border [&_label]:rounded-lg [&_label]:bg-background [&_label]:text-xs [&_label]:font-semibold [&_label]:whitespace-nowrap [&_label]:cursor-pointer max-sm:items-start max-sm:flex-col"}><div><h2>待核对文件</h2><p>{files.filter(file => file.checked).length} / {files.length} 份已核对</p></div></div>
      <Table className="table-fixed">
        <TableHeader className="hidden md:table-header-group"><TableRow><TableHead className="w-[40%]">文件</TableHead><TableHead>类型</TableHead><TableHead className="w-24">修订</TableHead><TableHead className="w-28">归属</TableHead><TableHead className="w-20">核对</TableHead></TableRow></TableHeader>
        <TableBody>{files.map(file => <TableRow key={file.id} className="grid grid-cols-2 items-start md:table-row">
          <TableCell className="col-span-2 min-w-0 whitespace-normal"><FileName name={file.name} className="font-medium"/><p className="my-2 text-xs text-muted-foreground">{file.source === 'demo' ? '模拟文件' : '本机原件 · 内容待核对'}</p><SourceReaderButton input={readingInput} documents={documents} file={sources.find(item => item.id === file.id)?.ref} label="查看资料"/></TableCell>
          <TableCell><label className="grid gap-1.5"><span className="text-xs md:sr-only">类型</span><NativeSelect aria-label={`${file.name}的资料类型`} value={file.kind} onChange={event => updateFile(file.id, {kind: event.target.value as ReceivedFile['kind']})}><NativeSelectOption>待分类</NativeSelectOption><NativeSelectOption>控制计划</NativeSelectOption><NativeSelectOption>过程流程图</NativeSelectOption><NativeSelectOption>尺寸报告</NativeSelectOption></NativeSelect></label></TableCell>
          <TableCell><label className="grid gap-1.5"><span className="text-xs md:sr-only">修订</span><Input aria-label={`${file.name}的修订`} value={file.revision} placeholder="如 B" onChange={event => updateFile(file.id, {revision: event.target.value})}/></label></TableCell>
          <TableCell className="whitespace-normal"><label className="flex min-h-9 items-center gap-2"><Checkbox aria-label={`${file.name}属于本事项`} checked={file.associated} onCheckedChange={checked => updateFile(file.id, {associated: checked === true})}/><span>属于本事项</span></label></TableCell>
          <TableCell><Button variant="ghost" type="button" aria-label={`确认${file.name}的登记信息`} disabled={file.checked || !file.associated || file.kind === '待分类' || !file.revision.trim()} onClick={() => onFilesChange(files.map(item => item.id === file.id ? {...item, checked: true} : item))}>{file.checked ? '已确认' : '确认'}</Button></TableCell>
        </TableRow>)}</TableBody>
      </Table>
      <small>修改任何登记信息后，该文件必须重新确认。关联文件不等于问题解决。</small>
    </section>
    <div className={"flex justify-between items-center flex-wrap gap-3 max-w-212.5 max-sm:items-stretch"}><Button variant="outline" onClick={() => onStep('upload')}><ArrowLeft size={16}/> 返回选择文件</Button><Button className={""} disabled={!files.length || files.some(file => !file.checked)} onClick={() => onStep('reply')}>完成核对，登记答复 <ArrowRight size={16}/></Button></div>
  </div>

  return <div className={"pb-7.5"}>
    {heading}{steps}
    <p className={"-mt-2.5 mr-0 mb-6.5 ml-0 text-muted-foreground text-sm"}>审核员记录已收到的答复；未答复项保留。新原件不覆盖旧版，也不自动解决问题。</p>
    <label className={"grid gap-2 max-w-110 mt-0 mr-0 mb-6.5 ml-0 text-xs font-semibold"}><span>回件来源</span><Input aria-label="回件来源" placeholder="例如：供应商邮件，10 月 13 日" value={source} onChange={event => onSourceChange(event.target.value)}/></label>
    <div className={"flex gap-3.5 items-center flex-wrap -mt-1 mr-0 mb-7 ml-0 py-2.5 px-0 border-b text-xs [&_span]:font-semibold"}><span>{files.length ? `${files.length} 份已核对文件` : '本次无新文件'}</span><Button variant="ghost" type="button" onClick={() => onStep(files.length ? 'verify' : 'upload')}>{files.length ? '返回文件核对' : '选择补件文件'}</Button>{files.length > 0 && <Button variant="ghost" type="button" onClick={() => onStep('upload')}>补充文件</Button>}</div>
    {variant === 'tasks' && <div className={"min-w-0 grid grid-cols-[minmax(0,_1fr)_236px] items-start gap-7.5 max-lg:grid-cols-[minmax(0,_1fr)_190px] max-lg:gap-5 max-lg:block"}><div className={"grid gap-3.5 min-w-0"}>{keys.map(editor)}</div><aside className={"sticky top-5.5 pt-1.5 pr-0 pb-0 pl-0 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-3 [&_h2]:ml-0 [&_h2]:text-base [&_>_div]:grid [&_>_div]:grid-cols-[7px_1fr] [&_>_div]:gap-y-0.5 [&_>_div]:gap-x-2 [&_>_div]:py-3.5 [&_>_div]:px-0 [&_>_div]:border-t [&_>_div_>_span]:block [&_>_div_>_span]:w-2 [&_>_div_>_span]:h-2 [&_>_div_>_span]:mt-1.5 [&_>_div_>_span]:rounded-full [&_>_div_>_span]:bg-muted [&_strong]:text-xs [&_small]:col-start-2 [&_small]:text-muted-foreground [&_small]:text-xs [&_p]:mt-4.5 [&_p]:text-muted-foreground [&_p]:text-xs [&_p]:leading-relaxed max-lg:static max-lg:mt-5.5"}><h2>本次收件</h2>{keys.map(key => {const row = rows.find(item => item.id === key)!; return <div key={key}><span className={replyReady(replies[key]) ? "" : ""}/><strong>{row.title}</strong><small>{replyReady(replies[key]) ? '可登记' : '未收到'}</small></div>})}<p>说明只作为新记录附加；不能替换旧原件。收到新原件后仍须重新检查。</p></aside></div>}
    {variant === 'files' && <div className={"min-w-0 [&_nav_h2]:mt-0 [&_nav_h2]:mr-0 [&_nav_h2]:mb-3 [&_nav_h2]:ml-0 [&_nav_h2]:text-base grid grid-cols-[minmax(185px,_32%)_minmax(0,_1fr)] items-start gap-5.5 [&_nav]:grid [&_nav]:border-t [&_nav_h2]:pt-4 max-sm:block max-sm:[&_nav]:flex max-sm:[&_nav]:overflow-x-auto max-sm:[&_nav]:gap-2 max-sm:[&_nav]:mb-4 max-sm:[&_nav]:border-0 max-sm:[&_nav_h2]:hidden"}><nav aria-label="按原件选择补正请求"><h2>受影响原件</h2>{keys.map(key => {const row = rows.find(item => item.id === key)!; return <Button variant="ghost" type="button" key={key} aria-current={active === key ? 'true' : undefined} onClick={() => setFocused(key)}><FileText size={17}/><span><strong>{row.title}</strong><small>{row.original}</small></span><span className={replyReady(replies[key]) ? "" : ""}/></Button>})}</nav><div>{active && editor(active)}<p className={"text-muted-foreground text-xs"}>选中左侧其他原件可继续登记；已填写内容会保留在本次草稿中。</p></div></div>}
    {variant === 'guided' && <div className={"min-w-0 max-w-172.5 my-0 mx-auto"}><div className={"flex justify-between gap-3 mb-3.5 text-muted-foreground text-xs [&_strong]:text-foreground"}><span>第 {Math.max(1, keys.indexOf(active) + 1)} / {keys.length} 项</span><strong>一次登记一项</strong></div>{active && editor(active)}<div className={"flex items-center justify-between gap-3 mt-3.5 [&_span]:text-muted-foreground [&_span]:text-xs"}><Button variant="ghost" disabled={keys.indexOf(active) <= 0} onClick={() => setFocused(keys[keys.indexOf(active) - 1])}><ArrowLeft size={16}/> 上一项</Button><span>{ready.length} 项可登记</span><Button variant="ghost" disabled={keys.indexOf(active) >= keys.length - 1} onClick={() => setFocused(keys[keys.indexOf(active) + 1])}>下一项 <ArrowRight size={16}/></Button></div></div>}
    {submit}<span className={"sr-only"} aria-live="polite">模拟回件草稿：来源{source ? '已填' : '未填'}；{keys.map(key => `${key} ${replies[key].mode ?? '未选'} ${replyReady(replies[key]) ? '可登记' : '未收到'}`).join('；')}</span>
  </div>
}
