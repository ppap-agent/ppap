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
import {checkedFactAnswer, definiteFactAnswerRejection, factValueLabel, groupFactQuestions, type FactAnswerReceipt, type FactAnswersAdapter, type FactAnswersView, type FactQuestion} from './fact-answers-contract'
import {FactAnswersContext} from './fact-answers-context'
import {resultLabel, resultTone} from './status-labels'
import {writeAuditAddress} from './routes'

type FileVersion = components['schemas']['FileVersion']
type MachineFinding = Pick<components['schemas']['Finding'], 'item_key' | 'item' | 'status'>
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

/** Answers apply only to this frozen original and round. Machine findings stay untouched. */
export function FactQuestions({roundId, files, findings = [], fileVersionId, adapter: providedAdapter}: {roundId: string; files: FileVersion[]; findings?: MachineFinding[]; fileVersionId?: string; adapter?: FactAnswersAdapter}) {
  const inheritedAdapter = useContext(FactAnswersContext)
  const adapter = providedAdapter ?? inheritedAdapter
  const [view, setView] = useState<FactAnswersView | null>(null)
  const [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false)
  const [pending, setPending] = useState(() => pendingOperation(roundId))
  const [expanded, setExpanded] = useState(() => Boolean(pendingOperation(roundId)))
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
      } else setError('回答的保存结果尚未确定。请读取原操作，不要重复提交。')
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
    } catch (cause) {if (mounted.current) {if (inaccessible(cause)) setView(null); setError(`${message(cause)} 原操作仍待核对。`)}}
    finally {busy.current = false; if (mounted.current) setSaving(false)}
  }

  const questions = view?.questions.filter(row => !fileVersionId || row.file_version_id === fileVersionId) ?? []
  const groups = groupFactQuestions(questions)
  const answers = view?.answers.filter(row => !fileVersionId || row.file_version_id === fileVersionId) ?? []
  const results = view?.results.filter(row => !fileVersionId || row.sources.some(source => source.file_version_id === fileVersionId)) ?? []
  if (view && !questions.length && !answers.length && !results.length && !pending && !error) return null
  const filename = (id: string) => files.find(file => file.id === id)?.original_filename || '本轮原件'
  return <section aria-label="原件事实确认" className="min-w-0 border-b px-6 py-2 max-sm:px-4" data-fact-answer-state={saving ? 'saving' : pending ? 'unknown' : 'ready'}>
    <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary className="cursor-pointer text-sm"><span className="font-medium">原件事实确认</span><span className="ml-3 text-muted-foreground">{pending ? '回答结果待核对' : error ? '读取失败，可展开重试' : loading && !view ? '读取中…' : `待确认 ${questions.filter(question => !question.answered).length} 项 · 人工确认结果 ${results.length} 项`}</span></summary>
    <div className="space-y-4 py-4">
    <header className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">核对原件中的事实</h2><p className="mt-1 text-sm text-muted-foreground">只回答这份原件中的具体事实，不修改规则。回答不消耗模型额度，也不会自动加入训练样本。</p></div><Button variant="ghost" size="icon" aria-label="读取最新事实问题" disabled={loading || saving} onClick={() => void read()}><RefreshCw className={loading ? 'animate-spin' : ''}/></Button></header>
    {loading && !view && <p role="status" className="text-sm text-muted-foreground">正在读取可确认的问题…</p>}
    {error && <Alert variant="destructive"><AlertTitle>{pending ? '回答结果待核对' : '事实确认暂不可用'}</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {pending && <div className="flex flex-wrap items-center gap-3 text-sm"><p role="status">{saving ? '正在保存并核对回答…' : '有一次回答等待核对，当前不能再次提交。'}</p><Button variant="outline" disabled={saving} onClick={() => void recover()}>读取原回答操作</Button></div>}
    {groups.map(group => {
      const question = (row: FactQuestion) => <FactQuestionForm key={`${row.file_version_id}:${row.field}:${row.fingerprint}`} question={row} file={files.find(file => file.id === row.file_version_id)} roundId={roundId} disabled={loading || saving || Boolean(pending)} onAnswer={value => void apply(row, value)}/>
      return <section key={group.fileVersionId} aria-label={`${filename(group.fileVersionId)}的事实问题`} className="space-y-4 border-t pt-4"><h3 className="font-medium"><FileName name={filename(group.fileVersionId)}/></h3>{group.visible.map(question)}{group.folded.length > 0 && <details className="space-y-4"><summary className="cursor-pointer text-sm">更多待确认事实（{group.folded.length}）</summary><div className="mt-4 space-y-4">{group.folded.map(question)}</div></details>}</section>
    })}
    {results.length > 0 && <section aria-label="人工确认结果" className="space-y-4 border-t pt-4"><h3 className="font-medium">人工确认结果</h3><p className="text-xs text-muted-foreground">下方单独记录使用人工回答的确定性重算，原机器结果未被替换。</p>{results.map(result => {
      const machine = findings.find(row => row.item_key === result.item_key)
      const clause = questions.find(row => row.item_keys.includes(result.item_key))?.clause_ids.join('、')
      return <article key={result.item_key} className="space-y-2 border-l-2 pl-3 text-sm"><h4 className="font-medium">{machine?.item || clause || result.item_key}</h4><p>原机器结果：{machine ? resultLabel[machine.status] : '请在原审核记录中查看'}</p><div className="flex items-center gap-2"><Badge variant="outline">人工确认</Badge><Badge variant={resultTone(result.status)}>{resultLabel[result.status]}</Badge></div><p className="whitespace-pre-wrap">{result.suggestions}</p><p className="text-xs text-muted-foreground">回答人：{result.confirmed_by.join('、')} · <time dateTime={result.confirmed_at}>{new Date(result.confirmed_at).toLocaleString()}</time></p>{result.sources.map(source => <p key={`${source.file_version_id}:${source.field}`} className="break-words text-xs text-muted-foreground">依据：{filename(source.file_version_id)} · {sourceLocation(source.locator)} · {answers.find(answer => answer.field === source.field && answer.file_version_id === source.file_version_id)?.prompt || source.field}：{factValueLabel(source.value)}</p>)}</article>
    })}</section>}
    {answers.length > 0 && <Button variant="outline" asChild><a href={`${apiUrl}/audit-rounds/${encodeURIComponent(roundId)}/fact-answers/report`} download="original-fact-confirmations.pdf">下载人工确认报告</a></Button>}
    {answers.length > 0 && <details className="border-t pt-3 text-sm"><summary className="cursor-pointer">已记录的原件回答（{answers.length}）</summary><div className="mt-3 space-y-3">{answers.map(answer => <article key={answer.operation_id} className="space-y-1"><p>{answer.prompt}：{factValueLabel(answer.value)}</p><p className="text-xs text-muted-foreground">{filename(answer.file_version_id)} · {sourceLocation(answer.locator)} · {answer.answered_by} · <time dateTime={answer.answered_at}>{new Date(answer.answered_at).toLocaleString()}</time></p></article>)}</div></details>}
    </div>
    </details>
  </section>
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
