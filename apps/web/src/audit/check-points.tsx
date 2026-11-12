// Stitch: projects/13261964020862199279/screens/0efd7637e4a343919cd039650cdf6847.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, Check, ChevronLeft, ChevronRight, FileText, FileUp, Loader2} from 'lucide-react'
import {request, uploadLocalVersion} from '../api'
import type {components} from '../generated/openapi'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Progress} from '../components/ui/progress'
import {Checkbox} from '../components/ui/checkbox'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Skeleton} from '../components/ui/skeleton'
import {Textarea} from '../components/ui/textarea'
import {message} from '../journey/remote'
import {ProgramStatus, StatusCard} from './program-status'
import {attentionLabels, attentionSummary, canRequestFormalization, checkPointLines, formalizationFailure, formalizationReceiptMatches, sameFormalizationTarget, textForCandidate, visibleFormalizationReadings} from '../journey/requirement-formalization-contract'

type Detail = components['schemas']['ClauseFormalizationView']
type Receipt = components['schemas']['OperationReceipt']
type Rejection = components['schemas']['FormalizationRejection']
type Generation = components['schemas']['OptimizationReceipt']
type GenerationView = components['schemas']['GenerationView']
type Target = {ruleVersionId: string; clauseId: string}
type Adopt = (clauseId: string, text: string, note: string) => void

const running = (state?: string) => state === 'queued' || state === 'running'
const unprepared = (state?: string) => state === 'not_started' || state === 'source_changed'
const REJECTED = '已拒绝（无可程序化判定标准）：'
/** Confirmed, or confirmed as rejected: either way the person has decided this clause. */
const decided = (status?: Status) => status === 'confirmed' || status === 'rejected'

/**
 * An uploaded rule's program on top of its page: generate it when there is none, follow a generation,
 * or hand over to the version card. The work itself happens in the wizard; nothing here runs on its own.
 */
export function RuleProgramCard({ruleVersionId, onPrepare}: {ruleVersionId: string; onPrepare: (updating: boolean) => void}) {
  const [view, setView] = useState<GenerationView | null>(null)
  const [readError, setReadError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setReadError('')
    void request<GenerationView>(`/rules/${ruleVersionId}/checker-generation`, 'GET', undefined, controller.signal)
      .then(value => {if (!controller.signal.aborted && value.rule_version_id === ruleVersionId) setView(value)})
      .catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [ruleVersionId, attempt])
  const reload = () => setAttempt(value => value + 1)
  if (readError) return <StatusCard badge="暂时读不到" title="检查程序的状态没读到" action={<Button variant="outline" onClick={reload}>重新读取</Button>}>{readError}</StatusCard>
  if (!view) return <StatusCard badge="正在读取" title="正在读取…"/>
  if (view.program === 'unavailable') return null
  const operation = view.operation
  const working = operation && (running(operation.state) || operation.state === 'unknown') ? <GenerationProgress initial={operation} onChanged={reload}/> : null
  const updating = view.program !== 'missing' && Boolean(view.suite_key)
  const summary = <CheckPointSummary ruleVersionId={ruleVersionId} onOpen={() => onPrepare(updating)}/>
  if (updating) {
    const fresh = operation?.state === 'completed' && operation.candidate_revision_id && operation.candidate_revision_id !== view.current_revision_id ? operation.candidate_revision_id : null
    return <>{summary}<ProgramStatus suiteKey={view.suite_key!} offered={fresh} onUpdate={working ? undefined : () => onPrepare(true)} extra={working} onChanged={reload}/></>
  }
  if (working) return <>{summary}<StatusCard badge="生成中" title="探索模式正在生成检查程序">{working}</StatusCard></>
  return <>{summary}<StatusCard badge="未生成" title="还不能用于审核" action={<Button onClick={() => onPrepare(false)}>生成检查程序</Button>}>
    {operation && (operation.state === 'failed' || operation.state === 'stopped') && <p className="text-foreground">上次生成没有完成：{generationFailure(operation)}</p>}
    {operation?.state === 'completed' && <p className="text-foreground">上次生成期间规则改过，没有启用。请重新生成。</p>}
  </StatusCard></>
}

/**
 * What the clauses of this version wait for. Saving a version reads every clause in the background, so the
 * person finds decisions here, grouped the way they take them up, and opens the steps to make them.
 */
function CheckPointSummary({ruleVersionId, onOpen}: {ruleVersionId: string; onOpen: () => void}) {
  const [clauses, setClauses] = useState<{attention?: string}[] | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void request<{rule_version_id: string; clauses: {attention?: string}[]}>(`/rules/${ruleVersionId}/formalizations`, 'GET', undefined, controller.signal)
      .then(value => {if (!controller.signal.aborted && value.rule_version_id === ruleVersionId) setClauses(value.clauses)})
      // A rule that cannot be read is reported by the program card beside this one.
      .catch(() => {})
    return () => controller.abort()
  }, [ruleVersionId, tick])
  const summary = clauses ? attentionSummary(clauses) : null
  // Only while readings are in flight; each read is the whole list, so a missed one is caught up by the next.
  useEffect(() => {
    if (!summary?.running) return
    const timer = window.setTimeout(() => setTick(value => value + 1), 10000)
    return () => window.clearTimeout(timer)
  }, [summary?.running, tick])
  if (!summary || (summary.open === 0 && summary.running === 0)) return null
  const {counts, open, running: reading} = summary
  return <StatusCard label="条款整理" badge={reading ? '整理中' : '等你处理'} tone={reading ? 'outline' : 'warning'} title={reading ? `正在整理 ${reading} 条条款` : `${open} 条条款等你处理`}
    action={open > 0 && <Button onClick={onOpen}>去处理</Button>}>
    <p className="text-foreground">{attentionLabels.filter(([key]) => counts[key]).map(([key, label]) => `${label} ${counts[key]} 条`).join(' · ')}</p>
    {reading > 0 && <p>可以离开页面，整理完回来处理。</p>}
    <p>没处理的条款，审核时交给人判断；拒绝的条款固定为无法判断。都不会判为通过。</p>
  </StatusCard>
}

function generationFailure(operation: Generation) {
  if (operation.state === 'stopped') return '已停止。'
  return formalizationFailure(operation.failure)
}

/** One generation, read until it ends. A lost transport is read back, never started again. */
function GenerationProgress({initial, onChanged}: {initial: Generation; onChanged: () => void}) {
  const [operation, setOperation] = useState(initial)
  const [error, setError] = useState('')
  const [stopping, setStopping] = useState(false)
  useEffect(() => {
    if (!running(operation.state)) return
    const timer = window.setTimeout(() => {
      void request<Generation>(`/checker-optimizations/${operation.operation_id}`, 'GET').then(value => {
        setOperation(value)
        if (!running(value.state)) onChanged()
      }).catch(cause => setError(message(cause)))
    }, 3000)
    return () => window.clearTimeout(timer)
  }, [operation])
  async function stop() {
    setStopping(true)
    try {setOperation(await request<Generation>(`/checker-optimizations/${operation.operation_id}/stop`, 'POST'))} catch (cause) {setError(message(cause))} finally {setStopping(false)}
  }
  return <div className="space-y-2 text-foreground">
    {running(operation.state) && <div className="flex flex-wrap items-center gap-3"><span role="status" className="flex items-center gap-2"><Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none"/>正在生成，可能需要几分钟。可以离开页面。</span><Button variant="outline" size="sm" disabled={stopping || operation.cancel_requested} onClick={() => void stop()}>{operation.cancel_requested ? '正在停止' : '停止'}</Button></div>}
    {operation.state === 'unknown' && <div role="status" className="flex flex-wrap items-center gap-3"><span>还不知道生成结果。刷新看看，不要重新开始。</span><Button variant="outline" size="sm" onClick={onChanged}>刷新</Button></div>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </div>
}

/** The full-page steps that make or update an uploaded rule's program. */
export function ProgramWizard({ruleVersionId, ruleTitle, version, updating, onClose, onAdopt}: {ruleVersionId: string; ruleTitle: string; version: string; updating: boolean; onClose: () => void; onAdopt: Adopt}) {
  const title = updating ? '更新检查程序' : '生成检查程序'
  return <div className="space-y-6">
    <header className="space-y-1">
      <Button variant="ghost" size="sm" className="-ml-2 max-w-full" onClick={onClose}><ArrowLeft/><span className="truncate">{ruleTitle} · 版本 {version}</span></Button>
      <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
      <p className="text-sm text-muted-foreground">探索模式：用已确认的条款和样本生成检查程序，人工采用后才用于审核。</p>
    </header>
    <CheckPoints ruleVersionId={ruleVersionId} action={title} onStarted={onClose} onAdopt={onAdopt}/>
  </div>
}

/** Where a clause stands, as the list beside the clause shows it. */
type Status = 'waiting' | 'running' | 'failed' | 'question' | 'review' | 'ready' | 'confirmed' | 'rejected'
type Report = {state: string; status: Status; text: string}
const statusLabel: Record<Status, string> = {waiting: '待整理', running: '整理中', failed: '没整理出来', question: '要选择', review: '要补充标准', ready: '待确认', confirmed: '已确认', rejected: '已拒绝'}
/** The decisions a person can jump to, in the order to take them up. */
const jumpOrder: Status[] = ['question', 'review', 'ready', 'failed']

function StatusMark({status}: {status?: Status}) {
  if (!status) return null
  const mark = decided(status) ? <Check className={`size-4 ${status === 'rejected' ? 'text-muted-foreground' : 'text-success'}`}/>
    : status === 'running' ? <Loader2 className="size-4 animate-spin text-muted-foreground motion-reduce:animate-none"/>
    : <span className={`size-2 rounded-full ${status === 'review' || status === 'question' ? 'bg-warning' : status === 'failed' ? 'bg-destructive' : status === 'ready' ? 'bg-foreground' : 'border border-muted-foreground/60'}`}/>
  return <span className="flex size-4 shrink-0 items-center justify-center" title={statusLabel[status]}>{mark}<span className="sr-only">{statusLabel[status]}</span></span>
}

/** Check points arrive as section headings followed by their statements; a reading without headings is one group. */
function groupPoints(lines: string[]): {title: string; items: string[]}[] {
  const groups: {title: string; items: string[]}[] = []
  for (const line of lines) {
    if (line.startsWith('# ')) groups.push({title: line.slice(2), items: []})
    else if (groups.length) groups.at(-1)!.items.push(line)
    else groups.push({title: '', items: [line]})
  }
  return groups.filter(group => group.items.length)
}

/** The suggested wording split into kept and added runs, so a person sees what would change. */
function insertions(before: string, after: string): {text: string; added: boolean}[] {
  const a = [...before], b = [...after]
  // Longest common subsequence over characters; clauses are short enough for the full table.
  const table = Array.from({length: a.length + 1}, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) table[i]![j] = a[i] === b[j] ? table[i + 1]![j + 1]! + 1 : Math.max(table[i + 1]![j]!, table[i]![j + 1]!)
  const parts: {text: string; added: boolean}[] = []
  const push = (text: string, added: boolean) => {const last = parts.at(-1); if (last && last.added === added) last.text += text; else parts.push({text, added})}
  let i = 0, j = 0
  while (j < b.length) {
    if (i < a.length && a[i] === b[j]) {push(b[j]!, false); i++; j++}
    else if (i < a.length && table[i + 1]![j]! >= table[i]![j + 1]!) i++
    else {push(b[j]!, true); j++}
  }
  return parts
}

/** Long unchanged stretches shrink to their edges, so only the changes and a little context remain. */
function changesOnly(parts: {text: string; added: boolean}[]): {text: string; added: boolean}[] {
  const edge = 14
  return parts.map((part, index) => {
    if (part.added || part.text.length <= edge * 2 + 4) return part
    const chars = [...part.text]
    const head = index === 0 ? '' : chars.slice(0, edge).join('')
    const tail = index === parts.length - 1 ? '' : chars.slice(-edge).join('')
    return {text: `${head}…${tail}`, added: false}
  })
}

function Changes({parts}: {parts: {text: string; added: boolean}[]}) {
  return <p className="whitespace-pre-wrap break-words leading-relaxed">{parts.map((part, index) => part.added ? <mark key={index} className="rounded-sm bg-success/15 px-0.5 text-foreground">{part.text}</mark> : <span key={index} className="text-muted-foreground">{part.text}</span>)}</p>
}

/** Every clause is read when the version is saved, or here on request, and confirmed one at a time; the example file and the start stay in reach at the bottom. */
function CheckPoints({ruleVersionId, action, onStarted, onAdopt}: {ruleVersionId: string; action: string; onStarted: () => void; onAdopt: Adopt}) {
  const [clauses, setClauses] = useState<string[] | null>(null)
  const [readError, setReadError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [reports, setReports] = useState<Record<string, Report>>({})
  const [selected, setSelected] = useState('')
  const [prepare, setPrepare] = useState(0)
  const [carryable, setCarryable] = useState<string[]>([]), [carrying, setCarrying] = useState(false)
  const [example, setExample] = useState<{name: string; id: string} | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const operationId = useRef(crypto.randomUUID())
  const picker = useRef<HTMLInputElement>(null)
  const detail = useRef<HTMLDivElement>(null)
  const placed = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    setClauses(null); setReadError(''); setReports({})
    void request<{clauses: {clause_id: string}[]; carryable?: string[]}>(`/rules/${ruleVersionId}/formalizations`, 'GET', undefined, controller.signal)
      .then(value => {if (!controller.signal.aborted) {setClauses(value.clauses.map(item => item.clause_id)); setCarryable(value.carryable ?? [])}})
      .catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [ruleVersionId, attempt])
  const total = clauses?.length ?? 0
  const confirmed = clauses?.filter(id => decided(reports[id]?.status)).length ?? 0
  const waiting = clauses?.filter(id => unprepared(reports[id]?.state)).length ?? 0
  const current = clauses?.includes(selected) ? selected : clauses?.[0] ?? ''
  const index = clauses?.indexOf(current) ?? -1
  // Once every clause has been read, open on the first one still waiting for the person.
  useEffect(() => {
    if (placed.current || !clauses?.length || clauses.some(id => !reports[id])) return
    placed.current = true
    setSelected(clauses.find(id => !decided(reports[id]?.status)) ?? clauses[0]!)
  }, [clauses, reports])
  function show(clauseId: string) {
    placed.current = true
    setSelected(clauseId)
    const top = detail.current?.getBoundingClientRect().top
    if (top !== undefined && top < 0) detail.current?.scrollIntoView({block: 'start'})
  }
  function next(after: string) {
    if (!clauses) return
    const from = clauses.indexOf(after)
    const rest = [...clauses.slice(from + 1), ...clauses.slice(0, from)]
    const target = rest.find(id => !decided(reports[id]?.status))
    if (target) show(target)
  }
  /** Unchanged clauses confirmed in the previous version are confirmed here in one step; each can still be withdrawn. */
  async function carryOver() {
    setCarrying(true); setError('')
    try {
      await request(`/rules/${ruleVersionId}/formalization-carry-overs`, 'POST', {operation_id: crypto.randomUUID()})
      setAttempt(value => value + 1)
    } catch (cause) {setError(message(cause))} finally {setCarrying(false)}
  }
  async function upload(file: File) {
    setUploading(true); setError('')
    try {
      const receipt = await uploadLocalVersion(file, crypto.randomUUID())
      if (receipt.state !== 'ready' || !receipt.file) throw new Error('这份文件还没传好，稍后再试或换一份。')
      setExample({name: receipt.file.original_filename, id: receipt.file.id})
    } catch (cause) {setError(message(cause))} finally {setUploading(false)}
  }
  async function start() {
    if (!example || starting) return
    setStarting(true); setError('')
    try {
      await request<Generation>(`/rules/${ruleVersionId}/checker-generations`, 'POST', {operation_id: operationId.current, file_version_id: example.id})
      onStarted()
    } catch (cause) {
      // A refused request changed nothing; any other failure is read back by the same operation.
      if ((cause as {status?: number}).status === 409) operationId.current = crypto.randomUUID()
      setError(message(cause))
    } finally {setStarting(false)}
  }
  const status = confirmed === 0 ? '至少确认一条，才能生成。' : !example ? '还要一份示例文件。' : confirmed < total ? `还有 ${total - confirmed} 条没确认，审核时这几条交给人判断。` : '都确认好了。'
  const currentStatus = reports[current]?.status
  const jumps = jumpOrder.map(kind => ({kind, ids: clauses?.filter(id => reports[id]?.status === kind) ?? []})).filter(item => item.ids.length)
  return <section aria-label="逐条确认" className="space-y-5">
    {readError && <div role="alert" className="flex flex-wrap items-center gap-3"><span>暂时读不到。{readError}</span><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></div>}
    {!clauses && !readError && <Skeleton className="h-64 w-full"/>}
    {carryable.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3"><span>上一版确认过的 {carryable.length} 条，原文没变。</span><Button disabled={carrying} onClick={() => void carryOver()}>沿用这 {carryable.length} 条</Button></div>}
    {waiting > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3"><span>{waiting} 条还没整理。</span><Button onClick={() => setPrepare(value => value + 1)}>开始整理</Button></div>}
    {jumps.length > 0 && <nav aria-label="按要做的事跳转" className="flex flex-wrap items-center gap-2"><span className="text-muted-foreground">先处理：</span>{jumps.map(({kind, ids}) => <Button key={kind} variant="outline" size="sm" onClick={() => show(ids.find(id => clauses!.indexOf(id) > index) ?? ids[0]!)}>{statusLabel[kind]} {ids.length}</Button>)}</nav>}
    {total > 0 && <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <nav aria-label="条款" className="space-y-3 self-start max-lg:hidden lg:sticky lg:top-6">
        <div className="flex items-center gap-3 px-3 text-muted-foreground"><span className="shrink-0 tabular-nums">已确认 {confirmed} / {total}</span><Progress aria-label="已确认的条款" value={confirmed * 100 / total}/></div>
        <ol className="max-h-[calc(100dvh-8rem)] space-y-0.5 overflow-y-auto">{clauses!.map((clauseId, position) => <li key={clauseId}>
          <button type="button" aria-current={clauseId === current ? 'step' : undefined} onClick={() => show(clauseId)}
            className={`flex w-full items-start gap-3 rounded-md px-3 py-2 text-left hover:bg-muted focus-visible:outline-ring ${clauseId === current ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}>
            <span className="pt-px text-xs tabular-nums">{String(position + 1).padStart(2, '0')}</span>
            <span className="line-clamp-2 min-w-0 flex-1 break-words">{reports[clauseId]?.text || ' '}</span>
            <span className="pt-0.5"><StatusMark status={reports[clauseId]?.status}/></span>
          </button>
        </li>)}</ol>
      </nav>
      <div ref={detail} className="min-w-0 scroll-mt-6 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="flex items-center gap-2 font-medium"><span>第 {index + 1} 条</span><span className="font-normal text-muted-foreground">共 {total} 条<span className="lg:hidden">，已确认 {confirmed} 条</span></span>{currentStatus && <Badge shape="square" variant={currentStatus === 'confirmed' ? 'success' : currentStatus === 'rejected' ? 'secondary' : currentStatus === 'review' || currentStatus === 'question' ? 'warning' : 'outline'}>{statusLabel[currentStatus]}</Badge>}</p>
          <div className="flex gap-1">
            <Button variant="ghost" size="icon" aria-label="上一条" disabled={index <= 0} onClick={() => show(clauses![index - 1]!)}><ChevronLeft/></Button>
            <Button variant="ghost" size="icon" aria-label="下一条" disabled={index >= total - 1} onClick={() => show(clauses![index + 1]!)}><ChevronRight/></Button>
          </div>
        </div>
        {clauses!.map(clauseId => <div key={`${ruleVersionId}:${clauseId}`} hidden={clauseId !== current}>
          <ClauseCheckPoints ruleVersionId={ruleVersionId} clauseId={clauseId} prepare={prepare} onAdopt={onAdopt} onConfirmed={() => next(clauseId)}
            onReport={report => setReports(previous => {const old = previous[clauseId]; return old && old.state === report.state && old.status === report.status && old.text === report.text ? previous : {...previous, [clauseId]: report}})}/>
        </div>)}
      </div>
    </div>}
    {total > 0 && <footer className="sticky bottom-0 z-10 -mx-6 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t bg-background/95 px-6 py-4 backdrop-blur-sm max-sm:-mx-4 max-sm:px-4">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        <input hidden ref={picker} type="file" aria-label="选择示例文件" onChange={event => {const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ''}}/>
        {example
          ? <span className="flex min-w-0 items-center gap-2"><FileText aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><span className="truncate">{example.name}</span><Button variant="ghost" size="sm" disabled={uploading || starting} onClick={() => picker.current?.click()}>{uploading && <Loader2 className="animate-spin motion-reduce:animate-none"/>}换一份</Button></span>
          : <Button variant="outline" disabled={uploading || starting} onClick={() => picker.current?.click()}>{uploading ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <FileUp/>}选择示例文件</Button>}
        {error ? <p role="alert" className="text-destructive">{error}</p> : <p role="status" className="text-muted-foreground">{status}</p>}
      </div>
      <Button disabled={confirmed === 0 || !example || uploading || starting} onClick={() => void start()}>{starting && <Loader2 className="animate-spin motion-reduce:animate-none"/>}{action}</Button>
    </footer>}
  </section>
}

function ClauseCheckPoints({ruleVersionId, clauseId, prepare, onAdopt, onReport, onConfirmed}: {ruleVersionId: string; clauseId: string; prepare: number; onAdopt: Adopt; onReport: (report: Report) => void; onConfirmed: () => void}) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [readError, setReadError] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState('')
  const [operation, setOperation] = useState<Receipt | null>(null)
  const [choice, setChoice] = useState(''), [choiceCandidate, setChoiceCandidate] = useState('')
  const [revision, setRevision] = useState(''), [revisionCandidate, setRevisionCandidate] = useState('')
  const [editing, setEditing] = useState(false), [original, setOriginal] = useState(false), [whole, setWhole] = useState(false)
  const [locked, setLocked] = useState(false)
  const [rejecting, setRejecting] = useState<'clause' | 'part' | null>(null)
  const [rejectPoints, setRejectPoints] = useState<string[]>([]), [rejectQuestions, setRejectQuestions] = useState<string[]>([]), [rejectParts, setRejectParts] = useState(''), [rejectReason, setRejectReason] = useState('')
  const busy = useRef(false)
  const prepared = useRef(prepare)
  const shown = useRef<Target>({ruleVersionId, clauseId})
  shown.current = {ruleVersionId, clauseId}
  const stillShown = (origin: Target) => sameFormalizationTarget(origin, shown.current)
  function begin() {
    if (busy.current) return false
    busy.current = true
    setLocked(true)
    return true
  }
  function finish() {
    busy.current = false
    setLocked(false)
  }
  useEffect(() => {
    const controller = new AbortController()
    setReadError('')
    void request<Detail>(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}`, 'GET', undefined, controller.signal).then(value => {
      if (controller.signal.aborted || value.rule_version_id !== ruleVersionId || value.clause_id !== clauseId) return
      setDetail(value)
      const open = value.operation
      if (open && running(open.state)) {setOperation(open); void follow(open)}
      else if (open && (open.state === 'unknown' || open.state === 'stopped' || open.state === 'failed')) setOperation(open)
      else setOperation(null)
    }).catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [ruleVersionId, clauseId, pending])
  // The page's "开始整理" asks each clause that has none; a clause still loading asks once it has read its state.
  useEffect(() => {
    if (prepare === prepared.current || !detail) return
    prepared.current = prepare
    if (unprepared(detail.state) && canRequestFormalization(detail.state, operation?.state)) void generate({})
  }, [prepare, detail])
  const readings = visibleFormalizationReadings(detail?.state ?? '', detail?.candidates ?? [])
  const candidate = readings.current
  const question = candidate?.unresolved[0]
  const confirmed = detail?.state === 'confirmed' || detail?.state === 'rejected'
  const rejections: Rejection[] = candidate?.rejections ?? []
  const wholeRejected = rejections.some(item => item.scope === 'clause')
  const waiting = running(operation?.state) || operation?.state === 'unknown'
  const selectedChoice = textForCandidate(candidate?.id, choiceCandidate, choice)
  const selectedRevision = textForCandidate(candidate?.id, revisionCandidate, revision)
  const incomplete = candidate && !question && candidate.completeness !== 'complete'
  async function generate(body: Record<string, unknown>) {
    if (!detail || waiting || !begin()) return
    const origin = {ruleVersionId, clauseId}
    setError('')
    try {
      const receipt = await request<Receipt>(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}/generations`, 'POST', {
        operation_id: crypto.randomUUID(), expected_source_digest: detail.source_rule_digest, ...body, consume_model: true,
      })
      if (!stillShown(origin) || !formalizationReceiptMatches(origin, receipt, receipt.operation_id)) return
      setOperation(receipt); setEditing(false)
      if (running(receipt.state)) await follow(receipt, origin)
      else if (receipt.state === 'completed') setPending(receipt.operation_id)
    } catch (cause) {if (stillShown(origin)) setError(message(cause))}
    finally {finish()}
  }
  async function follow(receipt: Receipt, origin = {ruleVersionId, clauseId}) {
    let current = receipt
    try {
      for (let attempt = 0; attempt < 180 && running(current.state); attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, attempt < 10 ? 500 : 1500))
        if (!stillShown(origin)) return
        current = await request<Receipt>(`/rule-formalization-generations/${receipt.operation_id}`, 'GET')
        if (!stillShown(origin) || !formalizationReceiptMatches(origin, current, receipt.operation_id)) return
        setOperation(current)
      }
    } catch (cause) {
      if (stillShown(origin)) setError(message(cause))
      return
    }
    if (current.state === 'completed' && stillShown(origin)) setPending(receipt.operation_id)
  }
  function reread(operationId: string) {
    const origin = {ruleVersionId, clauseId}
    void request<Receipt>(`/rule-formalization-generations/${operationId}`, 'GET').then(value => {
      if (!stillShown(origin) || !formalizationReceiptMatches(origin, value, operationId)) return
      setOperation(value)
      if (value.state === 'completed') setPending(value.operation_id)
      else if (running(value.state)) void follow(value, origin)
    }).catch(cause => {if (stillShown(origin)) setError(message(cause))})
  }
  async function confirm() {
    if (!detail || !candidate || !begin()) return
    const origin = {ruleVersionId, clauseId}
    setError('')
    try {
      const currentConfirmation = detail.confirmations.find(item => item.current)
      await request(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}/confirmations`, 'POST', {
        operation_id: crypto.randomUUID(),
        candidate_id: candidate.id,
        expected_candidate_revision: candidate.formalization_revision,
        expected_source_digest: detail.source_rule_digest,
        expected_display_digest: candidate.display_digest,
        ...(currentConfirmation ? {expected_current_confirmation_id: currentConfirmation.id} : {}),
      })
      if (!stillShown(origin)) return
      // A fresh key: the candidate just confirmed may be the one already loaded (a rejection), which would not reload.
      setPending(crypto.randomUUID())
      onConfirmed()
    } catch (cause) {
      if (stillShown(origin)) setError(message(cause))
    } finally {finish()}
  }
  /** No model call: the decision becomes a reading of its own, which the person then confirms. */
  async function reject() {
    if (!detail || !rejecting || !rejectReason.trim() || !begin()) return
    const origin = {ruleVersionId, clauseId}
    const latest = detail.candidates.at(-1)
    setError('')
    try {
      const parts = rejectParts.split(/\n/).map(item => item.trim()).filter(Boolean)
      const made = await request<{id: string}>(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}/rejections`, 'POST', {
        operation_id: crypto.randomUUID(), expected_source_digest: detail.source_rule_digest, scope: rejecting, reason: rejectReason.trim(), parts,
        ...(latest ? {based_on_candidate_id: latest.id} : {}),
        ...(rejecting === 'part' ? {obligation_ids: rejectPoints, unresolved_ids: rejectQuestions} : {}),
      })
      if (!stillShown(origin)) return
      setRejecting(null); setRejectPoints([]); setRejectQuestions([]); setRejectParts(''); setRejectReason('')
      setPending(made.id)
    } catch (cause) {if (stillShown(origin)) setError(message(cause))}
    finally {finish()}
  }
  async function revoke() {
    const current = detail?.confirmations.find(item => item.current)
    if (!current || !begin()) return
    const origin = {ruleVersionId, clauseId}
    setError('')
    try {
      await request(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}/confirmations/${current.id}/revocation`, 'POST')
      if (stillShown(origin)) setPending(crypto.randomUUID())
    } catch (cause) {if (stillShown(origin)) setError(message(cause))}
    finally {finish()}
  }
  const lines = candidate ? checkPointLines(candidate.cnl_text, (candidate.obligations as {id?: unknown}[]).flatMap(item => typeof item?.id === 'string' ? [item.id] : [])) : []
  // Ambiguities the original leaves open are listed apart from the points; nothing resolves them for the person.
  const points = lines.filter(line => !line.startsWith('待澄清：') && !line.startsWith(REJECTED)), review = lines.filter(line => line.startsWith('待澄清：')).map(line => line.slice('待澄清：'.length))
  const questionChoices = (candidate?.unresolved ?? []).filter(item => item.id !== 'undecided')
  const pointChoices = ((candidate?.obligations ?? []) as {id?: unknown; text?: unknown}[]).flatMap(item => typeof item?.id === 'string' && typeof item.text === 'string' ? [{id: item.id, text: item.text}] : [])
  const suggestion = ((candidate?.assumptions ?? []) as {kind?: unknown; text?: unknown}[]).find(item => item?.kind === 'suggested_clause' && typeof item.text === 'string')?.text as string | undefined
  const clauseText = detail?.clause_text ?? ''
  const failed = !candidate && ((operation?.state === 'failed' && operation.failure !== 'source_changed') || operation?.state === 'stopped' || operation?.state === 'unknown')
  const status: Status | null = !detail ? null : running(operation?.state) ? 'running' : confirmed ? (detail.state === 'rejected' ? 'rejected' : 'confirmed') : failed ? 'failed' : !candidate ? 'waiting' : question ? 'question' : review.length ? 'review' : 'ready'
  useEffect(() => {if (detail && status) onReport({state: detail.state, status, text: clauseText})}, [detail?.state, status, clauseText])
  const parts = suggestion ? insertions(clauseText, suggestion) : []
  const longOriginal = clauseText.length > 360
  return <article aria-label={`条款 ${clauseId}`} className="space-y-6 rounded-lg border bg-card px-5 py-5 sm:px-6">
    <section className="space-y-2">
      <h3 className="font-medium">原文</h3>
      {detail ? <p className={`whitespace-pre-wrap break-words leading-relaxed ${longOriginal && !original ? 'line-clamp-6' : ''}`}>{clauseText}</p> : !readError && <Skeleton className="h-4 w-3/4"/>}
      {longOriginal && <button type="button" className="text-muted-foreground underline underline-offset-4 hover:text-foreground" aria-expanded={original} onClick={() => setOriginal(value => !value)}>{original ? '收起' : '展开'}</button>}
    </section>
    {readError && <div role="alert" className="flex flex-wrap items-center gap-3"><span>暂时读不到。{readError}</span><Button variant="outline" size="sm" onClick={() => setPending(crypto.randomUUID())}>重新读取</Button></div>}
    {running(operation?.state) ? <div role="status" className="space-y-2.5"><p className="flex items-center gap-2 text-muted-foreground"><Loader2 aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none"/>正在整理，大约一两分钟。</p><Skeleton className="h-4 w-full"/><Skeleton className="h-4 w-2/3"/></div>
      : operation?.state === 'unknown' ? <div role="status" className="flex flex-wrap items-center gap-3"><span>还不知道整理结果。刷新看看，不要重新开始。</span><Button variant="outline" size="sm" onClick={() => reread(operation.operation_id)}>刷新</Button></div>
      : (operation?.state === 'failed' && operation.failure !== 'source_changed') || operation?.state === 'stopped' ? <div role="status" className="flex flex-wrap items-center gap-3"><span>{operation.state === 'stopped' ? '这条没整理出来。' : formalizationFailure(operation.failure)}</span><Button variant="outline" size="sm" disabled={locked} onClick={() => void generate({})}>再试一次</Button></div>
      : detail && !candidate && !error && unprepared(detail.state) ? <div className="flex flex-wrap items-center gap-3"><span className="text-muted-foreground">还没整理。</span><Button variant="outline" size="sm" disabled={locked} onClick={() => void generate({})}>整理这条</Button>{!rejecting && <Button variant="ghost" size="sm" disabled={locked} onClick={() => setRejecting('clause')}>没有判定标准</Button>}</div>
      : null}
    {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-destructive"><span>{error}</span>{!candidate && detail && <Button variant="outline" size="sm" disabled={locked} onClick={() => void generate({})}>再试一次</Button>}{candidate && <Button variant="outline" size="sm" onClick={() => {setError(''); setPending(crypto.randomUUID())}}>重新读取</Button>}</div>}
    {candidate && !waiting && !question && <>
      {review.length > 0 && <section className="space-y-3 rounded-md border border-warning/30 bg-warning/5 px-4 py-4">
        <h3 className="font-medium">原文有 {review.length} 处没说清</h3>
        <ul className="list-disc space-y-1.5 pl-5">{review.map((item, position) => <li key={position} className="break-words">{item}</li>)}</ul>
        {suggestion && <div className="space-y-2 border-t border-warning/20 pt-3">
          <h4 className="font-medium">建议这样改</h4>
          <Changes parts={whole ? parts : changesOnly(parts)}/>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <button type="button" className="text-muted-foreground underline underline-offset-4 hover:text-foreground" aria-expanded={whole} onClick={() => setWhole(value => !value)}>{whole ? '只看改动' : '看改后全文'}</button>
            <Button variant="outline" size="sm" onClick={() => onAdopt(clauseId, suggestion, review.join('；').slice(0, 300))}>用建议改规则</Button>
          </div>
        </div>}
      </section>}
      {rejections.length > 0 && <section aria-label="已拒绝" className="space-y-2 rounded-md border bg-muted/40 px-4 py-4">
        <h3 className="font-medium">{wholeRejected ? '整条已拒绝：没有可程序化的判定标准' : '以下部分已拒绝：没有可程序化的判定标准'}</h3>
        <ul className="list-disc space-y-1.5 pl-5">{rejections.map(item => <li key={item.id} className="break-words">{item.parts.length ? item.parts.join('；') : '整条条款'}<span className="block text-muted-foreground">原因：{item.reason}</span></li>)}</ul>
        <p className="text-muted-foreground">审核时{wholeRejected ? '这条' : '这些部分'}不生成检查、不调用模型，结果固定为无法判断，交给人工复核。</p>
      </section>}
      {points.length > 0 && <section className="space-y-3">
        <h3 className="font-medium">检查步骤</h3>
        <div className="space-y-4">{groupPoints(points).map((group, position) => <div key={position} className="space-y-1.5">
          {group.title && <h4 className="text-muted-foreground">{group.title}</h4>}
          <ul className="list-disc space-y-1.5 pl-5 leading-relaxed">{group.items.map((point, item) => <li key={item} className="break-words">{point.replace(/^【[^】]{1,12}】\s*/, '')}</li>)}</ul>
        </div>)}</div>
      </section>}
      {incomplete && <p role="status" className="text-muted-foreground">这条还不完整。</p>}
      {(editing || incomplete) && <div className="grid gap-2">
        <label className="grid gap-1.5">哪里写得不对？<Textarea className="min-h-20" maxLength={2000} value={selectedRevision} onChange={event => {setRevisionCandidate(candidate.id); setRevision(event.target.value)}}/></label>
        <div className="flex justify-end gap-2">{!incomplete && <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>取消</Button>}<Button variant="outline" size="sm" disabled={!selectedRevision.trim() || locked} onClick={() => void generate({based_on_candidate_id: candidate.id, user_instruction: selectedRevision.trim()})}>重新整理</Button></div>
      </div>}
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
        {!editing && !incomplete && !confirmed && !rejecting ? <span className="flex flex-wrap gap-2">{!wholeRejected && <Button variant="outline" disabled={locked} onClick={() => setEditing(true)}>写得不对</Button>}{!wholeRejected && <Button variant="ghost" disabled={locked} onClick={() => setRejecting('part')}>没有判定标准</Button>}</span> : <span/>}
        {confirmed ? <Button variant="outline" disabled={locked} onClick={() => void revoke()}>撤销确认</Button> : <Button disabled={locked || incomplete || candidate.unresolved.length > 0} onClick={() => void confirm()}>{wholeRejected ? '确认拒绝' : review.length ? '保持原文，确认' : '确认'}</Button>}
      </footer>
    </>}
    {question && !waiting && <fieldset className="space-y-3"><legend className="mb-3 font-medium">{question.reason}</legend>
      <RadioGroup value={selectedChoice} onValueChange={value => {setChoiceCandidate(candidate?.id ?? ''); setChoice(value)}}>
        {(question.choices ?? []).map(item => <label key={item.id} className="flex min-h-11 items-start gap-3 rounded-md border px-3 py-3"><RadioGroupItem value={item.id} className="mt-0.5"/><span>{item.summary}{item.counterexample && <span className="mt-1 block text-xs text-muted-foreground">反例：{item.counterexample}</span>}</span></label>)}
      </RadioGroup>
      <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
        {!rejecting && <Button variant="ghost" disabled={locked} onClick={() => setRejecting('clause')}>没有判定标准</Button>}
        <Button variant="ghost" disabled={locked} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: 'undecided'}})}>暂不确定</Button>
        <Button disabled={!selectedChoice || locked} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: selectedChoice}})}>确认选择</Button>
      </div>
    </fieldset>}
    {rejecting && !waiting && <section aria-label="拒绝" className="space-y-3 border-t pt-4">
      <h3 className="font-medium">拒绝：原文没有可程序化的判定标准</h3>
      {pointChoices.length > 0 && <RadioGroup value={rejecting} onValueChange={value => setRejecting(value as 'clause' | 'part')}>
        <label className="flex items-center gap-3"><RadioGroupItem value="part"/>只拒绝其中一部分，其余照常检查</label>
        <label className="flex items-center gap-3"><RadioGroupItem value="clause"/>整条拒绝</label>
      </RadioGroup>}
      {rejecting === 'part' && <fieldset className="space-y-2"><legend className="mb-2 text-muted-foreground">勾选没有判定标准的要点</legend>
        {pointChoices.map(item => <label key={item.id} className="flex items-start gap-3"><Checkbox className="mt-0.5" checked={rejectPoints.includes(item.id)} onCheckedChange={value => setRejectPoints(previous => value === true ? [...previous, item.id] : previous.filter(id => id !== item.id))}/><span className="break-words">{item.text}</span></label>)}
        {questionChoices.map(item => <label key={item.id} className="flex items-start gap-3"><Checkbox className="mt-0.5" checked={rejectQuestions.includes(item.id)} onCheckedChange={value => setRejectQuestions(previous => value === true ? [...previous, item.id] : previous.filter(id => id !== item.id))}/><span className="break-words">待选择：{item.reason}</span></label>)}
      </fieldset>}
      <label className="grid gap-1.5">{rejecting === 'part' ? '要点里没有单列的部分（每行一项，可不填）' : '拒绝的内容（可不填，默认整条）'}<Textarea className="min-h-16" maxLength={3000} value={rejectParts} onChange={event => setRejectParts(event.target.value)}/></label>
      <label className="grid gap-1.5">原因：哪些地方没有判定标准<Textarea className="min-h-20" maxLength={1000} value={rejectReason} onChange={event => setRejectReason(event.target.value)}/></label>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => setRejecting(null)}>取消</Button>
        <Button variant="outline" size="sm" disabled={!rejectReason.trim() || locked || (rejecting === 'part' && !rejectPoints.length && !rejectQuestions.length && !rejectParts.trim())} onClick={() => void reject()}>拒绝</Button>
      </div>
    </section>}
  </article>
}
