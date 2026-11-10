import {displayTime} from '../helpers'
import {useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import {apiUrl} from '../api'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {FileName} from '../journey/file-name'
import {sourceLocation} from '../journey/product'
import {RemoteSourcePane} from '../journey/remote-source'
import {checkedFactAnswer, definiteFactAnswerRejection, factValueLabel, groupFactQuestions, type FactAnswerReceipt, type FactAnswerSampleRequest, type FactAnswersAdapter, type FactAnswersView, type FactQuestion} from './fact-answers-contract'
import {FactAnswersContext} from './fact-answers-context'
import {resultLabel, resultTone} from './status-labels'
import {writeAuditAddress} from './routes'
import {Disclosure} from '../components/disclosure'

type FileVersion = components['schemas']['FileVersion']
type MachineFinding = Pick<components['schemas']['Finding'], 'item_key' | 'item' | 'status'>
type PendingSample = {operationId: string; answerId: string; itemKey: string}
const message = (cause: unknown) => cause instanceof Error ? cause.message : '暂时无法读取，请稍后核对。'
const inaccessible = (cause: unknown) => [401, 403, 404].includes((cause as {status?: number})?.status || 0)

function pendingOperation(roundId: string) {
  const url = new URL(location.href)
  return url.searchParams.get('factRound') === roundId ? url.searchParams.get('factAnswer') : null
}
function keepOperation(roundId: string, operationId: string | null) {
  const url = new URL(location.href)
  if (operationId) {url.searchParams.set('factRound', roundId); url.searchParams.set('factAnswer', operationId)}
  else if (url.searchParams.get('factRound') === roundId) {url.searchParams.delete('factRound'); url.searchParams.delete('factAnswer')}
  writeAuditAddress(url)
}
function pendingSample(roundId: string): PendingSample | null {
  const url = new URL(location.href)
  const operationId = url.searchParams.get('factSample'), answerId = url.searchParams.get('factSampleAnswer'), itemKey = url.searchParams.get('factSampleItem')
  return url.searchParams.get('factSampleRound') === roundId && operationId && answerId && itemKey ? {operationId, answerId, itemKey} : null
}
function keepSample(roundId: string, sample: PendingSample | null) {
  const url = new URL(location.href)
  if (sample) {
    url.searchParams.set('factSampleRound', roundId); url.searchParams.set('factSample', sample.operationId); url.searchParams.set('factSampleAnswer', sample.answerId); url.searchParams.set('factSampleItem', sample.itemKey)
  } else if (url.searchParams.get('factSampleRound') === roundId) for (const key of ['factSampleRound', 'factSample', 'factSampleAnswer', 'factSampleItem']) url.searchParams.delete(key)
  writeAuditAddress(url)
}

/** Answers apply only to this frozen original and round. Machine findings stay untouched. */
export function FactQuestions({roundId, files, findings = [], fileVersionId, adapter: providedAdapter}: {roundId: string; files: FileVersion[]; findings?: MachineFinding[]; fileVersionId?: string; adapter?: FactAnswersAdapter}) {
  const inheritedAdapter = useContext(FactAnswersContext)
  const adapter = providedAdapter ?? inheritedAdapter
  const [view, setView] = useState<FactAnswersView | null>(null)
  const [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false)
  const [pending, setPending] = useState(() => pendingOperation(roundId))
  const [samplePending, setSamplePending] = useState(() => pendingSample(roundId))
  const [expanded, setExpanded] = useState(() => Boolean(pendingOperation(roundId) || pendingSample(roundId)))
  const generation = useRef(0), busy = useRef(false), mounted = useRef(true)
  const read = useCallback(async () => {
    const token = ++generation.current
    setLoading(true)
    try {
      const next = await adapter.read(roundId)
      if (token !== generation.current || !mounted.current) return
      if (next.round_id !== roundId || next.model_calls !== 0) throw new Error('事实问题不属于本次审核，请重新读取。')
      setView(current => current && current.revision > next.revision ? current : next)
      setError('')
    } catch (cause) {
      if (token === generation.current && mounted.current) {setError(message(cause)); if (inaccessible(cause)) setView(null)}
    } finally {if (token === generation.current && mounted.current) setLoading(false)}
  }, [adapter, roundId])
  useEffect(() => {
    mounted.current = true; void read()
    return () => {mounted.current = false; generation.current++}
  }, [read])

  async function accepted(receipt: FactAnswerReceipt) {
    if (!mounted.current) return
    setPending(null); keepOperation(roundId, null)
    setNotice(receipt.answer.value === null ? '已记录“看不清”。相关条款仍为无法判断。' : '回答已保存，只重算相关条款。原机器结果保留，未调用模型。')
    await read()
  }
  async function apply(question: FactQuestion, value: boolean | string | null) {
    if (!view || loading || pending || busy.current) return
    busy.current = true; setSaving(true); setError(''); setNotice('')
    const input = {operation_id: crypto.randomUUID(), expected_revision: view.revision, file_version_id: question.file_version_id, field: question.field, expected_question_fingerprint: question.fingerprint, value}
    setPending(input.operation_id); keepOperation(roundId, input.operation_id)
    try {await accepted(checkedFactAnswer(await adapter.apply(roundId, input), roundId, input.operation_id, input))}
    catch (cause) {
      if (!mounted.current) return
      if (definiteFactAnswerRejection(cause)) {
        setPending(null); keepOperation(roundId, null)
        if ((cause as {status?: number})?.status === 409) {
          setView(null)
          await read()
          setNotice('问题或回答已更新，已重新读取。请核对当前内容后再明确提交。')
        } else {if (inaccessible(cause)) setView(null); setError(`${message(cause)} 本次回答未保存。`)}
      } else setError('回答的保存结果尚未确定。请查看原操作，不要重复提交。')
    } finally {busy.current = false; if (mounted.current) setSaving(false)}
  }
  async function recover() {
    if (!pending || busy.current) return
    busy.current = true; setSaving(true)
    try {
      const receipt = await adapter.readOperation(roundId, pending)
      if (!mounted.current) return
      if (receipt) await accepted(checkedFactAnswer(receipt, roundId, pending))
      else setError('尚未读到原操作结果，请稍后继续读取。不会自动重交回答。')
    } catch (cause) {if (mounted.current) {if (inaccessible(cause)) setView(null); setError(`${message(cause)} 原操作仍待确认。`)}}
    finally {busy.current = false; if (mounted.current) setSaving(false)}
  }

  const questions = view?.questions.filter(row => !fileVersionId || row.file_version_id === fileVersionId) ?? []
  const groups = groupFactQuestions(questions)
  const answers = view?.answers.filter(row => !fileVersionId || row.file_version_id === fileVersionId) ?? []
  const results = view?.results.filter(row => !fileVersionId || row.sources.some(source => source.file_version_id === fileVersionId)) ?? []
  // Nothing to answer means nothing to show; a first read in progress does not claim a row either.
  if (!pending && !error && (!view || !questions.length && !answers.length && !results.length)) return null
  const filename = (id: string) => files.find(file => file.id === id)?.original_filename || '本轮原件'
  return <section aria-label="原件事实确认" className="min-w-0 border-b px-6 py-2 max-sm:px-4" data-fact-answer-state={saving ? 'saving' : pending ? 'unknown' : 'ready'}>
    <Disclosure open={expanded || Boolean(error)} onOpenChange={setExpanded} summary={<><span className="font-medium">原件事实确认</span><span className="ml-3 text-muted-foreground">{pending ? '回答结果待确认' : error ? '读取失败' : loading && !view ? '读取中…' : `待确认 ${questions.filter(question => !question.answered).length} 项 · 人工确认结果 ${results.length} 项`}</span></>}>
    <div className="space-y-4 py-4">
    <header className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">核对原件中的事实</h2><p className="mt-1 text-sm text-muted-foreground">只回答原件里的事实，不会修改规则，也不消耗额度。</p></div><Button variant="ghost" size="icon" aria-label="读取最新事实问题" disabled={loading || saving} onClick={() => void read()}><RefreshCw className={loading ? 'animate-spin' : ''}/></Button></header>
    {loading && !view && <p role="status" className="text-sm text-muted-foreground">正在读取可确认的问题…</p>}
    {error && <Alert variant="destructive"><AlertTitle>{pending ? '回答结果待确认' : '事实确认暂不可用'}</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {pending && <div className="flex flex-wrap items-center gap-3 text-sm"><p role="status">{saving ? '正在保存并核对回答…' : '有一次回答等待确认，当前不能再次提交。'}</p><Button variant="outline" disabled={saving} onClick={() => void recover()}>查看原回答操作</Button></div>}
    {groups.map(group => {
      const question = (row: FactQuestion) => <FactQuestionForm key={`${row.file_version_id}:${row.field}:${row.fingerprint}`} question={row} file={files.find(file => file.id === row.file_version_id)} roundId={roundId} disabled={loading || saving || Boolean(pending)} onAnswer={value => void apply(row, value)}/>
      return <section key={group.fileVersionId} aria-label={`${filename(group.fileVersionId)}的事实问题`} className="space-y-4 border-t pt-4"><h3 className="font-medium"><FileName name={filename(group.fileVersionId)}/></h3>{group.visible.map(question)}{group.folded.length > 0 && <Disclosure  summary={<>更多待确认事实（{group.folded.length}）</>}><div className="space-y-4">{group.folded.map(question)}</div></Disclosure>}</section>
    })}
    {results.length > 0 && <section aria-label="人工确认结果" className="space-y-4 border-t pt-4"><h3 className="font-medium">人工确认结果</h3><p className="text-xs text-muted-foreground">下方单独记录使用人工回答的确定性重算，原机器结果未被替换。</p>{results.map(result => {
      const machine = findings.find(row => row.item_key === result.item_key)
      const clause = questions.find(row => row.item_keys.includes(result.item_key))?.clause_ids.join('、')
      const sampleAnswer = answers.find(answer => result.answer_ids.includes(answer.operation_id) && answer.value !== null)
      return <article key={result.item_key} className="space-y-2 border-l-2 pl-3 text-sm"><h4 className="font-medium">{machine?.item || clause || result.item_key}</h4><p>原机器结果：{machine ? resultLabel[machine.status] : '请在原审核记录中查看'}</p><div className="flex items-center gap-2"><Badge variant="outline">人工确认</Badge><Badge variant={resultTone(result.status)}>{resultLabel[result.status]}</Badge></div><p className="whitespace-pre-wrap">{result.suggestions}</p><p className="text-xs text-muted-foreground">回答人：{result.confirmed_by.join('、')} · <time dateTime={result.confirmed_at}>{displayTime(result.confirmed_at)}</time></p>{result.sources.map(source => <p key={`${source.file_version_id}:${source.field}`} className="break-words text-xs text-muted-foreground">依据：{filename(source.file_version_id)} · {sourceLocation(source.locator)} · {answers.find(answer => answer.field === source.field && answer.file_version_id === source.file_version_id)?.prompt || source.field}：{factValueLabel(source.value)}</p>)}{sampleAnswer && adapter.saveSample && adapter.readSample && <FactSampleForm roundId={roundId} answerId={sampleAnswer.operation_id} itemKey={result.item_key} adapter={adapter} pending={samplePending} onPending={value => {setSamplePending(value); keepSample(roundId, value)}}/>}</article>
    })}</section>}
    {answers.length > 0 && <Button variant="outline" asChild><a href={`${apiUrl}/audit-rounds/${encodeURIComponent(roundId)}/fact-answers/report`} download="original-fact-confirmations.pdf">下载人工确认报告</a></Button>}
    {answers.length > 0 && <Disclosure className="border-t pt-3 text-sm" summary={<>已记录的原件回答（{answers.length}）</>}><div className="space-y-3">{answers.map(answer => <article key={answer.operation_id} className="space-y-1"><p>{answer.prompt}：{factValueLabel(answer.value)}</p><p className="text-xs text-muted-foreground">{filename(answer.file_version_id)} · {sourceLocation(answer.locator)} · {answer.answered_by} · <time dateTime={answer.answered_at}>{displayTime(answer.answered_at)}</time></p></article>)}</div></Disclosure>}
    </div>
    </Disclosure>
  </section>
}

function FactSampleForm({roundId, answerId, itemKey, adapter, pending, onPending}: {roundId: string; answerId: string; itemKey: string; adapter: FactAnswersAdapter; pending: PendingSample | null; onPending: (value: PendingSample | null) => void}) {
  const matching = pending?.answerId === answerId && pending?.itemKey === itemKey
  const [open, setOpen] = useState(matching), [expected, setExpected] = useState<FactAnswerSampleRequest['expected_status'] | ''>(''), [rationale, setRationale] = useState('')
  const [saving, setSaving] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(false)
  const busy = useRef(false), mounted = useRef(true)
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  async function save() {
    if (!expected || !rationale.trim() || pending || busy.current || !adapter.saveSample) return
    busy.current = true; setSaving(true); setError('')
    const operation = {operationId: crypto.randomUUID(), answerId, itemKey}
    onPending(operation)
    try {
      await adapter.saveSample(roundId, answerId, {operation_id: operation.operationId, item_key: itemKey, expected_status: expected, rationale: rationale.trim()})
      if (mounted.current) {onPending(null); setSaved(true)}
    } catch (cause) {
      if (!mounted.current) return
      const status = (cause as {status?: number})?.status
      if (status && [400, 401, 403, 404, 409, 422].includes(status)) {onPending(null); setError(`${message(cause)} 样本未保存。`)}
      else setError('样本保存结果尚未确定。请读取原样本操作，不要重复提交。')
    } finally {busy.current = false; if (mounted.current) setSaving(false)}
  }
  async function recover() {
    if (!matching || !pending || busy.current || !adapter.readSample) return
    busy.current = true; setSaving(true)
    try {
      const sample = await adapter.readSample(roundId, pending.operationId)
      if (!mounted.current) return
      if (sample) {onPending(null); setSaved(true); setError('')}
      else setError('尚未读到原样本操作。请稍后继续读取，不会自动重交。')
    } catch (cause) {if (mounted.current) setError(message(cause))}
    finally {busy.current = false; if (mounted.current) setSaving(false)}
  }
  if (saved) return <p role="status">已加入优化样本。未启动模型优化，原机器结果保留。</p>
  return <div className="space-y-2">
    <Button variant="outline" size="sm" onClick={() => setOpen(value => !value)} aria-expanded={open}>{open ? '收起样本选择' : '作为优化样本'}</Button>
    {open && <form className="space-y-3" onSubmit={event => {event.preventDefault(); void save()}}>
      <p className="text-xs text-muted-foreground">确认本条款的正确结果和依据后，才加入优化样本。不会自动优化或发布规则。</p>
      <fieldset disabled={saving || Boolean(pending)} className="flex flex-wrap gap-4"><legend className="mb-2">本条款的正确结果</legend>{(['PASS', 'NG', 'NA'] as const).map(value => <label key={value} className="flex items-center gap-2"><input type="radio" name={`sample-${answerId}-${itemKey}`} checked={expected === value} onChange={() => setExpected(value)}/>{resultLabel[value]}</label>)}</fieldset>
      <Input aria-label="样本判定依据" value={rationale} maxLength={7000} disabled={saving || Boolean(pending)} onChange={event => setRationale(event.target.value)}/>
      <Button type="submit" size="sm" disabled={!expected || !rationale.trim() || saving || Boolean(pending)}>确认加入优化样本</Button>
      {matching && <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => void recover()}>读取原样本操作</Button>}
      {error && <p role="alert">{error}</p>}
    </form>}
  </div>
}

function FactQuestionForm({question, file, roundId, disabled, onAnswer}: {question: FactQuestion; file?: FileVersion; roundId: string; disabled: boolean; onAnswer: (value: boolean | string | null) => void}) {
  const [choice, setChoice] = useState(''), [text, setText] = useState(''), [open, setOpen] = useState(false)
  const location = sourceLocation(question.locator)
  const focus = useMemo(() => ({location, requestId: 0}), [location])
  const id = `fact-${question.fingerprint}`
  const ready = choice === 'unknown' || question.answer_type === 'boolean' && ['yes', 'no'].includes(choice) || question.answer_type === 'text' && choice === 'text' && Boolean(text.trim())
  return <article className="space-y-3 rounded-md border p-4" aria-label={question.prompt}>
    <div className="space-y-1"><h4 className="font-medium">{question.prompt}</h4><p className="text-xs text-muted-foreground">{location} · 关联条款：{question.clause_ids.join('、')}（{question.item_keys.length} 项结果）</p></div>
    {file && <Button type="button" variant="outline" size="sm" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? '收起原件' : '对照原件'}</Button>}
    {open && file && <div className="flex h-[26rem] min-w-0"><RemoteSourcePane nativeOffice file={{file_id: file.file_id, file_version_id: file.id, original_filename: file.original_filename, role: 'target', rule_version_id: null}} roundId={roundId} focus={focus}/></div>}
    <form onSubmit={event => {event.preventDefault(); if (ready && !disabled) onAnswer(choice === 'unknown' ? null : question.answer_type === 'boolean' ? choice === 'yes' : text.trim())}}>
      <fieldset disabled={disabled} className="space-y-3"><legend className="sr-only">{question.prompt}</legend><div className="flex flex-wrap gap-4">
        {question.answer_type === 'boolean' ? [['yes', '是'], ['no', '否']].map(([value, label]) => <label key={value} className="flex items-center gap-2 text-sm"><input type="radio" name={id} checked={choice === value} onChange={() => setChoice(value)}/>{label}</label>) : <label className="flex items-center gap-2 text-sm"><input type="radio" name={id} checked={choice === 'text'} onChange={() => setChoice('text')}/>填写原件内容</label>}
        <label className="flex items-center gap-2 text-sm"><input type="radio" name={id} checked={choice === 'unknown'} onChange={() => setChoice('unknown')}/>看不清</label>
      </div>{question.answer_type === 'text' && choice === 'text' && <Input aria-label="原件内容" value={text} onChange={event => setText(event.target.value)} maxLength={2000}/>}
      {choice === 'unknown' && <p className="text-xs text-muted-foreground">只记录看不清，相关条款保持无法判断。</p>}<Button type="submit" size="sm" disabled={!ready || disabled}>保存回答</Button></fieldset>
    </form>
  </article>
}
