import {useEffect, useRef, useState} from 'react'
import {knownRequestMessage, request} from '../api'
import type {components} from '../generated/openapi.ts'
import {meaningChangedWithoutText} from '../audit/rule-impact-state.ts'
import {canRequestFormalization, checkerAvailability, checkerAvailabilityCopy, confirmedReadingAside, formalizationFailure, formalizationReceiptMatches, sameFormalizationTarget, textForCandidate, visibleFormalizationReadings, type CheckerValidation} from './requirement-formalization-contract.ts'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {message} from './remote'

type Detail = components['schemas']['ClauseFormalizationView']
type Receipt = components['schemas']['OperationReceipt']

const stateCopy: Record<string, string> = {
  not_started: '来源已在上方。系统还没有解释这条规则。',
  source_changed: '原文或范围已变化。以前的确认不能用于现在这条规则。',
  candidate: '请核对系统理解。这还不是检查功能已验证。',
  needs_clarification: '还有一个会影响判定的问题。回答之前不能确认。',
  partial: '形式化还不完整，不能确认这一理解。',
  confirmed: '规则解释已确认。这只确认需求含义，不是检查功能已验证。',
}

export function ClauseFormalization({ruleVersionId, clauseId, suiteRevisionId, sourceVerified}: {ruleVersionId: string; clauseId: string; suiteRevisionId?: string; sourceVerified: boolean}) {
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
  const [checker, setChecker] = useState<'verified' | 'unverified' | 'unknown' | 'missing'>('missing')
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
    if (!suiteRevisionId) { setChecker('missing'); return }
    const controller = new AbortController()
    setChecker('unknown')
    void request<{validation?: CheckerValidation}>(`/suite-revisions/${encodeURIComponent(suiteRevisionId)}`, 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) setChecker(checkerAvailability(value.validation))
    }).catch(() => { if (!controller.signal.aborted) setChecker('unknown') })
    return () => controller.abort()
  }, [suiteRevisionId])
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
    }).catch(() => {if (stillShown(origin)) setError('形式规范没有复制。请展开高级内容后手动选择。')})
  }
  function reread(operationId: string) {
    const origin = {ruleVersionId, clauseId}
    void request<Receipt>(`/rule-formalization-generations/${operationId}`, 'GET').then(value => {
      if (!stillShown(origin) || !formalizationReceiptMatches(origin, value, operationId)) return
      setOperation(value)
      if (value.state === 'completed') setPending(value.operation_id)
    }).catch(cause => {if (stillShown(origin)) setError(message(cause))})
  }
  return <section aria-label={`系统是这样理解的 ${clauseId}`} className="space-y-3 rounded-lg border p-3">
    <h3 className="text-base font-semibold">系统是这样理解的</h3>
    {error && <Alert variant="destructive"><AlertTitle>解释没有完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {!detail && !readError && <p role="status">正在读取系统理解</p>}
    {readError && <p role="status">系统理解暂时不能读取。{readError}</p>}
    {detail && <p id={reasonId} className="text-sm text-muted-foreground">{stateCopy[detail.state] ?? '请按页面上的下一步继续。'}</p>}
    {checker !== 'missing' && <p role="status" className="text-sm">{checkerAvailabilityCopy(checker)}</p>}
    {detail && !confirmed && !sourceVerified && <p id={sourceReasonId} role="status">{knownRequestMessage('source_unchecked')}</p>}
    {previous && <p className="text-sm text-muted-foreground">原文没有被修改。</p>}
    {previous && <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">上一次理解：{previous.cnl_text}</p>}
    {historical && <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">以前的理解仍可阅读，但不能用于确认现在的规则：{historical.cnl_text}</p>}
    {confirmedAside && <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">已确认的理解仍可阅读：{confirmedAside.cnl_text}</p>}
    {candidate && <p className="whitespace-pre-wrap break-words leading-relaxed" role="status">{candidate.cnl_text}</p>}
    {question && <fieldset className="space-y-2"><legend className="text-sm font-medium">{question.reason}</legend>
      {(question.choices ?? []).map(item => <label key={item.id} className="block min-h-11"><input className="mr-2" type="radio" name={`${clauseId}-${question.id}`} checked={selectedChoice === item.id} onChange={() => { if (!candidate) return; setChoiceCandidate(candidate.id); setChoice(item.id) }}/>{item.summary}<span className="mt-1 block text-xs text-muted-foreground">反例：{item.counterexample}</span></label>)}
      <Button type="button" variant="outline" disabled={!selectedChoice || locked || waiting} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: selectedChoice}})}>按这个选择重新解释</Button>
      <Button type="button" variant="outline" disabled={locked || waiting} onClick={() => void generate({based_on_candidate_id: candidate?.id, clarification_answer: {unresolved_id: question.id, choice_id: 'undecided'}})}>暂不确定</Button>
    </fieldset>}
    <div className="flex flex-wrap gap-2">
      {canRequestFormalization(detail?.state ?? '', operation?.state) && <Button type="button" disabled={locked} onClick={() => void generate({})}>让系统解释这条规则</Button>}
      {canRequestFormalization(detail?.state ?? '', operation?.state) && <p className="text-xs text-muted-foreground">这会使用你的个人模型。</p>}
      <Button type="button" aria-describedby={describedBy} disabled={blocked || confirmed || locked} onClick={() => void confirm()}>确认这一理解</Button>
      {candidate && <label className="grid min-w-0 w-full gap-1 text-sm sm:w-auto sm:min-w-60">要改成的含义<textarea className="min-h-11 w-full rounded-md border bg-transparent px-2 py-1" maxLength={2000} value={selectedRevision} onChange={event => { setRevisionCandidate(candidate.id); setRevision(event.target.value) }} /></label>}
      {candidate && <Button type="button" variant="outline" disabled={!selectedRevision.trim() || locked || waiting} onClick={() => void generate({based_on_candidate_id: candidate.id, user_instruction: selectedRevision.trim()})}>修改理解</Button>}
      {(operation?.state === 'queued' || operation?.state === 'running') && <p role="status">正在生成解释。请读取原操作，不要再次付费生成。</p>}
      {operation?.state === 'unknown' && <p role="status">生成结果尚未确定。请读取原操作，不要再次生成。</p>}
      {operation?.state === 'stopped' && <p role="status">这次解释已停止。可以重新让系统解释。</p>}
      {operation?.state === 'failed' && operation.failure !== 'source_changed' && <p role="status">{formalizationFailure(operation.failure)}</p>}
      {conflict && <Button type="button" variant="outline" onClick={() => {setConflict(false); setError(''); setPending(crypto.randomUUID())}}>重新读取当前候选</Button>}
      {confirmed && suiteRevisionId && <Button type="button" variant="outline" disabled={handoff?.state === 'sending' || handoff?.state === 'saved'} onClick={() => void materialize(false)}>用于检查优化</Button>}
      {handoff?.state === 'saved' && <p role="status">已生成待验证的检查候选。尚未发布，也不会开始审核。{handoff.meaningChanged ? '原文没有变化。已确认的含义已变化，检查需要重新验证。' : ''}</p>}
      {handoff?.state === 'unknown' && <Button type="button" variant="outline" onClick={() => void materialize(true)}>重新读取检查候选</Button>}
      {operation?.operation_id && operation.state !== 'completed' && <Button type="button" variant="outline" onClick={() => reread(operation.operation_id)}>重新读取原操作</Button>}
    </div>
    {candidate && <Collapsible>
      <CollapsibleTrigger asChild><Button type="button" variant="ghost" size="sm">查看形式化表达（高级）</Button></CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">
          {candidate.formal_profile}{'\n'}{candidate.owl_content}{candidate.clif_content ? `\n${candidate.clif_content}` : ''}
        </pre>
        <Button type="button" variant="outline" size="sm" onClick={copySpec}>复制形式规范</Button>
        {copied && <p role="status">形式规范已复制。</p>}
      </CollapsibleContent>
    </Collapsible>}
  </section>
}
