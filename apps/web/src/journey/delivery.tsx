import {isPreview} from './access'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch: projects/13261964020862199279/screens/0015340fe7124e599b68ffead87b9768.
// Same decision, three structures (?deliveryLayout=task|steps|history). No external sends.
import {useEffect, useRef, useState, type SetStateAction} from 'react'
import {ArrowLeft, ArrowRight, Download, FileArchive, Send, X} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import {Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {SourceReaderButton} from './source-reader'
import {RequirementOriginal} from './requirements'
import {sourceKey, type DemoDocument} from './source-documents'
import type {RecordedDecision} from './decision'
import {deliveryLabel, deliveryTargets, initialDelivery, packageBytes, recoveredDelivery, unresolvedDelivery, type DeliveryAttempt, type DeliveryPackage, type DeliveryState, type DeliveryTarget} from './delivery-state'
type Props = {
  record: RecordedDecision; documents: DemoDocument[]; saved?: DeliveryState
  setSaved: (next: SetStateAction<DeliveryState | undefined>) => void
  report: 'idle' | 'generating' | 'failed' | 'ready'; reportHTML: string
  onGenerateReport: () => void; onReportPrepared: () => void; onDownloadReport: () => void; onDownloadRecord: () => void
  failReport: boolean; setFailReport: (value: boolean) => void; downloadRequested: string | null
  legacyDelivery: {recipient: string; channel: string; note: string; at: string} | null
  legacyDraft?: DeliveryState['manualDraft']; historical: boolean; saveError: string
}
const layoutIds = ['task', 'steps', 'history'], layoutNames = ['交付工作台', '逐步交付', '记录优先']
const phaseLabels = {sending:'正在发送', checking:'正在核对', unknown:'发送结果未知', not_sent:'已确认未发送', sent:'已发送（模拟回执）'}
type Outcome = 'sent' | 'rejected' | 'unknown_sent' | 'unknown_absent'
type Confirmation = {packageId: string; target: DeliveryTarget; retry?: string}
type PackageJob = {kind: 'prepare' | 'download'; pack: DeliveryPackage; report: string; record: RecordedDecision; fail: boolean}
const stamp = (value: string) => new Date(value).toLocaleString('zh-CN')

function DeliveryPanel(props: Props & {onClose: () => void}) {
  const {record, documents, saved, setSaved, onClose} = props
  const initial = () => ({...initialDelivery(record.input.files), ...(props.legacyDraft ? {manualDraft: props.legacyDraft} : {})})
  const state = saved ?? initial()
  const update = (fn: (current: DeliveryState) => DeliveryState) => setSaved(current => fn(current ?? initial()))
  const [layout, setLayout] = useState(() => Math.max(0, layoutIds.indexOf(new URLSearchParams(location.search).get('deliveryLayout') ?? 'task')))
  const [step, setStep] = useState('prepare'), [channel, setChannel] = useState('feishu')
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [operation, setOperation] = useState<{kind: 'send' | 'query'; id: string; outcome?: Outcome; queryFails?: boolean} | null>(null)
  const [job, setJob] = useState<PackageJob | null>(null), [buildProgress, setBuildProgress] = useState('')
  const building = useRef(false), buildController = useRef<AbortController | null>(null)
  const preparing = job?.kind === 'prepare'
  const [failPackage, setFailPackage] = useState(false), [outcome, setOutcome] = useState<Outcome>('sent'), [queryFails, setQueryFails] = useState(false)
  const [notice, setNotice] = useState(''), [preview, setPreview] = useState(false), [permission, setPermission] = useState(false)
  const previewTrigger = useRef<HTMLButtonElement>(null), permissionTrigger = useRef<HTMLButtonElement>(null)
  const sendTrigger = useRef<HTMLButtonElement>(null), confirmationTrigger = useRef<HTMLButtonElement | null>(null)
  const historyHeading = useRef<HTMLHeadingElement>(null)
  const selected = state.packages.find(item => item.id === state.packageId)
  const unresolved = unresolvedDelivery(state)
  const latest = state.attempts.at(-1)
  const chosenFiles = record.input.files.filter(file => state.selection.includes(sourceKey(file)))
  const missing = chosenFiles.filter(file => !file.localFile && documents.filter(doc => sourceKey(doc) === sourceKey(file)).length !== 1)
  const selectionChanged = selected && JSON.stringify(selected.files.map(sourceKey)) !== JSON.stringify(chosenFiles.map(sourceKey))
  const manual = state.manualDraft
  // Restored observation loss is unknown, never an invitation to send again.
  useEffect(() => {update(recoveredDelivery)}, [])
  useEffect(() => {
    if (!job) return
    const request = job
    const controller = new AbortController(); buildController.current = controller
    let current = true
    async function run() {
      try {
        if (request.fail) throw new Error('本次模拟生成失败。')
        const bytes = await packageBytes(request.pack, request.report, request.record, controller.signal, label => {if (current) setBuildProgress(label)})
        if (!current || controller.signal.aborted) return
        if (request.kind === 'prepare') {
          update(state => ({...state, packages: [...state.packages, request.pack], packageId: request.pack.id, preparation: 'idle'}))
          props.onReportPrepared(); setNotice(`${request.pack.id} 已准备好，可下载。`)
        } else {
          const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], {type: 'application/zip'}))
          try {const link = document.createElement('a'); link.href = url; link.download = `${request.pack.id}.原型资料包.zip`; link.click()}
          finally {window.setTimeout(() => URL.revokeObjectURL(url), 1000)}
          update(state => ({...state, downloads: [...state.downloads, {packageId: request.pack.id, at: new Date().toISOString()}]}))
          setNotice('已请求浏览器下载 ZIP。请检查下载结果；这不是发送回执。')
        }
      } catch (error) {
        if (!current || controller.signal.aborted) return
        if (request.kind === 'prepare') update(state => ({...state, preparation: 'failed'}))
        setNotice(`${error instanceof Error ? error.message : '资料包读取失败。'}没有生成或下载新包，决定与已有记录保留。可在“核对本次资料”中恢复原件后重试。`)
      } finally {if (current) {building.current = false; buildController.current = null; setJob(null); setBuildProgress('')}}
    }
    void run()
    return () => {current = false; controller.abort(); if (buildController.current === controller) buildController.current = null}
  }, [job])
  useEffect(() => {
    if (!operation) return
    const timer = window.setTimeout(() => {
      update(current => ({...current, attempts: current.attempts.map(item => {
        if (item.id !== operation.id || item.phase !== (operation.kind === 'send' ? 'sending' : 'checking')) return item
        const phase = operation.kind === 'query' ? operation.queryFails ? 'unknown' : item.remote === 'sent' ? 'sent' : 'not_sent'
          : operation.outcome === 'sent' ? 'sent' : operation.outcome === 'rejected' ? 'not_sent' : 'unknown'
        return {...item, phase, observedAt: new Date().toISOString(), receipt: phase === 'sent' ? `DEMO-${item.id}` : undefined}
      })}))
      setOperation(null)
    }, 1600)
    return () => window.clearTimeout(timer)
  }, [operation])
  function selectLayout(next: number) {setLayout(next); const url = new URL(location.href); url.searchParams.set('deliveryLayout', layoutIds[next]); history.replaceState(null, '', url)}
  function prepare() {
    if (missing.length || building.current || props.saveError) return
    const pack: DeliveryPackage = {id: `${record.id}-P${state.packages.length + 1}`, at: new Date().toISOString(), files: structuredClone(chosenFiles), omitted: structuredClone(record.input.files.filter(file => !state.selection.includes(sourceKey(file)))), documents: structuredClone(documents.filter(doc => chosenFiles.some(file => sourceKey(file) === sourceKey(doc))))}
    update(current => ({...current, preparation: 'preparing'})); startBuild('prepare', pack)
  }
  function startBuild(kind: PackageJob['kind'], pack: DeliveryPackage) {
    if (building.current || props.saveError) return
    building.current = true; setNotice(''); setBuildProgress('正在核对资料')
    setJob({kind, pack: structuredClone(pack), report: props.reportHTML, record: structuredClone(record), fail: kind === 'prepare' && (failPackage || props.failReport)})
  }
  function cancelBuild() {
    buildController.current?.abort(); building.current = false
    if (preparing) update(current => ({...current, preparation: 'interrupted'}))
    setJob(null); setBuildProgress(''); setNotice('已取消，本次没有生成或下载新包。')
  }
  function send() {
    if (!confirmation || unresolved || !state.authorized || props.saveError) return
    const previous = confirmation.retry ? state.attempts.find(item => item.id === confirmation.retry) : undefined
    if (confirmation.retry && (!previous || previous.phase !== 'not_sent' || previous.id !== latest?.id)) return
    const bound = previous ?? confirmation
    if (!state.packages.some(pack => pack.id === bound.packageId)) return
    const id = `S${state.attempts.length + 1}`
    const attempt: DeliveryAttempt = {id, intent: previous?.intent ?? id, packageId: bound.packageId, target: bound.target, at: new Date().toISOString(), phase: 'sending', remote: ['sent','unknown_sent'].includes(outcome) ? 'sent' : 'absent'}
    update(current => unresolvedDelivery(current) ? current : {...current, attempts: [...current.attempts, attempt]})
    setConfirmation(null); setOperation({kind: 'send', id, outcome}); setStep('history')
  }
  function reconcile(attempt: DeliveryAttempt) {
    if (attempt.phase !== 'unknown' || props.saveError) return
    update(current => ({...current, attempts: current.attempts.map(item => item.id === attempt.id && item.phase === 'unknown' ? {...item, phase: 'checking'} : item)}))
    setOperation({kind: 'query', id: attempt.id, queryFails})
  }
  const prepareSection = <section className={"min-w-0 mb-7"} aria-label="准备交付内容"><h3>交付内容</h3><p>审核报告与依据记录始终包含。选择要附上的本次资料。</p>
    <Table><TableHeader><TableRow><TableHead>资料</TableHead><TableHead>版本</TableHead><TableHead>附加内容</TableHead></TableRow></TableHeader><TableBody>{record.input.files.map(file => {
      const available = Boolean(file.localFile) || documents.filter(doc => sourceKey(doc) === sourceKey(file)).length === 1
      return <TableRow key={sourceKey(file)}><TableCell><label className={"flex items-start gap-2.5 min-w-0 [&_[role=checkbox]]:shrink-0 [&_[role=checkbox]]:mt-1 [&_span]:break-all [&_span]:min-w-0 max-sm:min-h-11 max-sm:items-center max-sm:[&_[role=checkbox]]:mt-0"}><Checkbox aria-label={`附上 ${file.name} · 文件 v${file.sourceVersion}`} checked={state.selection.includes(sourceKey(file))} disabled={Boolean(job)} onCheckedChange={checked => update(current => ({...current, selection: checked ? [...current.selection, sourceKey(file)] : current.selection.filter(key => key !== sourceKey(file))}))}/><span>{file.name}</span></label></TableCell><TableCell>Rev {file.revision}<small>文件 v{file.sourceVersion}</small></TableCell><TableCell>{file.localFile ? '本机原件' : available ? '示例 HTML' : '不可读取'}</TableCell></TableRow>
    })}</TableBody></Table>
    <div className={"flex flex-wrap items-center gap-2 my-4 mx-0"}><SourceReaderButton input={record.input} documents={documents} label="核对本次资料"/>{record.input.basis?.source.localFile && <RequirementOriginal basis={record.input.basis}/>}<Button ref={previewTrigger} variant="ghost" onClick={() => setPreview(true)}>预览报告</Button></div>
    {missing.length > 0 && <p role="alert">{missing.length} 份资料不可读取。先取消勾选这些资料，或返回事项补充资料；不能把缺失原件称为完整包。</p>}
    {chosenFiles.length < record.input.files.length && <p>本包将不包含 {record.input.files.length - chosenFiles.length} 份资料，清单会保留缺项。</p>}
    {['failed','interrupted'].includes(state.preparation) && <p role="alert">{state.preparation === 'interrupted' ? '上次生成中断，未产生新包。' : '资料包生成失败。'}决定及已有资料包未变，可重新生成。</p>}
    <div className={"flex flex-wrap items-center gap-2 my-4 mx-0"}><Button className={""} disabled={Boolean(job) || missing.length > 0 || Boolean(props.saveError)} onClick={prepare}><FileArchive/>{preparing ? '正在准备…' : state.packages.length ? '生成新资料包' : '准备资料包'}</Button><span>{chosenFiles.length} / {record.input.files.length} 份资料</span></div>
    <p className={""}>{record.input.basis?.source.localFile ? `同时附上规则原件：${record.input.basis.source.filename}。` : '要求条款固定在报告与依据记录中。'}打包时核对本机原件；示例资料保留示例标识。</p>
  </section>
  const sendSection = <section className={"min-w-0 mb-7"} aria-label="交付方式"><h3>交付方式</h3>
    {!state.packages.length ? <p>准备资料包后，选择下载或发送。</p> : <>
      <label className={"grid gap-1.5 my-3 mx-0"}>本次交付的资料包<Select value={state.packageId} onValueChange={packageId => update(current => ({...current, packageId, manualDraft: {...current.manualDraft, acknowledged: false}}))}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label="本次交付的资料包"><SelectValue className="min-w-0"/></SelectTrigger><SelectContent>{state.packages.map(pack => <SelectItem key={pack.id} value={pack.id}>{pack.id} · {pack.files.length} 份资料{pack.omitted.length ? ` · 缺 ${pack.omitted.length} 份` : ''}</SelectItem>)}</SelectContent></Select></label>
      {selected && <p>{selected.files.length} 份资料 · {stamp(selected.at)}{selected.omitted.length > 0 && ` · 未包含 ${selected.omitted.length} 份`}</p>}
      {selectionChanged && <p role="status">勾选已改变。当前交付仍是 {selected?.id}；要采用新清单，请先生成新包。</p>}
      <Tabs value={channel} onValueChange={setChannel}><TabsList variant="line" aria-label="交付方式"><TabsTrigger value="feishu">飞书</TabsTrigger><TabsTrigger value="download">下载与登记</TabsTrigger></TabsList>
        <TabsContent value="feishu"><label className={"grid gap-1.5 my-3 mx-0"}>接收位置<Select value={state.target} onValueChange={target => update(current => ({...current, target: target as DeliveryTarget}))}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label="接收位置"><SelectValue className="min-w-0"/></SelectTrigger><SelectContent><SelectItem value="group">群 · {deliveryTargets.group}</SelectItem><SelectItem value="document">文档 · {deliveryTargets.document}</SelectItem></SelectContent></Select></label>
          <p>{state.target === 'group' ? '发送决定摘要及资料包。' : '向所选文档追加本次决定及资料包引用，不覆盖原文。'}</p>
          {!state.authorized ? <div className={"py-3 px-3.5 border-l bg-background my-3.5 mx-0"}><strong>尚无发送权限</strong><p>先连接飞书并确认所选位置的写入权限。</p><Button ref={permissionTrigger} variant="outline" onClick={() => setPermission(true)}>连接飞书（模拟）</Button></div> : <p>已获发送权限（模拟）<Button variant="ghost" size="sm" onClick={() => update(current => ({...current, authorized: false}))}>断开连接</Button></p>}
          <Button ref={sendTrigger} className={""} disabled={!selected || !state.authorized || Boolean(unresolved) || Boolean(props.saveError)} onClick={event => {confirmationTrigger.current = event.currentTarget; setConfirmation({packageId: state.packageId, target: state.target})}}><Send/>核对后发送</Button>
          {unresolved && <p role="status">先处理下方 {unresolved.id} 的发送结果，当前不能再发起发送。</p>}
        </TabsContent>
        <TabsContent value="download"><div className={"flex flex-wrap items-center gap-2 my-4 mx-0"}><Button variant="outline" disabled={!selected || Boolean(job) || Boolean(props.saveError)} onClick={() => selected && startBuild('download', selected)}><Download/>{job?.kind === 'download' ? '正在读取资料包…' : '下载资料包'}</Button><Button variant="ghost" disabled={props.report !== 'ready'} onClick={props.onDownloadReport}>单独下载报告</Button><Button variant="ghost" onClick={props.onDownloadRecord}>下载依据记录</Button></div>
          {state.downloads.length > 0 && <p>已请求下载 {state.downloads.at(-1)?.packageId}，浏览器是否保存成功需自行确认。</p>}
          <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">登记系统外已完成的交付</Button></CollapsibleTrigger><CollapsibleContent><div className={"[&>label]:grid [&>label]:gap-1.5 [&>label]:my-3 [&>label]:mx-0"}>
            <label>接收对象<Input value={manual.recipient} onChange={event => update(current => ({...current, manualDraft: {...current.manualDraft, recipient: event.target.value, acknowledged: false}}))}/></label>
            <label>交付渠道<Input value={manual.channel} placeholder="如邮件、当面交接" onChange={event => update(current => ({...current, manualDraft: {...current.manualDraft, channel: event.target.value, acknowledged: false}}))}/></label>
            <label>交付说明<Textarea value={manual.note} placeholder="记录交付内容、时间和可追溯位置" onChange={event => update(current => ({...current, manualDraft: {...current.manualDraft, note: event.target.value, acknowledged: false}}))}/></label>
            <label className={"flex items-start gap-2.5 min-w-0 [&_[role=checkbox]]:shrink-0 [&_[role=checkbox]]:mt-1 [&_span]:break-all [&_span]:min-w-0 max-sm:min-h-11 max-sm:items-center max-sm:[&_[role=checkbox]]:mt-0"}><Checkbox checked={manual.acknowledged} onCheckedChange={checked => update(current => ({...current, manualDraft: {...current.manualDraft, acknowledged: checked === true}}))}/><span>我已在系统外交付 {selected?.id}</span></label>
            <Button disabled={!selected || !manual.acknowledged || !manual.recipient.trim() || !manual.channel.trim() || !manual.note.trim() || Boolean(props.saveError)} onClick={() => {update(current => ({...current, manual: [...current.manual, {packageId: current.packageId, recipient: current.manualDraft.recipient.trim(), channel: current.manualDraft.channel.trim(), note: current.manualDraft.note.trim(), at: new Date().toISOString()}], manualDraft: {recipient:'',channel:'',note:'',acknowledged:false}})); setNotice('已保存人工交付记录，系统没有发送文件。'); setStep('history')}}>保存交付记录</Button>
          </div></CollapsibleContent></Collapsible>
        </TabsContent>
      </Tabs>
    </>}
    {!state.packages.length && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">只需要报告</Button></CollapsibleTrigger><CollapsibleContent><div className={"flex flex-wrap items-center gap-2 my-4 mx-0"}>{props.report === 'ready' ? <Button variant="outline" onClick={props.onDownloadReport}>下载报告</Button> : <Button variant="outline" disabled={props.report === 'generating'} onClick={props.onGenerateReport}>{props.report === 'generating' ? '正在生成…' : '生成审核报告'}</Button>}<Button variant="ghost" onClick={props.onDownloadRecord}>下载依据记录</Button></div>{props.report === 'failed' && <p role="alert">报告生成失败，可重试，决定已保留。</p>}{props.downloadRequested && <p>已请求浏览器下载，不代表对外送达。</p>}</CollapsibleContent></Collapsible>}
  </section>
  const historySection = <section className={"min-w-0 mb-7 border-t pt-6 [&_article]:py-4 [&_article]:px-0 [&_article]:border-b [&_article[data-pending=true]]:border-l [&_article[data-pending=true]]:pl-3.5"} aria-label="交付记录"><h3 ref={historyHeading} tabIndex={-1}>交付记录</h3>{!state.attempts.length && !state.manual.length && !props.legacyDelivery && <p>尚无交付记录。生成和下载不会在这里变成“已发送”。</p>}
    {[...state.attempts].reverse().map(attempt => <article key={attempt.id} data-pending={['unknown','sending','checking'].includes(attempt.phase)}><div className={"flex items-center justify-between gap-3 flex-wrap"}><strong>{deliveryTargets[attempt.target]}</strong><span className={["inline-flex items-center justify-center w-fit min-h-6.5 py-0.5 px-2 rounded-lg text-xs font-semibold whitespace-nowrap",({"bad":"bg-destructive/10 text-destructive","wait":"bg-muted text-foreground","missing":"bg-muted text-primary","good":"bg-muted text-foreground"} as Record<string,string>)[String(attempt.phase === "sent" ? "good" : "wait")] ?? "",""].filter(Boolean).join("")}>{phaseLabels[attempt.phase]}</span></div><p>{attempt.packageId} · {attempt.target === 'group' ? '发送至群' : '追加至文档'} · {attempt.id}{attempt.intent !== attempt.id ? `（重试 ${attempt.intent}）` : ''}</p><small>{stamp(attempt.at)}</small>
      {attempt.phase === 'unknown' && <><p>未拿到明确回执。先核对，不能直接重发。</p><Button className={""} onClick={() => reconcile(attempt)} disabled={Boolean(props.saveError)}>核对发送结果</Button></>}
      {['sending','checking'].includes(attempt.phase) && <p role="status">{attempt.phase === 'sending' ? '正在等待回执。离开本页不代表取消发送。' : '正在核对这笔发送，不会另发文件。'}</p>}
      {attempt.phase === 'not_sent' && latest?.id === attempt.id && <><p>已确认此尝试没有发送；重试仍使用本行资料包与接收位置。</p><Button variant="outline" disabled={!state.authorized || Boolean(unresolved) || Boolean(props.saveError)} onClick={event => {confirmationTrigger.current = event.currentTarget; setConfirmation({packageId: attempt.packageId, target: attempt.target, retry: attempt.id})}}>重试这笔发送</Button>{!state.authorized && <p>请先恢复飞书连接。</p>}</>}
      {attempt.phase === 'sent' && <p>回执 {attempt.receipt} · {attempt.observedAt && stamp(attempt.observedAt)}。不表示接收人已读。</p>}
    </article>)}
    {[...state.manual].reverse().map((item, index) => <article key={`${item.at}-${index}`}><strong>人工登记 · {item.recipient}</strong><p>{item.packageId} · {item.channel}</p><p>{item.note}</p><small>{stamp(item.at)} · 系统未发送文件</small></article>)}
    {props.legacyDelivery && <article><strong>人工登记 · {props.legacyDelivery.recipient}</strong><p>本决定的报告 · {props.legacyDelivery.channel}</p><p>{props.legacyDelivery.note}</p><small>{stamp(props.legacyDelivery.at)} · 系统未发送文件</small></article>}
  </section>
  const simulator = isPreview() && import.meta.env.DEV && <Collapsible className={""}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">演示异常</Button></CollapsibleTrigger><CollapsibleContent><div className={"[&>label]:grid [&>label]:gap-1.5 [&>label]:my-3 [&>label]:mx-0"}><label className={"flex items-start gap-2.5 min-w-0 [&_[role=checkbox]]:shrink-0 [&_[role=checkbox]]:mt-1 [&_span]:break-all [&_span]:min-w-0 max-sm:min-h-11 max-sm:items-center max-sm:[&_[role=checkbox]]:mt-0"}><Checkbox checked={failPackage} disabled={Boolean(preparing)} onCheckedChange={checked => setFailPackage(checked === true)}/>资料包生成失败</label><label className={"flex items-start gap-2.5 min-w-0 [&_[role=checkbox]]:shrink-0 [&_[role=checkbox]]:mt-1 [&_span]:break-all [&_span]:min-w-0 max-sm:min-h-11 max-sm:items-center max-sm:[&_[role=checkbox]]:mt-0"}><Checkbox checked={props.failReport} disabled={Boolean(preparing) || props.report === 'generating'} onCheckedChange={checked => props.setFailReport(checked === true)}/>报告生成失败</label><label>下一次发送<Select value={outcome} onValueChange={value => setOutcome(value as Outcome)}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label="下一次发送"><SelectValue className="min-w-0"/></SelectTrigger><SelectContent><SelectItem value="sent">成功并收到回执</SelectItem><SelectItem value="rejected">明确拒绝，未发送</SelectItem><SelectItem value="unknown_sent">回执丢失，实际已发送</SelectItem><SelectItem value="unknown_absent">回执丢失，实际未发送</SelectItem></SelectContent></Select></label><label className={"flex items-start gap-2.5 min-w-0 [&_[role=checkbox]]:shrink-0 [&_[role=checkbox]]:mt-1 [&_span]:break-all [&_span]:min-w-0 max-sm:min-h-11 max-sm:items-center max-sm:[&_[role=checkbox]]:mt-0"}><Checkbox checked={queryFails} onCheckedChange={checked => setQueryFails(checked === true)}/>核对接口暂不可用</label></div></CollapsibleContent></Collapsible>
  return <DialogContent className={"flex min-h-dvh max-lg:[&_td:first-child]:w-auto       max-sm:[&_td:nth-child(2)]:min-w-16.5 max-sm:[&_td:last-child]:min-w-16.5"} showCloseButton={false} onKeyDown={event => {if (['ArrowLeft','ArrowRight'].includes(event.key) && !(event.target as HTMLElement).closest('input,textarea,select,button,[role=combobox],[role=tab]')) {event.preventDefault(); event.stopPropagation(); selectLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)}}}>
    <header className={"flex items-start justify-between gap-4 py-5.5 px-7 border-b max-sm:p-4"}><div><DialogTitle>交付结果</DialogTitle><DialogDescription>{record.outcome} · {record.id}</DialogDescription></div><Button variant="ghost" size="icon" aria-label="关闭交付工作区" onClick={onClose}><X/></Button></header>
    <div className={"flex-1 overflow-auto min-h-0 pt-5 pr-7 pb-7 pl-7 max-sm:p-4"}><p className={"mt-0"}>{record.input.scope}</p>{props.historical && <p className={"py-3 px-3.5 border-l bg-background my-3.5 mx-0"}>这是历史决定的交付。已有后续变化，本包不代表当前版本的结论。</p>}{props.saveError && <p role="alert">{props.saveError}</p>}
      <div className={"flex justify-between flex-wrap gap-2 pt-3 pr-0 pb-5 pl-0 mb-5 border-b [&_strong]:text-primary"}><span>{record.input.evidence.length} 条要求 · {record.input.files.length} 份资料</span><strong role="status">{deliveryLabel(state, Boolean(props.legacyDelivery))}</strong></div>
      {layout === 0 && <><div className={"grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-8 max-lg:grid-cols-1 max-lg:gap-0"}>{prepareSection}{sendSection}</div>{historySection}</>}
      {layout === 1 && <Tabs value={step} onValueChange={setStep}><TabsList variant="line" aria-label="交付步骤"><TabsTrigger value="prepare">1 · 准备</TabsTrigger><TabsTrigger value="send">2 · 交付</TabsTrigger><TabsTrigger value="history">3 · 记录</TabsTrigger></TabsList><TabsContent value="prepare">{prepareSection}<Button variant="outline" onClick={() => setStep('send')}>继续选择交付方式<ArrowRight/></Button></TabsContent><TabsContent value="send">{sendSection}</TabsContent><TabsContent value="history">{historySection}</TabsContent></Tabs>}
      {layout === 2 && <div className={"grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-8 max-lg:grid-cols-1 max-lg:gap-0"}>{historySection}<div>{sendSection}<Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">准备新的资料包</Button></CollapsibleTrigger><CollapsibleContent>{prepareSection}</CollapsibleContent></Collapsible></div></div>}
      {notice && <p role="status" className={"py-3 px-3.5 border-l bg-background my-3.5 mx-0"}>{notice}</p>}{simulator}
    </div>
    {job && <div className={"flex items-center justify-between gap-3 py-3 px-6 border-t bg-background shrink-0 [&_span]:min-w-0 [&_span]:break-all"}><span role="status">{buildProgress}</span><Button variant="outline" onClick={cancelBuild}>取消打包</Button></div>}
    <footer className={"flex items-center justify-between gap-3 py-2.5 px-6 border-t text-muted-foreground bg-background text-xs shrink-0 max-sm:flex-col max-sm:gap-1.5 max-sm:pt-2 max-sm:pr-3  max-sm:pl-3"}><span>模拟交付，不发送真实文件。</span>{isPreview() && import.meta.env.DEV && <div className={"flex items-center gap-2 rounded-lg py-1 px-1.5 bg-muted text-foreground whitespace-nowrap"} role="toolbar" aria-label="交付布局"><Button size="icon-sm" variant="ghost" aria-label="上一种交付布局" onClick={() => selectLayout((layout + 2) % 3)}><ArrowLeft/></Button><span>{layout + 1} / 3 · {layoutNames[layout]}</span><Button size="icon-sm" variant="ghost" aria-label="下一种交付布局" onClick={() => selectLayout((layout + 1) % 3)}><ArrowRight/></Button></div>}</footer>
    <Dialog open={Boolean(confirmation)} onOpenChange={open => !open && setConfirmation(null)}><DialogContent onCloseAutoFocus={event => {event.preventDefault(); const trigger = confirmationTrigger.current; (trigger?.isConnected && !trigger.disabled ? trigger : historyHeading.current)?.focus()}}><DialogTitle>{confirmation?.retry ? '重试这笔发送' : '确认发送内容'}</DialogTitle><DialogDescription>模拟发送。离开页面不代表取消。</DialogDescription><p>{confirmation && deliveryTargets[confirmation.target]}</p><p>{confirmation?.packageId}</p><p>{record.outcome} · {record.input.evidence.length} 条限定要求，不代表全项 PPAP 批准。</p><p>{state.packages.find(pack => pack.id === confirmation?.packageId)?.omitted.length ? `此包未包含 ${state.packages.find(pack => pack.id === confirmation?.packageId)?.omitted.length} 份资料。` : '包含本包所列资料及报告。'}</p><Button onClick={send} disabled={!state.authorized || Boolean(unresolved) || Boolean(props.saveError)}>确认模拟发送</Button><Button variant="outline" onClick={() => setConfirmation(null)}>返回修改</Button></DialogContent></Dialog>
    <Dialog open={permission} onOpenChange={setPermission}><DialogContent onCloseAutoFocus={event => {event.preventDefault(); (permissionTrigger.current ?? sendTrigger.current ?? historyHeading.current)?.focus()}}><DialogTitle>连接飞书</DialogTitle><DialogDescription>仅演示授权，不访问真实账号。</DialogDescription><Button onClick={() => {update(current => ({...current, authorized: true})); setPermission(false); setNotice('已连接飞书示例目标。')}}>允许（模拟）</Button><Button variant="outline" onClick={() => {setPermission(false); setNotice('未取得权限。可以重试连接或下载后在系统外交付。')}}>拒绝授权</Button></DialogContent></Dialog>
    <Dialog open={preview} onOpenChange={setPreview}><DialogContent className={""} onCloseAutoFocus={event => {event.preventDefault(); previewTrigger.current?.focus()}}><DialogTitle>本次审核报告</DialogTitle><DialogDescription>{record.id} · 固定决定的范围与依据</DialogDescription><iframe title="审核报告预览" sandbox="" srcDoc={props.reportHTML}/></DialogContent></Dialog>
  </DialogContent>
}

export function DeliveryWorkspace(props: Props) {
  const [open, setOpen] = useState(false)
  function changeOpen(next: boolean) {
    if (!next) props.setSaved(current => current ? recoveredDelivery(current) : current)
    setOpen(next)
  }
  return <Dialog open={open} onOpenChange={changeOpen}><DialogTrigger asChild><Button className={""}><FileArchive/>打开交付工作区</Button></DialogTrigger>{open && <DeliveryPanel key={props.record.id} {...props} onClose={() => changeOpen(false)}/>}</Dialog>
}
