import {useCallback, useContext, useEffect, useId, useRef, useState} from 'react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Label} from '../components/ui/label'
import {Textarea} from '../components/ui/textarea'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {sourceLocation, displayTime} from '../helpers'
import {writeAuditAddress} from './routes'
import {PersonalModelSettings} from './model-config'
import type {PersonalModelConfig} from './model-config-contract'
import {CheckerRelease} from './checker-release'
import {AffectedFeedbackRounds} from './checker-feedback-groups'
import {CheckerEvidenceInput} from './checker-evidence'
import {definiteCheckerRejection} from './checker-contract'
import {selectedFeedback, checkerReason, latestOptimization, checkedFeedbackView, feedbackEvidence, feedbackSourceStatus, nativeCheckerFeedbackAdapter, optimizationSummary, sameEvidence, usageText, type FeedbackGroup, type SuiteFeedbackView, type CheckerFeedback, type CheckerFeedbackAdapter, type CheckerFeedbackInput, type CheckerFeedbackView, type CheckerOptimization, type FeedbackEvidence, type OptimizeInput, type RawFeedbackEvidence} from './checker-feedback-contract'
import {SupportEntryContext} from './support'
import {resultLabel} from './status-labels'
import {Disclosure} from '../components/disclosure'

const failureMessage = (cause: unknown) => checkerReason(typeof (cause as {code?: unknown})?.code === 'string' ? (cause as {code: string}).code : cause instanceof Error ? cause.message : '操作暂未完成。')
const statusText = (status: string | null) => status && status in resultLabel ? resultLabel[status as keyof typeof resultLabel] : '未检查'

/** Human assertions remain feedback until an independent validation has actually run. */
export function CheckerFeedbackWorkspace({roundId, fileVersionId, ruleVersionId, clauseId, originalStatus, resultAttempt, evidence, originals, onEvidence, onReaudit, adapter = nativeCheckerFeedbackAdapter}: {roundId: string; fileVersionId: string; ruleVersionId: string; clauseId: string; originalStatus: string; resultAttempt: number | null; evidence: RawFeedbackEvidence[]; originals: {id: string; name: string}[]; onEvidence: (value: FeedbackEvidence) => void; onReaudit?: () => void; adapter?: CheckerFeedbackAdapter}) {
  const formId = useId(), support = useContext(SupportEntryContext), locked = useRef(false), submittedOperations = useRef(new Set<string>())
  const scope = `${roundId}:${fileVersionId}:${clauseId}`
  const savedId = (name: string) => {const params = new URL(location.href).searchParams; return params.get('checkerFeedbackScope') === scope ? params.get(name) : null}
  function remember(name: string, id: string | null) {
    const url = new URL(location.href)
    if (url.searchParams.get('checkerFeedbackScope') !== scope) for (const key of ['checkerFeedbackOperation', 'checkerOptimization', 'checkerSampleMutation']) url.searchParams.delete(key)
    url.searchParams.set('checkerFeedbackScope', scope)
    if (id) url.searchParams.set(name, id); else url.searchParams.delete(name)
    writeAuditAddress(url)
  }
  const [view, setView] = useState<CheckerFeedbackView | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false)
  const [expected, setExpected] = useState<CheckerFeedbackInput['expected_status'] | ''>(''), [rationale, setRationale] = useState(''), [selectedEvidence, setSelectedEvidence] = useState<FeedbackEvidence[]>([])
  const [reasonCategory, setReasonCategory] = useState<CheckerFeedbackInput['reason_category']>('unspecified')
  const [selectedIds, setSelectedIds] = useState<string[] | null>(null), [groups, setGroups] = useState<FeedbackGroup[]>([]), [sampleCursor, setSampleCursor] = useState<number | null>(null)
  function receiveSamples(value: SuiteFeedbackView, append = false) {
    setSamples(rows => append ? [...new Map([...(rows || []), ...value.feedback].map(row => [row.id, row])).values()] : value.feedback)
    setGroups(rows => {
      const merged = new Map((append ? rows : []).map(row => [row.id, row]))
      for (const group of value.groups || []) merged.set(group.id, {...group, feedback_ids: [...new Set([...(merged.get(group.id)?.feedback_ids || []), ...group.feedback_ids])]})
      return [...merged.values()]
    })
    setSampleCursor(value.next_cursor ?? null)
  }
  const [samples, setSamples] = useState<CheckerFeedback[] | null>(null), [samplesTick, setSamplesTick] = useState(0)
  // Refreshes temporarily remove the sample list; keep the user's disclosure choice.
  const [samplesExpanded, setSamplesExpanded] = useState(false)
  const [knownSuite, setKnownSuite] = useState<{revision: string; key: string} | null>(null)
  const [editing, setEditing] = useState<CheckerFeedback | null>(null), [deleting, setDeleting] = useState<CheckerFeedback | null>(null)
  const [pendingMutation, setPendingMutation] = useState(() => savedId('checkerSampleMutation'))
  const [pendingFeedback, setPendingFeedback] = useState(() => savedId('checkerFeedbackOperation'))
  // Discovery can show a report; only a URL or user intent may replace an explicit revision.
  const [operationIntentId, setOperationIntentId] = useState(() => savedId('checkerOptimization'))
  const [operationId, setOperationId] = useState(() => savedId('checkerOptimization')), [operation, setOperation] = useState<CheckerOptimization | null>(null)
  const [admissionPending, setAdmissionPending] = useState(false)
  const [developmentRevision, setDevelopmentRevision] = useState<string | null>(null), [workspaceBusy, setWorkspaceBusy] = useState(false)
  const [ruleConfirmed, setRuleConfirmed] = useState(false)
  const [modelConfig, setModelConfig] = useState<PersonalModelConfig | null>(null), [consent, setConsent] = useState(false), [showModel, setShowModel] = useState(false), [stopConfirm, setStopConfirm] = useState(false)
  const read = useCallback(async () => checkedFeedbackView(await adapter.read(roundId), roundId), [adapter, roundId])
  const currentFeedback = view?.feedback.filter(row => row.file_version_id === fileVersionId && row.rule_clause_id === clauseId) || []
  const frozenSource = view?.sources?.find(row => row.file_version_id === fileVersionId)
  const suiteRevisionId = currentFeedback.at(-1)?.suite_revision_id || knownSuite?.revision || operation?.suite_revision_id || frozenSource?.suite_revision_id
  const suiteKey = currentFeedback.at(-1)?.suite_key || knownSuite?.key || operation?.suite_key || frozenSource?.suite_key
  const batch = (samples || []).filter(row => row.suite_key === suiteKey)
  const selectedBatch = selectedFeedback(batch, selectedIds, currentFeedback.map(row => row.id))
  const hasSelectedTraining = selectedBatch.some(row => (row.case_ordinal ?? row.ordinal) % 4 !== 0)
  useEffect(() => {
    if (!suiteRevisionId || !suiteKey) return
    setKnownSuite({revision: suiteRevisionId, key: suiteKey})
    let live = true
    setSamples(null)
    void adapter.samples(suiteRevisionId).then(value => {if (live) receiveSamples(value)}).catch(cause => {if (live) {setSamples(null); setError(failureMessage(cause))}})
    return () => {live = false}
  }, [adapter, suiteRevisionId, suiteKey, samplesTick])
  const batchIdentity = batch.map(row => `${row.id}:${row.revision}`).join(',')
  const selectedIdentity = selectedBatch.map(row => row.id).join(',')
  useEffect(() => {setConsent(false); setRuleConfirmed(false)}, [batchIdentity, selectedIdentity, developmentRevision])
  const modelReady = modelConfig?.configured === true && Boolean(modelConfig.revision)
  useEffect(() => {setConsent(false)}, [modelConfig])
  const running = Boolean(operationId && (!operation || ['queued', 'running', 'unknown'].includes(operation.state)))
  const readOnly = busy || workspaceBusy || Boolean(pendingFeedback || pendingMutation)
  const validSource = feedbackSourceStatus(originalStatus, resultAttempt)
  useEffect(() => {
    let live = true
    void read().then(value => {
      if (!live) return
      setView(value); setError('')
      // A different entry point reads the same server operation, not a browser ledger.
      if (!operationId) {
        const key = value.feedback.find(row => row.file_version_id === fileVersionId && row.rule_clause_id === clauseId)?.suite_key
        const latest = key ? latestOptimization(value, key) : null
        if (latest) setOperationId(latest.operation_id)
      }
    }).catch(cause => {if (live) setError(failureMessage(cause))})
    return () => {live = false}
  }, [read, fileVersionId, clauseId])
  useEffect(() => {
    // The URL records intent before POST, but GET cannot resolve that intent until
    // admission settles. A premature 404 must not replace its rejection receipt.
    if (!operationId || !view || admissionPending) return
    if (!view.optimizations.some(row => row.operation_id === operationId) && !submittedOperations.current.has(operationId)) {setOperation(null); setError('尚未读到本轮的原优化操作，请查看原操作，不要重复开始。'); return}
    let live = true, timer: ReturnType<typeof setTimeout> | undefined
    setOperation(null)
    const poll = async () => {
      try {
        const value = await adapter.operation(operationId)
        if (!live) return
        setOperation(value)
        if (['queued', 'running'].includes(value.state)) timer = setTimeout(() => void poll(), 4000)
        else if (value.state === 'completed') setNotice('修复结果已就绪，请核对回归结果；尚未发布。')
      } catch (cause) {if (live) setError(failureMessage(cause))}
    }
    void poll()
    return () => {live = false; if (timer) clearTimeout(timer)}
  }, [adapter, operationId, view, admissionPending])
  function begin() {if (locked.current) return false; locked.current = true; setBusy(true); setError(''); setNotice(''); return true}
  function end() {locked.current = false; setBusy(false)}
  function resetEditor() {setEditing(null); setExpected(''); setRationale(''); setSelectedEvidence([]); setReasonCategory('unspecified')}
  async function recover() {
    if (!begin()) return
    try {
      const value = await read(); setView(value)
      if (suiteRevisionId) receiveSamples(await adapter.samples(suiteRevisionId))
      if (pendingFeedback) {
        const saved = value.feedback.find(row => row.operation_id === pendingFeedback)
        if (!saved) {setError('尚未读到原反馈，请稍后读取，不要重复提交。'); return}
        setPendingFeedback(null); remember('checkerFeedbackOperation', null); setNotice('原反馈已保存。')
      }
      if (pendingMutation) {
        setPendingMutation(null); remember('checkerSampleMutation', null); resetEditor(); setDeleting(null)
        setNotice('已读取当前样本。请核对实际内容，未自动重试之前的修改或删除。')
      }
      if (operationId) {
        if (!value.optimizations.some(row => row.operation_id === operationId) && !submittedOperations.current.has(operationId)) {setError('尚未读到本轮的原优化操作，请稍后读取。'); return}
        setOperation(await adapter.operation(operationId))
      }
    } catch (cause) {setError(failureMessage(cause))} finally {end()}
  }
  async function save() {
    if (readOnly || !expected || !rationale.trim() || !selectedEvidence.length || !validSource || !begin()) return
    const answer = {expected_status: expected, rationale: rationale.trim(), reason_category: reasonCategory, evidence: feedbackEvidence(selectedEvidence)}
    try {
      if (editing) {
        setPendingMutation(editing.id); remember('checkerSampleMutation', editing.id)
        await adapter.edit(editing.round_id, editing.id, {...answer, expected_revision: editing.revision})
        setPendingMutation(null); remember('checkerSampleMutation', null); resetEditor()
        setNotice('正确答案已修改。原审核未改写，之前的验证需要重新执行。')
      } else {
        const input: CheckerFeedbackInput = {...answer, operation_id: crypto.randomUUID(), file_version_id: fileVersionId, rule_version_id: ruleVersionId, rule_clause_id: clauseId, original_status: originalStatus as CheckerFeedbackInput['original_status'], result_attempt: resultAttempt!}
        setPendingFeedback(input.operation_id); remember('checkerFeedbackOperation', input.operation_id)
        const saved = await adapter.save(roundId, input)
        setSelectedIds([saved.id])
        setPendingFeedback(null); remember('checkerFeedbackOperation', null); resetEditor()
        setNotice('已加入待修复。尚未调用模型，也没有改写本轮结果。')
      }
      try {setView(await read()); setSamplesTick(value => value + 1)} catch {setError('操作结果已确认，但最新列表暂不可读。请读取最新记录。')}
    } catch (cause) {
      if (definiteCheckerRejection(cause)) {setPendingFeedback(null); setPendingMutation(null); remember('checkerFeedbackOperation', null); remember('checkerSampleMutation', null); setError(failureMessage(cause))}
      else setError('样本保存结果尚未确定，请查看原操作，不要重复提交。')
    } finally {end()}
  }
  async function remove() {
    if (!deleting || readOnly || !begin()) return
    setPendingMutation(deleting.id); remember('checkerSampleMutation', deleting.id)
    try {
      await adapter.remove(deleting.round_id, deleting.id, deleting.revision)
      setPendingMutation(null); remember('checkerSampleMutation', null); setDeleting(null); resetEditor(); try {setView(await read()); setSamplesTick(value => value + 1)} catch {setError('删除结果已确认，但最新列表暂不可读。请读取最新记录。')}; setNotice('样本已删除，相关验证需要重新执行。已有报告不变。')
    } catch (cause) {
      if (definiteCheckerRejection(cause)) {setPendingMutation(null); remember('checkerSampleMutation', null); setError(failureMessage(cause))}
      else setError('删除结果待确认，请读取当前样本，不要重复删除。')
    } finally {end()}
  }
  async function optimize(action: OptimizeInput['action'], consumeModel: boolean, revision = developmentRevision || operation?.candidate_revision_id || suiteRevisionId) {
    if (busy || running || !revision || !hasSelectedTraining || consumeModel && (!modelReady || !consent) || !begin()) return
    const id = crypto.randomUUID()
    submittedOperations.current.add(id)
    setAdmissionPending(true)
    setOperation(null); setOperationId(id); setOperationIntentId(id); remember('checkerOptimization', id)
    try {
      setOperation(await adapter.optimize(revision, {operation_id: id, feedback_ids: selectedBatch.map(row => row.id), action, consume_model: consumeModel, ...(ruleConfirmed ? {confirmed_rule_revision_id: revision, expected_feedback_revisions: Object.fromEntries(batch.map(row => [row.id, row.revision]))} : {}), ...(consumeModel ? {expected_model_revision: modelConfig?.revision ?? null} : {})})); setConsent(false)
      // Accepted-operation polling is independent of the optional list refresh.
      setAdmissionPending(false)
      try {setView(await read()); setSamplesTick(value => value + 1)} catch {setError('操作结果已确认，但最新列表暂不可读。请读取最新记录。')}
    } catch (cause) {
      if ((cause as {code?: string})?.code === 'personal_model_configuration_changed') {setConsent(false); setModelConfig(null)}
      if (definiteCheckerRejection(cause)) {setOperationId(null); setOperationIntentId(null); remember('checkerOptimization', null); setError(failureMessage(cause))}
      else setError('操作提交结果尚未确定。请查看原操作，不要再次开始；若已允许模型调用，可能已经消费 Token。')
    } finally {setAdmissionPending(false); end()}
  }
  async function stop() {
    if (!operationId || !begin()) return
    try {setOperation(await adapter.stop(operationId)); setStopConfirm(false); setNotice('已提交停止请求。以读取到的最终状态为准，已经发生的消费不会撤销。')}
    catch (cause) {setError(definiteCheckerRejection(cause) ? failureMessage(cause) : '停止结果待确认，请查看原操作。')} finally {end()}
  }
  function contactSupport() {
    support?.({scenario: '检查误判修复', facts: [{label: '审核轮次', value: roundId}, {label: '原件版本', value: fileVersionId}, {label: '条款', value: clauseId}, {label: '原机器结论', value: originalStatus}, ...selectedEvidence.map(item => ({label: '原件定位', value: `${item.file_version_id} · ${sourceLocation({locator: item.locator})}`})), ...(operationId ? [{label: '优化操作', value: operationId}, {label: '操作状态', value: operation?.state || '待确认'}] : []), ...(rationale.trim() ? [{label: '用户判断依据', value: rationale.trim()}] : [])]})
  }
  const priorOperations = view?.optimizations.filter(row => row.suite_key === suiteKey) || []
  const report = operation?.report?.validation, summary = report ? optimizationSummary(report) : null
  const sampleAddress = (row: CheckerFeedback, item: FeedbackEvidence) => `/audit?round=${encodeURIComponent(row.round_id)}&file=${encodeURIComponent(item.file_version_id)}&clause=${encodeURIComponent(row.rule_clause_id)}&basis=1&checkerFeedbackClause=${encodeURIComponent(row.rule_clause_id)}`
  const sampleEvidence = (row: CheckerFeedback, item: FeedbackEvidence, index: number) => row.round_id === roundId ? <Button key={index} size="sm" variant="link" onClick={() => onEvidence(item)}>{sourceLocation({locator: item.locator})}</Button> : <a key={index} className="block underline" href={sampleAddress(row, item)}>查看样本原件：{sourceLocation({locator: item.locator})}</a>
  const editorOriginals = editing && editing.round_id !== roundId ? [...new Set(editing.evidence.map(item => item.file_version_id))].map(id => ({id, name: '该样本的证据原件'})) : originals
  const sourceEvidence = feedbackEvidence(editing?.evidence || evidence)
  const selection = [...sourceEvidence, ...selectedEvidence.filter(item => !sourceEvidence.some(row => sameEvidence([row], [item])))]
  return <section aria-label="这条判错了" className="space-y-4 border-t pt-4">
    <h3 className="font-medium">{editing ? `修改条款 ${editing.rule_clause_id} 的正确答案` : '告诉它正确答案'}</h3>
    <p className="text-sm text-muted-foreground">原机器结论保留。请先核对原件，正确答案不会自动成为已验证样本。</p>
    {!validSource && <p role="alert" className="text-sm">原结果的执行身份不完整，请先读取完整审核结果，不能保存未确认来源的样本。</p>}
    {error && <Alert variant="destructive"><AlertTitle>需要核对</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <div className="space-y-2"><Label htmlFor={`${formId}-expected`}>正确结论</Label><NativeSelect id={`${formId}-expected`} disabled={readOnly} value={expected} onChange={event => setExpected(event.target.value as typeof expected)}><NativeSelectOption value="">请明确选择</NativeSelectOption><NativeSelectOption value="PASS">符合</NativeSelectOption><NativeSelectOption value="NG">不符合</NativeSelectOption><NativeSelectOption value="NA">不适用</NativeSelectOption></NativeSelect></div>
    <fieldset className="space-y-3"><legend className="text-sm font-medium">核对原件并选择判断依据</legend>{selection.map((item, index) => <div key={index} className="space-y-2 rounded border p-2"><div className="flex items-start gap-2"><Checkbox id={`${formId}-evidence-${index}`} checked={selectedEvidence.some(row => sameEvidence([row], [item]))} disabled={readOnly} onCheckedChange={checked => setSelectedEvidence(values => checked ? [...values, item] : values.filter(row => !sameEvidence([row], [item])))}/><Label htmlFor={`${formId}-evidence-${index}`} className="break-words">{sourceLocation({locator: item.locator})}</Label></div><p className="whitespace-pre-wrap text-xs">{item.excerpt || '未提供文字摘录，请在原件中确认。'}</p>{editing && editing.round_id !== roundId ? <a className="block text-sm underline" href={sampleAddress(editing, item)}>打开样本所在审核的原件</a> : <Button variant="link" size="sm" onClick={() => onEvidence(item)}>打开这处原件</Button>}</div>)}{!selection.length && <p className="text-sm text-warning">机器没有提供可定位证据。你可以打开本轮原件，添加自己找到的位置。</p>}<CheckerEvidenceInput key={editing?.id || 'new'} files={editorOriginals} disabled={readOnly} onAdd={item => setSelectedEvidence(rows => rows.some(row => sameEvidence([row], [item])) ? rows : [...rows, item])}/></fieldset>
    <div className="space-y-2"><Label htmlFor={`${formId}-reason-category`}>判断理由类别（用于建议分组）</Label><NativeSelect id={`${formId}-reason-category`} value={reasonCategory} disabled={readOnly} onChange={event => setReasonCategory(event.target.value as typeof reasonCategory)}><NativeSelectOption value="unspecified">暂不分类</NativeSelectOption><NativeSelectOption value="missing_value">同列或字段缺值</NativeSelectOption><NativeSelectOption value="incorrect_reading">原件读取不准确</NativeSelectOption><NativeSelectOption value="rule_application">规则适用或判断不准确</NativeSelectOption></NativeSelect></div>
    <div className="space-y-2"><Label htmlFor={`${formId}-rationale`}>为什么应当这样判断</Label><Textarea id={`${formId}-rationale`} value={rationale} maxLength={8000} disabled={readOnly} onChange={event => setRationale(event.target.value)} placeholder="说明原件中的事实和适用规则，不只写‘判错了’。"/></div>
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={readOnly || !validSource || !expected || !rationale.trim() || !selectedEvidence.length} onClick={() => void save()}>{editing ? '保存正确答案修改' : '加入待修复'}</Button>{editing && <Button variant="ghost" disabled={readOnly} onClick={resetEditor}>取消修改</Button>}<Button variant="ghost" disabled={busy} onClick={() => void recover()}>{pendingFeedback || pendingMutation || running ? '查看原操作' : '读取最新记录'}</Button></div>
    {suiteRevisionId && samples?.length === 0 && <p className="text-sm text-muted-foreground">{sampleCursor !== null ? '当前页暂无可读取的验证样本，可以继续读取更多样本。' : '尚无验证样本。可以查看版本和官方更新；添加正确答案后才能升级并验证。'}</p>}
    {suiteRevisionId && samples === null && <p role="status" className="text-sm">检查样本尚未读齐，请读取最新记录后再开始修复。</p>}
    {sampleCursor !== null && <Button variant="ghost" disabled={readOnly || running} onClick={() => {void adapter.samples(suiteRevisionId!, sampleCursor).then(value => receiveSamples(value, true)).catch(cause => setError(failureMessage(cause)))}}>读取更多样本</Button>}
    {batch.length > 0 && <section aria-label="同类文件待修复" className="space-y-3 border-t pt-3"><h4 className="font-medium">已确认的样本 {batch.length} 条</h4><p className="text-sm text-muted-foreground">默认只选择当前这一条。可以选择可能相关的反馈合并修复；是否节省用量以实际结果为准。未选择的已确认样本仍用于独立回归。</p>
      {groups.map(group => <div key={group.id} className="space-y-2 rounded border p-2"><p className="text-sm">这 {group.feedback_ids.length} 条可能是同一个原因：条款 {group.rule_clause_id} · {group.source_field}</p><Button variant="outline" size="sm" disabled={readOnly || running} onClick={() => {setSelectedIds(group.feedback_ids); setSamplesExpanded(true)}}>选择这组一起修复</Button></div>)}
      <p className="text-sm">本次选择 {selectedBatch.length} 条；每条正确答案均由人单独确认。分组不是根因结论，可以取消勾选拆开修复。</p><Button variant="ghost" size="sm" disabled={readOnly || running} onClick={() => {setSelectedIds(batch.map(row => row.id)); setSamplesExpanded(true)}}>选择全部已确认样本</Button>
      {!hasSelectedTraining && <p className="text-sm text-muted-foreground">请至少选择一条修复样本；独立验证样本只用于检验结果。</p>}
      <Disclosure className="text-sm" open={samplesExpanded} onOpenChange={setSamplesExpanded} summary={<>查看、改错或删除样本</>}>{batch.map(row => <article key={row.id} className="space-y-2 border-t py-3"><div className="flex gap-2"><Checkbox id={`${formId}-sample-${row.id}`} checked={selectedBatch.some(item => item.id === row.id)} disabled={readOnly || running} onCheckedChange={value => setSelectedIds(value ? [...selectedBatch.map(item => item.id), row.id] : selectedBatch.filter(item => item.id !== row.id).map(item => item.id))}/><Label htmlFor={`${formId}-sample-${row.id}`}>本次修复条款 {row.rule_clause_id} 的样本 {row.ordinal}</Label></div><p>条款 {row.rule_clause_id} · 正确答案：{resultLabel[row.expected_status]} · {(row.case_ordinal ?? row.ordinal) % 4 === 0 ? '独立验证样本' : '修复样本'}</p><p>{row.rationale}</p>{row.evidence.map((item, index) => sampleEvidence(row, item, index))}<div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={readOnly || running} onClick={() => {setEditing(row); setExpected(row.expected_status); setRationale(row.rationale); setReasonCategory(row.reason_category || 'unspecified'); setSelectedEvidence(feedbackEvidence(row.evidence))}}>改错</Button><Button variant="ghost" size="sm" disabled={readOnly || running} onClick={() => setDeleting(row)}>删除样本</Button></div></article>)}</Disclosure>
      {deleting && <div role="group" aria-label="确认删除样本" className="space-y-2 border p-3"><p className="text-sm">删除条款 {deleting.rule_clause_id} 的这条样本后，相关验证需要重新执行；已有报告不变。</p><Button variant="destructive" disabled={readOnly} onClick={() => void remove()}>确认删除样本</Button><Button variant="ghost" disabled={readOnly} onClick={() => setDeleting(null)}>取消</Button></div>}
      <div className="flex items-start gap-2"><Checkbox id={`${formId}-rule-confirmation`} checked={ruleConfirmed} disabled={readOnly || running} onCheckedChange={value => setRuleConfirmed(value === true)}/><Label htmlFor={`${formId}-rule-confirmation`}>我已核对当前所选规则及全部样本，确认以上正确答案仍适用于当前规则</Label></div><p className="text-xs text-muted-foreground">规则变化后必须由你重新确认答案，模型不会代为确认；未确认的旧答案不会产生已验证结论。</p>
      <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={readOnly || running || !hasSelectedTraining} onClick={() => void optimize('save_only', false)}>仅保存待发布修改</Button><Button variant="outline" disabled={readOnly || running || !hasSelectedTraining} onClick={() => void optimize('run_tests', false)}>只运行现有检查验证</Button><Button disabled={readOnly || running || !hasSelectedTraining} onClick={() => setShowModel(value => !value)}>让 AI 修复并验证</Button></div><p className="text-xs text-muted-foreground">仅保存不运行测试。只验证不会生成代码或调用模型，需要模型的条目会如实显示无法判断。以上操作均不自动发布。</p>
      {showModel && <div className="space-y-3"><PersonalModelSettings onConfig={setModelConfig}/><p className="text-sm">将把本次明确选择的反馈、样本资料及规则发送到已保存的模型服务，使用你的 Key，可能产生 Token 费用。比普通审核更贵且更慢，无法承诺精确费用封顶。关闭或离开页面不停止已经发生的消费；返回时读取同一操作。</p><div className="flex items-start gap-2"><Checkbox id={`${formId}-consent`} checked={consent} disabled={!modelReady} onCheckedChange={value => setConsent(value === true)}/><Label htmlFor={`${formId}-consent`}>同意将本次资料发送至 {modelConfig?.base_url || '待配置地址'}，使用 {modelConfig?.model || '待配置模型'} 修复并验证这 {selectedBatch.length} 条反馈</Label></div><Button disabled={readOnly || running || !hasSelectedTraining || !modelReady || !consent} onClick={() => void optimize('run_tests', true)}>确认开始修复</Button></div>}
    </section>}
    {suiteRevisionId && <AffectedFeedbackRounds key={`${suiteRevisionId}:${clauseId}:${roundId}`} revision={suiteRevisionId} clause={clauseId} round={roundId} adapter={adapter}/>}
    {operation && <section aria-label="修复与回归结果" data-operation-id={operation.operation_id} data-candidate-revision={operation.candidate_revision_id} className="space-y-3 border-t pt-3"><h4 className="font-medium">{operation.state === 'saved' ? '已保存，尚未验证' : operation.state === 'completed' ? '修复结果' : operation.state === 'failed' ? '本次修复未完成' : operation.state === 'stopped' ? '已停止' : operation.state === 'unknown' ? '模型消费与执行结果待确认' : '正在修复与验证'}</h4>{['queued', 'running'].includes(operation.state) && <><p role="status" className="text-sm">可以离开页面。操作在后台继续，返回后读取同一记录；不会自动发布。</p><Button variant="outline" disabled={busy || operation.cancel_requested} onClick={() => setStopConfirm(true)}>{operation.cancel_requested ? '已请求停止，等待读回' : '停止后续执行'}</Button></>}{stopConfirm && <div role="group" aria-label="确认停止修复" className="space-y-2"><p className="text-sm">停止后续执行不会撤销已经发生的消费。</p><Button variant="destructive" disabled={busy} onClick={() => void stop()}>确认停止</Button><Button variant="ghost" disabled={busy} onClick={() => setStopConfirm(false)}>继续执行</Button></div>}{operation.failure && <p role="alert" className="text-sm">{checkerReason(operation.failure)}</p>}{operation.report?.reasons?.map(reason => <p key={reason} className="text-sm">{checkerReason(reason)}</p>)}
      {operation.report?.code_changed === false && report?.verified && <p role="status" className="text-sm">代码无需修改，样本已通过。请核对后确认发布，才能采用新规则。</p>}
      {summary && report && <><p role="status" className="text-sm">修好 {summary.fixed} 条 · 新增错误 {summary.regressions} 条 · 无法判断 {summary.unknown} 条 · 技术失败 {summary.errors} 条</p><p className="text-sm">错误放行 {summary.falsePass} 条 · 错误判为不符合 {summary.falseNg} 条</p>{(report.total_samples < 4 || !report.cases.some(row => row.split === 'holdout')) && <p className="text-sm text-warning">{report.total_samples === 1 ? '只验证了这一条，没有回归样本。' : '验证样本不足或缺少独立验证样本，不能认定整体通过。'}</p>}{report.reasons.map((reason, index) => <p key={index} className="text-sm text-warning">{checkerReason(reason)}</p>)}<Disclosure className="text-sm" summary={<>逐项验证结果与正确答案</>}><div className="space-y-3">{report.cases.map(row => <article key={row.case_id} data-case-id={row.case_id} className="space-y-2 border-t pt-2"><p>{row.split === 'holdout' ? '独立验证样本' : '修复样本'} · {row.passed ? '符合已确认答案' : '需要核对'}</p>{row.error_code && <p>技术失败：{checkerReason(row.error_code)}</p>}{row.clauses.map(item => <p key={item.clause_id}>条款 {item.clause_id} · 正确答案 {statusText(item.expected)} · 此前 {statusText(item.baseline_actual)} → 当前 {statusText(item.actual)}{item.regression ? ' · 新增错误' : ''}{!item.evidence_locatable ? ' · 原件定位未验证' : ''}</p>)}{samples?.filter(sample => (row.feedback_ids?.length ? row.feedback_ids : [row.case_id]).includes(sample.id)).flatMap(sample => sample.evidence.map((item, index) => sampleEvidence(sample, item, index)))}</article>)}</div></Disclosure></>}
      <p className="text-xs text-muted-foreground">模型用量：{usageText(operation.usage)}</p>
    </section>}
    {suiteRevisionId && suiteKey && <CheckerRelease key={`${scope}:${suiteKey}`} scope={`${scope}:${suiteKey}`} legacyScope={scope} suiteKey={suiteKey} revisionId={suiteRevisionId} optimization={operation?.candidate_revision_id ? {operationId: operation.operation_id, revisionId: operation.candidate_revision_id, explicit: operationIntentId === operation.operation_id} : undefined} preferCurrent onRuleDraft={id => {setDevelopmentRevision(id); setShowModel(true); setConsent(false); setRuleConfirmed(false); setNotice('已选择新版规则，请核对样本答案与费用后确认开始。尚未调用模型或发布。')}} onReaudit={onReaudit} onRevision={setDevelopmentRevision} onBusyChange={setWorkspaceBusy} onUpgradeResult={runTests => setNotice(runTests ? '升级候选已创建，正在进入样本验证；尚未发布。' : '已找回升级候选，尚未自动运行样本。请重新运行验证样本后核对结果。')} onVerify={id => void optimize('run_tests', false, id)} operationRunning={running} verificationBusy={readOnly || running || !hasSelectedTraining} refreshKey={batchIdentity}/>}
    {priorOperations.length > 0 && <Disclosure className="text-sm" summary={<>之前的修复记录</>}>{priorOperations.map(row => <Button key={row.operation_id} variant="link" size="sm" disabled={workspaceBusy} onClick={() => {setOperationId(row.operation_id); setOperationIntentId(row.operation_id); remember('checkerOptimization', row.operation_id)}}>{row.created_at ? displayTime(row.created_at) + ' · ' : ''}{row.state === 'completed' ? '查看修复结果' : row.state === 'saved' ? '查看待验证修改' : row.state === 'failed' ? '查看失败记录' : '读取操作记录'}</Button>)}</Disclosure>}
    {support && <Button variant="link" onClick={contactSupport}>没修好？联系 PPAP 支持</Button>}
  </section>
}
