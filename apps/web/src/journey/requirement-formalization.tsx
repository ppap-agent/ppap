import {useEffect, useRef, useState} from 'react'
import {knownRequestMessage, request} from '../api'
import type {components} from '../generated/openapi.ts'
import {meaningChangedWithoutText} from '../audit/rule-impact-state.ts'
import {canRequestFormalization, checkerAvailability, checkerAvailabilityCopy, confirmedReadingAside, formalizationFailure, formalizationReceiptMatches, sameFormalizationTarget, textForCandidate, visibleFormalizationReadings, type CheckerValidation} from './requirement-formalization-contract.ts'
import {Loader2} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Textarea} from '../components/ui/textarea'
import {message} from './remote'

type Detail = components['schemas']['ClauseFormalizationView']
type Receipt = components['schemas']['OperationReceipt']

const stateCopy: Record<string, string> = {
  not_started: '系统还没有解释这条规则。',
  source_changed: '原文或范围已变化，需要重新解释。',
  candidate: '请核对这段理解，无误后确认。',
  needs_clarification: '还有一个会影响判定的问题，回答后才能确认。',
  partial: '这段理解还不完整，不能确认。',
  confirmed: '已确认。这只确认规则含义，检查还需单独验证。',
}

/** One clause's reading. It stays one line until there is something to read or decide; checker status is said once per rule by RuleCheckerStatus. */
export function ClauseFormalization({ruleVersionId, clauseId, suiteRevisionId, sourceVerified, explainAll = 0, modelNote = true}: {ruleVersionId: string; clauseId: string; suiteRevisionId?: string; sourceVerified: boolean; explainAll?: number; modelNote?: boolean}) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  const [readError, setReadError] = useState('')
  const [pending, setPending] = useState('')
  const [operation, setOperation] = useState<Receipt | null>(null)
  const [choice, setChoice] = useState(''), [choiceCandidate, setChoiceCandidate] = useState('')
  const [revision, setRevision] = useState(''), [revisionCandidate, setRevisionCandidate] = useState('')
  const [handoff, setHandoff] = useState<{id: string; state: 'sending' | 'saved' | 'unknown'; meaningChanged: boolean} | null>(null)
  const [conflict, setConflict] = useState(false)
  const [copied, setCopied] = useState(false)
  const [locked, setLocked] = useState(false)
  const busy = useRef(false)
  const shown = useRef({ruleVersionId, clauseId})
  shown.current = {ruleVersionId, clauseId}
  function stillShown(origin: {ruleVersionId: string; clauseId: string}) {
    return sameFormalizationTarget(origin, shown.current)
  }
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
    setError(''); setHandoff(null); setChoice(''); setChoiceCandidate(''); setRevision(''); setRevisionCandidate(''); setConflict(false)
  }, [ruleVersionId, clauseId])
  useEffect(() => {
    const controller = new AbortController()
    setDetail(null); setReadError(''); setOperation(null); setRevision(''); setConflict(false); setCopied(false)
    void request<Detail>(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}`, 'GET', undefined, controller.signal).then(value => {
      if (controller.signal.aborted || value.rule_version_id !== ruleVersionId || value.clause_id !== clauseId) return
      setDetail(value)
      const open = value.operation
      if (open && (open.state === 'queued' || open.state === 'running')) {setOperation(open); void follow(open)}
      else if (open && (open.state === 'unknown' || open.state === 'stopped' || open.state === 'failed')) setOperation(open)
    }).catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [ruleVersionId, clauseId, pending])
  const readings = visibleFormalizationReadings(detail?.state ?? '', detail?.candidates ?? [])
  const reasonId = `formal-reason-${clauseId.replace(/[^A-Za-z0-9_-]/g, '-')}`
  const sourceReasonId = `formal-source-${clauseId.replace(/[^A-Za-z0-9_-]/g, '-')}`
  const candidate = readings.current
  const previous = readings.previous
  const historical = readings.historical
  const question = candidate?.unresolved[0]
  const confirmedAside = confirmedReadingAside(detail?.confirmations.find(item => item.current)?.candidate_id, detail?.candidates ?? [], [candidate, previous, historical].flatMap(item => item ? [item] : []))
  const selectedChoice = textForCandidate(candidate?.id, choiceCandidate, choice)
  const selectedRevision = textForCandidate(candidate?.id, revisionCandidate, revision)
  const confirmed = detail?.state === 'confirmed'
  const describedBy = !detail ? undefined : sourceVerified || confirmed ? reasonId : `${reasonId} ${sourceReasonId}`
  const waiting = operation?.state === 'queued' || operation?.state === 'running' || operation?.state === 'unknown'
  const blocked = !sourceVerified || !candidate || candidate.completeness !== 'complete' || candidate.unresolved.length > 0 || waiting
  async function generate(body: Record<string, unknown>) {
    if (!detail || operation?.state === 'queued' || operation?.state === 'running' || operation?.state === 'unknown' || !begin()) return
    const origin = {ruleVersionId, clauseId}
    setError('')
    try {
      const receipt = await request<Receipt>(`/rules/${ruleVersionId}/formalizations/${encodeURIComponent(clauseId)}/generations`, 'POST', {
        operation_id: crypto.randomUUID(), expected_source_digest: detail.source_rule_digest, ...body, consume_model: true,
      })
      if (!stillShown(origin) || !formalizationReceiptMatches(origin, receipt, receipt.operation_id)) return
      setOperation(receipt)
      if (receipt.state === 'queued' || receipt.state === 'running') await follow(receipt, origin)
      else if (receipt.state === 'completed') setPending(receipt.operation_id)
    } catch (cause) {if (stillShown(origin)) setError(message(cause))}
    finally {finish()}
  }
  async function follow(receipt: Receipt, origin = {ruleVersionId, clauseId}) {
    let current = receipt
    try {
      for (let attempt = 0; attempt < 20 && (current.state === 'queued' || current.state === 'running'); attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 400))
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
  async function confirm() {
    if (!sourceVerified || !detail || !candidate || !begin()) return
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
      setConflict(false)
      setPending(candidate.id)
    } catch (cause) {
      if (!stillShown(origin)) return
      const status = cause instanceof Error && 'status' in cause ? Number(cause.status) : 0
      setConflict(status === 409)
      setError(message(cause))
    } finally {finish()}
  }
  async function materialize(replay = false) {
    if (!suiteRevisionId || !confirmed || handoff?.state === 'sending' || handoff?.state === 'saved' || !begin()) return
    const origin = {ruleVersionId, clauseId}
    const operationId = replay && handoff ? handoff.id : crypto.randomUUID()
    setError('')
    setHandoff({id: operationId, state: 'sending', meaningChanged: false})
    try {
      const saved = await request<{rule_impact?: {changed: boolean; appendix_changed?: boolean; clauses?: {kind: string}[]}}>(`/rules/${ruleVersionId}/formalization-suite-candidates`, 'POST', {operation_id: operationId, suite_revision_id: suiteRevisionId})
      if (!stillShown(origin)) return
      setHandoff({id: operationId, state: 'saved', meaningChanged: Boolean(saved.rule_impact && meaningChangedWithoutText(saved.rule_impact))})
    } catch (cause) {
      if (!stillShown(origin)) return
      const status = (cause as {status?: number}).status
      if (status && status < 500) {setError(message(cause)); setHandoff(null); return}
      setHandoff({id: operationId, state: 'unknown', meaningChanged: false})
    } finally {finish()}
  }
  function copySpec() {
    if (!candidate) return
    const origin = {ruleVersionId, clauseId}
    const text = `${candidate.owl_content}\n${candidate.clif_content ?? ''}`
    void navigator.clipboard.writeText(text).then(() => {
      if (!stillShown(origin)) return
      setCopied(true)
      setError('')
    }).catch(() => {if (stillShown(origin)) setError('没有复制。请展开后手动选择。')})
  }
  function reread(operationId: string) {
    const origin = {ruleVersionId, clauseId}
    void request<Receipt>(`/rule-formalization-generations/${operationId}`, 'GET').then(value => {
      if (!stillShown(origin) || !formalizationReceiptMatches(origin, value, operationId)) return
      setOperation(value)
      if (value.state === 'completed') setPending(value.operation_id)
    }).catch(cause => {if (stillShown(origin)) setError(message(cause))})
  }
  // "Explain all" on the rule asks each clause that has not started; a clause still loading asks once it has read its state.
  const explained = useRef(explainAll)
  useEffect(() => {
    if (explainAll === explained.current || !detail) return
    explained.current = explainAll
    if (canRequestFormalization(detail.state, operation?.state)) void generate({})
  }, [explainAll, detail])
  const canRequest = canRequestFormalization(detail?.state ?? '', operation?.state)
  return <section aria-label={`系统是这样理解的 ${clauseId}`} className="space-y-3 rounded-md bg-muted/40 px-3 py-2.5 text-sm">
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
      <h3 className="font-medium">系统是这样理解的</h3>
      {!detail && !readError && <p role="status" className="text-muted-foreground">正在读取…</p>}
      {detail && <p id={reasonId} className="text-muted-foreground">{stateCopy[detail.state] ?? '请按页面上的下一步继续。'}</p>}
      {readError && <p role="status" className="text-muted-foreground">暂时不能读取。{readError}</p>}
    </div>
    {error && <Alert variant="destructive"><AlertTitle>解释没有完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {candidate && <p className="whitespace-pre-wrap break-words leading-relaxed" role="status">{candidate.cnl_text}</p>}
    {previous && <p className="whitespace-pre-wrap break-words text-muted-foreground">上一次理解（原文没有被修改）：{previous.cnl_text}</p>}
    {historical && <p className="whitespace-pre-wrap break-words text-muted-foreground">以前的理解仍可阅读，但不能用于确认现在的规则：{historical.cnl_text}</p>}
    {confirmedAside && <p className="whitespace-pre-wrap break-words text-muted-foreground">已确认的理解仍可阅读：{confirmedAside.cnl_text}</p>}
    {candidate && !confirmed && !sourceVerified && <p id={sourceReasonId} role="status" className="text-muted-foreground">{knownRequestMessage('source_unchecked')}</p>}
    {question && <fieldset className="space-y-2"><legend className="font-medium">{question.reason}</legend>
      <RadioGroup value={selectedChoice} onValueChange={value => { if (!candidate) return; setChoiceCandidate(candidate.id); setChoice(value) }}>
        {(question.choices ?? []).map(item => <label key={item.id} className="flex min-h-11 items-start gap-2"><RadioGroupItem value={item.id} className="mt-0.5"/><span>{item.summary}<span className="mt-1 block text-xs text-muted-foreground">反例：{item.counterexample}</span></span></label>)}
      </RadioGroup>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" disabled={!selectedChoice || locked || waiting} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: selectedChoice}})}>按这个选择重新解释</Button>
        <Button type="button" variant="ghost" size="sm" disabled={locked || waiting} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: 'undecided'}})}>暂不确定</Button>
      </div>
    </fieldset>}
    {(operation?.state === 'queued' || operation?.state === 'running') && <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none"/>正在生成解释…</p>}
    {operation?.state === 'unknown' && <p role="status">生成结果尚未确定。请点“刷新解释进度”，不要重复生成。</p>}
    {operation?.state === 'stopped' && <p role="status">这次解释已停止，可以重新让系统解释。</p>}
    {operation?.state === 'failed' && operation.failure !== 'source_changed' && <p role="status">{formalizationFailure(operation.failure)}</p>}
    {handoff?.state === 'saved' && <p role="status">已生成新的检查草稿，尚未发布，也不会开始审核。{handoff.meaningChanged ? '已确认的含义变了，检查需要重新验证。' : ''}</p>}
    <div className="flex flex-wrap items-center gap-2">
      {canRequest && <><Button type="button" variant="outline" size="sm" disabled={locked} title="这会使用你的个人模型" onClick={() => void generate({})}>让系统解释这条规则</Button>{modelNote && <span className="text-xs text-muted-foreground">这会使用你的个人模型。</span>}</>}
      {candidate && !confirmed && <Button type="button" size="sm" aria-describedby={describedBy} disabled={blocked || locked} onClick={() => void confirm()}>确认这一理解</Button>}
      {confirmed && suiteRevisionId && <Button type="button" variant="outline" size="sm" disabled={handoff?.state === 'sending' || handoff?.state === 'saved'} onClick={() => void materialize(false)}>用于检查优化</Button>}
      {handoff?.state === 'unknown' && <Button type="button" variant="outline" size="sm" onClick={() => void materialize(true)}>刷新检查草稿状态</Button>}
      {conflict && <Button type="button" variant="outline" size="sm" onClick={() => {setConflict(false); setError(''); setPending(crypto.randomUUID())}}>重新读取当前解释</Button>}
      {operation?.operation_id && operation.state !== 'completed' && <Button type="button" variant="outline" size="sm" onClick={() => reread(operation.operation_id)}>刷新解释进度</Button>}
      {readError && <Button type="button" variant="outline" size="sm" onClick={() => setPending(crypto.randomUUID())}>重新读取</Button>}
    </div>
    {candidate && <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
      <label className="grid min-w-0 gap-1">要改成的含义<Textarea className="min-h-11" maxLength={2000} value={selectedRevision} onChange={event => { setRevisionCandidate(candidate.id); setRevision(event.target.value) }}/></label>
      <Button type="button" variant="outline" size="sm" disabled={!selectedRevision.trim() || locked || waiting} onClick={() => void generate({based_on_candidate_id: candidate.id, user_instruction: selectedRevision.trim()})}>修改理解</Button>
    </div>}
    {candidate && <Collapsible>
      <CollapsibleTrigger asChild><Button type="button" variant="ghost" size="sm">查看机器可读的规则</Button></CollapsibleTrigger>
      <CollapsibleContent><div className="space-y-2">
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">
          {candidate.formal_profile}{'\n'}{candidate.owl_content}{candidate.clif_content ? `\n${candidate.clif_content}` : ''}
        </pre>
        <Button type="button" variant="outline" size="sm" onClick={copySpec}>复制</Button>
        {copied && <p role="status">已复制。</p>}
      </div></CollapsibleContent>
    </Collapsible>}
  </section>
}

/** The rule's checker status, read once for the whole rule rather than once per clause. */
export function RuleCheckerStatus({suiteRevisionId}: {suiteRevisionId?: string}) {
  const [checker, setChecker] = useState<'verified' | 'unverified' | 'unknown' | null>(null)
  useEffect(() => {
    setChecker(null)
    if (!suiteRevisionId) return
    const controller = new AbortController()
    void request<{validation?: CheckerValidation}>(`/suite-revisions/${encodeURIComponent(suiteRevisionId)}`, 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) setChecker(checkerAvailability(value.validation))
    }).catch(() => { if (!controller.signal.aborted) setChecker('unknown') })
    return () => controller.abort()
  }, [suiteRevisionId])
  return checker && <p role="status" className="text-xs text-muted-foreground">{checkerAvailabilityCopy(checker)}</p>
}

/** Offers one action for every clause the system has not explained yet; each clause still runs and shows its own request. */
export function ExplainAll({ruleVersionId, onExplain}: {ruleVersionId: string; onExplain: () => void}) {
  const [waiting, setWaiting] = useState<number | null>(null), [started, setStarted] = useState(false)
  useEffect(() => {
    setWaiting(null); setStarted(false)
    const controller = new AbortController()
    void request<{clauses: {state?: unknown}[]}>(`/rules/${ruleVersionId}/formalizations`, 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) setWaiting(value.clauses.filter(clause => clause.state === 'not_started').length)
    }).catch(() => {})
    return () => controller.abort()
  }, [ruleVersionId])
  if (started) return <p role="status" className="text-xs text-muted-foreground">已请系统解释 {waiting} 条，进度显示在各条款下。</p>
  if (!waiting || waiting < 2) return null
  return <div className="flex flex-wrap items-center gap-2"><Button type="button" size="sm" onClick={() => {setStarted(true); onExplain()}}>让系统解释全部 {waiting} 条</Button><span className="text-xs text-muted-foreground">这会使用你的个人模型。</span></div>
}
