import {useCallback, useContext, useEffect, useId, useState} from 'react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Label} from '../components/ui/label'
import {Textarea} from '../components/ui/textarea'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {sourceLocation} from '../helpers'
import {writeAuditAddress} from './routes'
import {PersonalModelSettings} from './model-config'
import {CheckerRelease} from './checker-release'
import {definiteCheckerRejection} from './checker-contract'
import {checkedFeedbackView, nativeCheckerFeedbackAdapter, optimizationSummary, type CheckerFeedbackAdapter, type CheckerFeedbackInput, type CheckerFeedbackView, type CheckerOptimization, type FeedbackEvidence} from './checker-feedback-contract'
import {SupportEntryContext} from './support'
import {resultLabel} from './status-labels'

const failureMessage = (cause: unknown) => cause instanceof Error ? cause.message : '操作暂未完成。'
function remember(name: 'checkerFeedbackOperation' | 'checkerOptimization', id: string | null) {
  const url = new URL(location.href)
  if (id) url.searchParams.set(name, id); else url.searchParams.delete(name)
  writeAuditAddress(url)
}

/** Human assertions remain feedback until an independent validation has actually run. */
export function CheckerFeedbackWorkspace({roundId, fileVersionId, ruleVersionId, clauseId, originalStatus, resultAttempt, evidence, onEvidence, onReaudit, adapter = nativeCheckerFeedbackAdapter}: {roundId: string; fileVersionId: string; ruleVersionId: string; clauseId: string; originalStatus: string; resultAttempt: number | null; evidence: FeedbackEvidence[]; onEvidence: (index: number) => void; onReaudit?: () => void; adapter?: CheckerFeedbackAdapter}) {
  const formId = useId(), support = useContext(SupportEntryContext)
  const [view, setView] = useState<CheckerFeedbackView | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false)
  const [expected, setExpected] = useState<CheckerFeedbackInput['expected_status'] | ''>(''), [rationale, setRationale] = useState(''), [selectedEvidence, setSelectedEvidence] = useState<number[]>([])
  const [pendingFeedback, setPendingFeedback] = useState(() => new URL(location.href).searchParams.get('checkerFeedbackOperation'))
  const [operationId, setOperationId] = useState(() => new URL(location.href).searchParams.get('checkerOptimization')), [operation, setOperation] = useState<CheckerOptimization | null>(null)
  const [modelReady, setModelReady] = useState(false), [consent, setConsent] = useState(false), [showModel, setShowModel] = useState(false)
  const read = useCallback(async () => checkedFeedbackView(await adapter.read(roundId), roundId), [adapter, roundId])
  useEffect(() => {
    let current = true
    void read().then(value => {if (current) {setView(value); setError('')}}).catch(cause => {if (current) setError(failureMessage(cause))})
    return () => {current = false}
  }, [read])
  useEffect(() => {
    if (!operationId) return
    let live = true, timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const value = await adapter.operation(operationId)
        if (!live) return
        setOperation(value); setError('')
        if (['queued', 'running'].includes(value.state)) timer = setTimeout(() => void poll(), 4000)
      } catch (cause) {if (live) setError(failureMessage(cause))}
    }
    void poll()
    return () => {live = false; if (timer) clearTimeout(timer)}
  }, [adapter, operationId])
  const currentFeedback = view?.feedback.filter(row => row.file_version_id === fileVersionId && row.rule_clause_id === clauseId) || []
  const suiteRevisionId = currentFeedback.at(-1)?.suite_revision_id
  const batch = view?.feedback.filter(row => row.suite_revision_id === suiteRevisionId) || []
  const running = operationId && (!operation || ['queued', 'running', 'unknown'].includes(operation.state))
  async function recover() {
    if (busy) return
    setBusy(true)
    try {
      const value = await read(); setView(value)
      if (pendingFeedback) {
        const saved = value.feedback.find(row => row.operation_id === pendingFeedback)
        if (!saved) {setError('尚未读到原反馈，请稍后读取，不要重复提交。'); return}
        setPendingFeedback(null); remember('checkerFeedbackOperation', null); setNotice('原反馈已保存。')
      }
      if (operationId) setOperation(await adapter.operation(operationId))
      setError('')
    } catch (cause) {setError(failureMessage(cause))}
    finally {setBusy(false)}
  }
  async function save() {
    if (busy || pendingFeedback || !expected || !rationale.trim() || !selectedEvidence.length) return
    const input: CheckerFeedbackInput = {operation_id: crypto.randomUUID(), file_version_id: fileVersionId, rule_version_id: ruleVersionId, rule_clause_id: clauseId, original_status: originalStatus, result_attempt: resultAttempt, expected_status: expected, rationale: rationale.trim(), evidence: selectedEvidence.map(index => evidence[index])}
    setBusy(true); setError(''); setNotice(''); setPendingFeedback(input.operation_id); remember('checkerFeedbackOperation', input.operation_id)
    try {
      await adapter.save(roundId, input)
      setPendingFeedback(null); remember('checkerFeedbackOperation', null); setNotice('已加入待修复。尚未调用模型，也没有改写本轮结果。')
      setView(await read())
    } catch (cause) {
      if (definiteCheckerRejection(cause)) {setPendingFeedback(null); remember('checkerFeedbackOperation', null); setError(failureMessage(cause))}
      else setError('反馈保存结果尚未确定，请读取原操作，不要重复提交。')
    } finally {setBusy(false)}
  }
  async function optimize() {
    if (busy || running || !suiteRevisionId || !batch.length || !modelReady || !consent) return
    const id = crypto.randomUUID()
    setBusy(true); setOperation(null); setOperationId(id); remember('checkerOptimization', id); setError(''); setNotice('')
    try {
      setOperation(await adapter.optimize(suiteRevisionId, {operation_id: id, feedback_ids: batch.map(row => row.id), action: 'run_tests', consume_model: true}))
      setConsent(false)
    } catch (cause) {
      if (definiteCheckerRejection(cause)) {setOperationId(null); remember('checkerOptimization', null); setError(failureMessage(cause))}
      else setError('优化受理结果尚未确定。模型可能已经消费 Token，请读取原操作，不要再次开始。')
    } finally {setBusy(false)}
  }
  function contactSupport() {
    support?.({scenario: '检查误判修复', facts: [{label: '审核轮次', value: roundId}, {label: '原件版本', value: fileVersionId}, {label: '条款', value: clauseId}, {label: '原机器结论', value: originalStatus}, ...(operationId ? [{label: '优化操作', value: operationId}, {label: '操作状态', value: operation?.state || '待核对'}] : []), ...(rationale.trim() ? [{label: '用户判断依据', value: rationale.trim()}] : [])]})
  }
  const report = operation?.report, summary = report ? optimizationSummary(report) : null
  return <section aria-label="这条判错了" className="space-y-4 border-t pt-4">
    <h3 className="font-medium">告诉它正确答案</h3>
    <p className="text-sm text-muted-foreground">原机器结论保留。请先核对原件，正确答案不会自动成为已验证样本。</p>
    {error && <Alert variant="destructive"><AlertTitle>需要核对</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <div className="space-y-2"><Label htmlFor={`${formId}-expected`}>正确结论</Label><NativeSelect id={`${formId}-expected`} className="w-full" disabled={busy || Boolean(pendingFeedback)} value={expected} onChange={event => setExpected(event.target.value as typeof expected)}><NativeSelectOption value="">请明确选择</NativeSelectOption><NativeSelectOption value="PASS">符合</NativeSelectOption><NativeSelectOption value="NG">不符合</NativeSelectOption><NativeSelectOption value="NA">不适用</NativeSelectOption></NativeSelect></div>
    <fieldset className="space-y-3"><legend className="text-sm font-medium">核对原件并选择判断依据</legend>{evidence.map((item, index) => <div key={index} className="space-y-2 rounded border p-2"><div className="flex items-start gap-2"><Checkbox id={`${formId}-evidence-${index}`} checked={selectedEvidence.includes(index)} disabled={busy || Boolean(pendingFeedback)} onCheckedChange={checked => setSelectedEvidence(values => checked ? [...values, index] : values.filter(value => value !== index))}/><Label htmlFor={`${formId}-evidence-${index}`} className="break-words">{item.locator ? sourceLocation({locator: item.locator}) : '本轮原件，具体位置待核对'}</Label></div><p className="whitespace-pre-wrap text-xs">{item.excerpt || '未提供文字摘录，请在原件中确认。'}</p><Button variant="link" size="sm" onClick={() => onEvidence(index)}>打开这处原件</Button></div>)}{!evidence.length && <p className="text-sm text-warning">这条结果尚无可定位证据。请先联系支持补齐原件定位，不能把无依据的反馈当作正确答案。</p>}</fieldset>
    <div className="space-y-2"><Label htmlFor={`${formId}-rationale`}>为什么应当这样判断</Label><Textarea id={`${formId}-rationale`} value={rationale} maxLength={4000} disabled={busy || Boolean(pendingFeedback)} onChange={event => setRationale(event.target.value)} placeholder="说明原件中的事实和适用规则，不只写‘判错了’。"/></div>
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy || Boolean(pendingFeedback) || !expected || !rationale.trim() || !selectedEvidence.length} onClick={() => void save()}>加入待修复</Button><Button variant="ghost" disabled={busy} onClick={() => void recover()}>{pendingFeedback || running ? '读取原操作' : '读取最新记录'}</Button></div>
    {batch.length > 0 && <section aria-label="同类文件待修复" className="space-y-3 border-t pt-3"><h4 className="font-medium">待修复 {batch.length} 条</h4><p className="text-sm text-muted-foreground">同一检查的反馈一起提交，是否节省用量以实际回执为准。</p><ul className="list-inside list-disc text-sm">{batch.map(row => <li key={row.id}>条款 {row.rule_clause_id} · 正确答案：{resultLabel[row.expected_status]}</li>)}</ul><Button variant="outline" disabled={busy || Boolean(running)} onClick={() => setShowModel(value => !value)}>让 AI 修复并验证</Button>
      {showModel && <div className="space-y-3"><PersonalModelSettings onReady={setModelReady}/><p className="text-sm">将使用你自己的 DeepSeek Key，消耗账户 Token。比普通审核更贵且更慢，无法承诺精确费用封顶。关闭或离开页面不停止已经发生的消费；返回时读取同一操作。</p><div className="flex items-start gap-2"><Checkbox id={`${formId}-consent`} checked={consent} onCheckedChange={value => setConsent(value === true)}/><Label htmlFor={`${formId}-consent`}>同意本次调用我的 DeepSeek 账户修复并验证这 {batch.length} 条反馈</Label></div><Button disabled={busy || Boolean(running) || !modelReady || !consent} onClick={() => void optimize()}>确认开始修复</Button></div>}
    </section>}
    {operation && <section aria-label="修复与回归结果" className="space-y-3 border-t pt-3"><h4 className="font-medium">{operation.state === 'completed' ? '修复结果' : operation.state === 'failed' ? '本次修复未完成' : operation.state === 'unknown' ? '模型消费与执行结果待核对' : '正在修复与验证'}</h4>{['queued', 'running'].includes(operation.state) && <p role="status" className="text-sm">可以离开页面。操作在后台继续，返回后读取同一记录；不会自动发布。</p>}{operation.failure && <p role="alert" className="text-sm">{operation.failure}</p>}
      {summary && report && <><p role="status" className="text-sm">修好 {summary.fixed} 条 · 新增错误 {summary.regressions} 条 · 无法判断 {summary.unknown} 条 · 技术失败 {summary.errors} 条</p><p className="text-sm">错误放行 {summary.falsePass} 条 · 错误判为不符合 {summary.falseNg} 条</p>{report.total_samples <= 1 && <p className="text-sm text-warning">{report.total_samples ? '只验证了这一条，没有回归样本。' : '没有验证样本，不能判断是否修好。'}</p>}{report.reasons.map((reason, index) => <p key={index} className="text-sm text-warning">{reason}</p>)}<details className="text-sm"><summary className="cursor-pointer">逐项验证结果与正确答案</summary><div className="mt-3 space-y-3">{report.cases.map(row => <article key={row.case_id} className="space-y-2 border-t pt-2"><p>{row.split === 'holdout' ? '独立验证样本' : '修复样本'} · {row.passed ? '符合已确认答案' : '需要核对'}</p>{row.error_code && <p>技术失败：{row.error_code}</p>}{row.clauses.map(item => <p key={item.clause_id}>条款 {item.clause_id} · 正确答案 {item.expected} · 此前 {item.baseline_actual || '未核对'} → 当前 {item.actual || '未检查'}{item.regression ? ' · 新增错误' : ''}{!item.evidence_locatable ? ' · 原件定位未验证' : ''}</p>)}</article>)}</div></details></>}
      <p className="text-xs text-muted-foreground">模型用量：{operation.usage?.total_tokens != null ? `${operation.usage.total_tokens} Token` : '未知，不能按零消费处理。'}</p>
      {operation.candidate_revision_id && <CheckerRelease key={operation.candidate_revision_id} revisionId={operation.candidate_revision_id} onReaudit={onReaudit}/>}
    </section>}
    {view && view.optimizations.length > 0 && <details className="text-sm"><summary className="cursor-pointer">之前的修复记录</summary>{view.optimizations.map(row => <Button key={row.operation_id} variant="link" size="sm" onClick={() => {setOperationId(row.operation_id); remember('checkerOptimization', row.operation_id)}}>{row.state === 'completed' ? '查看修复结果' : row.state === 'failed' ? '查看失败记录' : '读取进行中的操作'}</Button>)}</details>}
    {support && <Button variant="link" onClick={contactSupport}>没修好？联系 PPAP 支持</Button>}
  </section>
}
