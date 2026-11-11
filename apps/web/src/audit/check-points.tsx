// Stitch: projects/13261964020862199279/screens/0efd7637e4a343919cd039650cdf6847.
import {useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowLeft, Check, FileUp, Loader2} from 'lucide-react'
import {request, uploadLocalVersion} from '../api'
import type {components} from '../generated/openapi'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Progress} from '../components/ui/progress'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Skeleton} from '../components/ui/skeleton'
import {Textarea} from '../components/ui/textarea'
import {message} from '../journey/remote'
import {ProgramStatus, StatusCard} from './program-status'
import {canRequestFormalization, checkPointLines, formalizationFailure, formalizationReceiptMatches, sameFormalizationTarget, textForCandidate, visibleFormalizationReadings} from '../journey/requirement-formalization-contract'

type Detail = components['schemas']['ClauseFormalizationView']
type Receipt = components['schemas']['OperationReceipt']
type Generation = components['schemas']['OptimizationReceipt']
type GenerationView = components['schemas']['GenerationView']
type Target = {ruleVersionId: string; clauseId: string}
type Adopt = (clauseId: string, text: string, note: string) => void

const running = (state?: string) => state === 'queued' || state === 'running'
const unprepared = (state?: string) => state === 'not_started' || state === 'source_changed'

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
  if (view.program !== 'missing' && view.suite_key) {
    const fresh = operation?.state === 'completed' && operation.candidate_revision_id && operation.candidate_revision_id !== view.current_revision_id ? operation.candidate_revision_id : null
    return <ProgramStatus suiteKey={view.suite_key} offered={fresh} onUpdate={working ? undefined : () => onPrepare(true)} extra={working} onChanged={reload}/>
  }
  if (working) return <StatusCard badge="生成中" title="正在生成检查程序">{working}</StatusCard>
  return <StatusCard badge="未生成" title="还不能用于审核" action={<Button onClick={() => onPrepare(false)}>生成检查程序</Button>}>
    <p>生成检查程序后，审核时才能选这条规则。需要你逐条确认，并给一份示例文件。</p>
    {operation && (operation.state === 'failed' || operation.state === 'stopped') && <p className="text-foreground">上次生成没有完成：{generationFailure(operation)}</p>}
    {operation?.state === 'completed' && <p className="text-foreground">上次生成期间规则改过，没有启用。请重新生成。</p>}
  </StatusCard>
}

/** The full-page steps that make or update an uploaded rule's program. */
export function ProgramWizard({ruleVersionId, updating, onClose, onAdopt}: {ruleVersionId: string; updating: boolean; onClose: () => void; onAdopt: Adopt}) {
  const title = updating ? '更新检查程序' : '生成检查程序'
  return <div className="space-y-6">
    <div className="space-y-2">
      <Button variant="ghost" size="sm" className="-ml-2" onClick={onClose}><ArrowLeft/>返回规则</Button>
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
    </div>
    <CheckPoints ruleVersionId={ruleVersionId} action={title} onStarted={onClose} onCancel={onClose} onAdopt={onAdopt}/>
  </div>
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

/** A step of the wizard: its number until it is done, then a check. */
function StepTitle({step, done, id, children}: {step: number; done: boolean; id?: string; children: ReactNode}) {
  return <h3 id={id} className="flex items-center gap-2.5 text-lg font-semibold">
    <span aria-hidden="true" className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs ${done ? 'bg-success text-background' : 'bg-foreground text-background'}`}>{done ? <Check className="size-3.5"/> : step}</span>
    <span>{children}{done && <span className="sr-only">（已完成）</span>}</span>
  </h3>
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

/** Every clause is prepared on request and confirmed one by one; the example file and the final start belong to the same step. */
function CheckPoints({ruleVersionId, action, onStarted, onCancel, onAdopt}: {ruleVersionId: string; action: string; onStarted: () => void; onCancel: () => void; onAdopt: Adopt}) {
  const [clauses, setClauses] = useState<string[] | null>(null)
  const [readError, setReadError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [states, setStates] = useState<Record<string, string>>({})
  const [prepare, setPrepare] = useState(0)
  const [example, setExample] = useState<{name: string; id: string} | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const operationId = useRef(crypto.randomUUID())
  const picker = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    setClauses(null); setReadError(''); setStates({})
    void request<{clauses: {clause_id: string}[]}>(`/rules/${ruleVersionId}/formalizations`, 'GET', undefined, controller.signal)
      .then(value => {if (!controller.signal.aborted) setClauses(value.clauses.map(item => item.clause_id))})
      .catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [ruleVersionId, attempt])
  const confirmed = clauses?.filter(id => states[id] === 'confirmed').length ?? 0
  const total = clauses?.length ?? 0
  const waiting = clauses?.filter(id => unprepared(states[id])).length ?? 0
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
  return <section aria-labelledby="check-points-title" className="space-y-5">
    <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div className="min-w-0 space-y-1.5">
        <StepTitle id="check-points-title" step={1} done={total > 0 && confirmed === total}>逐条确认</StepTitle>
        <p className="text-muted-foreground">系统把每条规则整理成检查步骤。看一遍，没问题就确认。</p>
      </div>
      {total > 0 && <div className="flex w-48 items-center gap-3 text-sm text-muted-foreground"><span className="shrink-0 tabular-nums">已确认 {confirmed} / {total}</span><Progress aria-label="已确认的条款" value={confirmed * 100 / total}/></div>}
    </header>
    {readError && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm"><span>暂时读不到。{readError}</span><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></div>}
    {!clauses && !readError && <Skeleton className="h-32 w-full"/>}
    {waiting > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm"><span>{waiting} 条还没整理。</span><Button onClick={() => setPrepare(value => value + 1)}>开始整理</Button></div>}
    {clauses?.map((clauseId, index) => <ClauseCheckPoints key={`${ruleVersionId}:${clauseId}`} number={index + 1} ruleVersionId={ruleVersionId} clauseId={clauseId} prepare={prepare} onAdopt={onAdopt}
      onState={state => setStates(previous => previous[clauseId] === state ? previous : {...previous, [clauseId]: state})}/>)}
    {total > 0 && <footer className="space-y-4 border-t pt-5 text-sm">
      <div className="space-y-2">
        <StepTitle step={2} done={Boolean(example)}>示例文件</StepTitle>
        <p className="text-muted-foreground">选一份这类文件，用来试运行检查程序。不需要标答案。</p>
        <input hidden ref={picker} type="file" aria-label="选择示例文件" onChange={event => {const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ''}}/>
        <div className="flex flex-wrap items-center gap-3"><Button variant="outline" disabled={uploading || starting} onClick={() => picker.current?.click()}>{uploading ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <FileUp/>}{example ? '换一份' : '选择文件'}</Button>{example && <span className="break-words">{example.name}</span>}</div>
      </div>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      <StepTitle step={3} done={false}>生成</StepTitle>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p role="status" className="text-muted-foreground">{confirmed === 0 ? '至少确认一条，才能生成。' : confirmed < total ? `还有 ${total - confirmed} 条没确认，审核时这几条交给人判断。` : '都确认好了。'}</p>
        <div className="flex gap-2"><Button variant="ghost" disabled={starting} onClick={onCancel}>取消</Button><Button disabled={confirmed === 0 || !example || uploading || starting} onClick={() => void start()}>{starting && <Loader2 className="animate-spin motion-reduce:animate-none"/>}{action}</Button></div>
      </div>
    </footer>}
  </section>
}

function ClauseCheckPoints({number, ruleVersionId, clauseId, prepare, onAdopt, onState}: {number: number; ruleVersionId: string; clauseId: string; prepare: number; onAdopt: Adopt; onState: (state: string) => void}) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [readError, setReadError] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState('')
  const [operation, setOperation] = useState<Receipt | null>(null)
  const [choice, setChoice] = useState(''), [choiceCandidate, setChoiceCandidate] = useState('')
  const [revision, setRevision] = useState(''), [revisionCandidate, setRevisionCandidate] = useState('')
  const [editing, setEditing] = useState(false), [reopened, setReopened] = useState(false), [original, setOriginal] = useState(false)
  const [locked, setLocked] = useState(false)
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
      onState(value.state)
      const open = value.operation
      if (open && running(open.state)) {setOperation(open); void follow(open)}
      else if (open && (open.state === 'unknown' || open.state === 'stopped' || open.state === 'failed')) setOperation(open)
      else setOperation(null)
    }).catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [ruleVersionId, clauseId, pending])
  // The page's "整理检查要点" asks each clause that has none; a clause still loading asks once it has read its state.
  useEffect(() => {
    if (prepare === prepared.current || !detail) return
    prepared.current = prepare
    if (unprepared(detail.state) && canRequestFormalization(detail.state, operation?.state)) void generate({})
  }, [prepare, detail])
  const readings = visibleFormalizationReadings(detail?.state ?? '', detail?.candidates ?? [])
  const candidate = readings.current
  const question = candidate?.unresolved[0]
  const confirmed = detail?.state === 'confirmed'
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
      setReopened(false)
      setPending(candidate.id)
    } catch (cause) {
      if (stillShown(origin)) setError(message(cause))
    } finally {finish()}
  }
  const lines = candidate ? checkPointLines(candidate.cnl_text, (candidate.obligations as {id?: unknown}[]).flatMap(item => typeof item?.id === 'string' ? [item.id] : [])) : []
  // Ambiguities the original leaves open are listed apart from the points; nothing resolves them for the person.
  const points = lines.filter(line => !line.startsWith('待澄清：')), review = lines.filter(line => line.startsWith('待澄清：')).map(line => line.slice('待澄清：'.length))
  const suggestion = ((candidate?.assumptions ?? []) as {kind?: unknown; text?: unknown}[]).find(item => item?.kind === 'suggested_clause' && typeof item.text === 'string')?.text as string | undefined
  const clauseText = detail?.clause_text ?? ''
  const numberLabel = String(number).padStart(2, '0')
  const consequence = review.length > 0 && <p className="text-xs text-muted-foreground">保持原文时，没说清的情形在审核时交给人判断。</p>
  if (confirmed && !reopened && !waiting && !error) return <article aria-label={`条款 ${clauseId}`} className="flex flex-wrap items-start gap-x-4 gap-y-2 rounded-lg border bg-muted/30 px-4 py-4 sm:px-5">
    <span aria-hidden="true" className="pt-0.5 text-xs tabular-nums text-muted-foreground">{numberLabel}</span>
    <div className="min-w-0 flex-1 space-y-1">
      <p className="flex flex-wrap items-center gap-2"><span className="min-w-0 break-words line-clamp-1">{clauseText}</span><Badge shape="square" variant="success">已确认</Badge></p>
      {review.length > 0 && <p className="text-sm text-muted-foreground">有 {review.length} 处没说清，审核时交给人判断。</p>}
    </div>
    <Button variant="ghost" size="sm" onClick={() => setReopened(true)}>查看</Button>
  </article>
  return <article aria-label={`条款 ${clauseId}`} className="space-y-4 rounded-lg border bg-card px-4 py-4 sm:px-5">
    <header className="flex items-start gap-4">
      <span aria-hidden="true" className="pt-0.5 text-xs tabular-nums text-muted-foreground">{numberLabel}</span>
      <div className="min-w-0 flex-1 space-y-1 text-sm text-muted-foreground">
        {detail ? <p className={`whitespace-pre-wrap break-words leading-relaxed ${original ? '' : 'line-clamp-2'}`}>{clauseText}</p> : !readError && <Skeleton className="h-4 w-3/4"/>}
        {clauseText.length > 80 && <button type="button" className="underline underline-offset-4" aria-expanded={original} onClick={() => setOriginal(value => !value)}>{original ? '收起' : '展开原文'}</button>}
      </div>
      {confirmed && <Badge shape="square" variant="success">已确认</Badge>}
    </header>
    {readError && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm"><span>暂时读不到。{readError}</span><Button variant="outline" size="sm" onClick={() => setPending(crypto.randomUUID())}>重新读取</Button></div>}
    {running(operation?.state) ? <div role="status" className="space-y-2.5"><p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none"/>正在整理…</p><Skeleton className="h-4 w-full"/><Skeleton className="h-4 w-2/3"/></div>
      : operation?.state === 'unknown' ? <div role="status" className="flex flex-wrap items-center gap-3 text-sm"><span>还不知道整理结果。刷新看看，不要重新开始。</span><Button variant="outline" size="sm" onClick={() => reread(operation.operation_id)}>刷新</Button></div>
      : (operation?.state === 'failed' && operation.failure !== 'source_changed') || operation?.state === 'stopped' ? <div role="status" className="flex flex-wrap items-center gap-3 text-sm"><span>{operation.state === 'stopped' ? '这条没整理出来。' : formalizationFailure(operation.failure)}</span><Button variant="outline" size="sm" disabled={locked} onClick={() => void generate({})}>再试一次</Button></div>
      : detail && !candidate && !error && unprepared(detail.state) ? <p className="text-sm text-muted-foreground">还没整理。</p>
      : null}
    {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive"><span>{error}</span>{!candidate && detail && <Button variant="outline" size="sm" disabled={locked} onClick={() => void generate({})}>再试一次</Button>}{candidate && <Button variant="outline" size="sm" onClick={() => {setError(''); setPending(crypto.randomUUID())}}>重新读取</Button>}</div>}
    {candidate && !waiting && !question && <>
      <section className="space-y-2.5">
        <h3 className="text-sm text-muted-foreground">系统会这样检查</h3>
        <ul className="list-disc space-y-1.5 pl-5 leading-relaxed">{points.map((point, index) => <li key={index} className="break-words">{point.replace(/^【[^】]{1,12}】\s*/, '')}</li>)}</ul>
      </section>
      {review.length > 0 && <section className="space-y-3 rounded-md border border-warning/30 bg-warning/5 px-4 py-3">
        <h3 className="font-medium">原文有 {review.length} 处没说清</h3>
        <ul className="list-disc space-y-1.5 pl-5 text-sm">{review.map((item, index) => <li key={index} className="break-words">{item}</li>)}</ul>
        {suggestion && <div className="space-y-1 border-t border-warning/20 pt-3 text-sm"><p className="text-muted-foreground">建议把这条改成（高亮为新增）</p><p className="whitespace-pre-wrap break-words">{insertions(clauseText, suggestion).map((part, index) => part.added ? <mark key={index} className="rounded-sm bg-success/15 px-0.5 text-foreground">{part.text}</mark> : <span key={index}>{part.text}</span>)}</p></div>}
      </section>}
      {incomplete && <p role="status" className="text-sm text-muted-foreground">这条还不完整。告诉系统哪里不对，再整理一次。</p>}
      {(editing || incomplete) && <div className="grid gap-2">
        <label className="grid gap-1.5 text-sm">哪里写得不对？<Textarea className="min-h-20" maxLength={2000} value={selectedRevision} onChange={event => {setRevisionCandidate(candidate.id); setRevision(event.target.value)}}/></label>
        <div className="flex justify-end gap-2">{!incomplete && <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>取消</Button>}<Button variant="outline" size="sm" disabled={!selectedRevision.trim() || locked} onClick={() => void generate({based_on_candidate_id: candidate.id, user_instruction: selectedRevision.trim()})}>重新整理</Button></div>
      </div>}
      <footer className="space-y-2 border-t pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          {!editing && !incomplete ? <Button variant="ghost" size="sm" disabled={locked} onClick={() => setEditing(true)}>写得不对？</Button> : <span/>}
          <div className="flex flex-wrap gap-2">
            {review.length > 0 && suggestion && !confirmed && <Button variant="outline" onClick={() => onAdopt(clauseId, suggestion, review.join('；').slice(0, 300))}>用建议改规则</Button>}
            {confirmed ? <Button variant="outline" onClick={() => setReopened(false)}>收起</Button> : <Button disabled={locked || incomplete || candidate.unresolved.length > 0} onClick={() => void confirm()}>{review.length ? '保持原文，确认' : '确认'}</Button>}
          </div>
        </div>
        {!confirmed && consequence}
      </footer>
    </>}
    {question && !waiting && <fieldset className="space-y-3"><legend className="mb-3 font-medium">{question.reason}</legend>
      <RadioGroup value={selectedChoice} onValueChange={value => {setChoiceCandidate(candidate?.id ?? ''); setChoice(value)}}>
        {(question.choices ?? []).map(item => <label key={item.id} className="flex min-h-11 items-start gap-3 rounded-md border px-3 py-3"><RadioGroupItem value={item.id} className="mt-0.5"/><span>{item.summary}{item.counterexample && <span className="mt-1 block text-xs text-muted-foreground">反例：{item.counterexample}</span>}</span></label>)}
      </RadioGroup>
      <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
        <Button variant="ghost" disabled={locked} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: 'undecided'}})}>暂不确定</Button>
        <Button disabled={!selectedChoice || locked} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: selectedChoice}})}>确认选择</Button>
      </div>
    </fieldset>}
  </article>
}
