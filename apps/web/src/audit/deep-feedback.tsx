import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {Check, ChevronDown, ChevronUp, RefreshCw, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {resultLabel, resultTone, type ResultStatus} from './status-labels'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Label} from '../components/ui/label'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Textarea} from '../components/ui/textarea'
import {FileName} from '../journey/file-name'
import {sourceLocation} from '../journey/product'
import {RemoteSourcePane} from '../journey/remote-source'
import {definiteRuleChangeRejection, nativeDeepFeedbackAdapter, type DeepFeedbackAdapter, type DeepFeedbackView, type RuleChangeReceipt, type RuleChangeRequest} from './deep-feedback-contract'
import type {AuditEvidence, OriginalRenderer} from './review-content'
import {writeAuditAddress} from './routes'
import {Disclosure} from '../components/disclosure'

type FileVersion = components['schemas']['FileVersion']
type RuleItem = components['schemas']['RuleItem']
type FeedbackRule = {documentId: string; filename: string; rule: RuleItem}
type Question = DeepFeedbackView['clarifications'][number]
type Change = Omit<RuleChangeRequest, 'operation_id' | 'expected_revision'>
const message = (cause: unknown) => cause instanceof Error ? cause.message : '暂时无法读取，请稍后核对。'
const inaccessible = (cause: unknown) => [401, 403, 404].includes((cause as {status?: number})?.status || 0)

function pendingOperation(roundId: string) {
  const url = new URL(location.href)
  return url.searchParams.get('feedbackRound') === roundId ? url.searchParams.get('ruleChange') : null
}
function keepOperation(roundId: string, operationId: string | null) {
  const url = new URL(location.href)
  if (operationId) {url.searchParams.set('feedbackRound', roundId); url.searchParams.set('ruleChange', operationId)}
  else {url.searchParams.delete('feedbackRound'); url.searchParams.delete('ruleChange')}
  writeAuditAddress(url)
}

/** One view over server-owned questions, inputs and operations. Drafts are not business facts. */
export function DeepFeedback({roundId, files, rules, renderOriginal, adapter = nativeDeepFeedbackAdapter, onBasisChanged}: {roundId: string; files: FileVersion[]; rules: FeedbackRule[]; renderOriginal?: OriginalRenderer; adapter?: DeepFeedbackAdapter; onBasisChanged?: (changed: boolean) => void}) {
  const [storedView, setView] = useState<DeepFeedbackView | null>(null), [error, setError] = useState('')
  const [selected, setSelected] = useState(''), [open, setOpen] = useState(true)
  const [loading, setLoading] = useState(false), [saving, setSaving] = useState(false)
  const [pending, setPending] = useState(() => pendingOperation(roundId))
  const [receipt, setReceipt] = useState<RuleChangeReceipt | null>(null)
  const [notice, setNotice] = useState('')
  const generation = useRef(0), toggle = useRef<HTMLButtonElement>(null)
  const view = storedView?.round_id === roundId ? storedView : null
  const read = useCallback(async () => {
    const token = ++generation.current
    setLoading(true)
    try {
      const next = await adapter.read(roundId)
      if (token !== generation.current) return
      if (next.round_id !== roundId) throw new Error('读取结果不属于本次审核，请重新读取。')
      setView(current => current && next.revision < current.revision ? current : next)
      setError('')
    } catch (cause) {if (token === generation.current) {setError(message(cause)); if (inaccessible(cause)) setView(null)}}
    finally {if (token === generation.current) setLoading(false)}
  }, [adapter, roundId])
  useEffect(() => {void read(); return () => {generation.current++}}, [read])
  useEffect(() => {if (view) onBasisChanged?.(view.input_changed || view.current_round_id !== roundId)}, [view, roundId, onBasisChanged])
  useEffect(() => {
    if (!view?.results.some(result => result.current && ['queued', 'running'].includes(result.execution))) return
    const timer = setInterval(() => void read(), 4000)
    return () => clearInterval(timer)
  }, [view?.results, read])

  const question = view?.clarifications.find(row => row.id === selected) ?? view?.clarifications[0]
  const analysisTargets = view?.results.find(result => result.current)?.inputs.filter(input => input.role === 'target').map(input => files.find(file => file.id === input.file_version_id)?.original_filename || String(input.original_filename || '本次待审文件')) || []
  async function accepted(saved: RuleChangeReceipt) {
    setReceipt(saved); setPending(null); keepOperation(roundId, null)
    setNotice(saved.analysis_round_id ? '解释已保存，新输入已进入分析。下方会显示实际分析结果。' : '说明已保存，尚未开始新的分析。')
    await read()
  }
  async function apply(change: Change) {
    if (!view || view.round_id !== roundId || pending || saving) return
    const input: RuleChangeRequest = {...change, operation_id: crypto.randomUUID(), expected_revision: view.revision}
    setPending(input.operation_id); keepOperation(roundId, input.operation_id); setSaving(true); setNotice(''); setError('')
    try {await accepted(await adapter.apply(roundId, input, view.workspace_id))}
    catch (cause) {
      if (definiteRuleChangeRejection(cause)) {
        if (inaccessible(cause)) setView(null)
        setPending(null); keepOperation(roundId, null)
        setError(`${message(cause)} 本次修改未应用，请读取最新依据后再核对。`)
      } else setError('提交结果尚未确定。请查看原操作，不要再次提交。已选内容仍保留。')
    } finally {setSaving(false)}
  }
  async function recover() {
    if (!pending || saving) return
    setSaving(true)
    try {
      const current = view ?? await adapter.read(roundId)
      if (current.round_id !== roundId) throw new Error('读取结果不属于本次审核，请重新读取。')
      const saved = await adapter.readOperation(roundId, pending, current.workspace_id)
      if (saved) await accepted(saved)
      else setError('尚未读到原操作结果。当前状态仍未确定，可以稍后继续读取。')
    } catch (cause) {if (inaccessible(cause)) setView(null); setError(`${message(cause)} 原操作仍待确认。`)}
    finally {setSaving(false)}
  }
  function close() {setOpen(false); toggle.current?.focus()}

  return <section aria-label="解释与重新分析" className="min-w-0 border-b" data-feedback-state={saving ? 'saving' : pending ? 'unknown' : receipt ? 'saved' : 'ready'}>
    <header className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 max-sm:px-4">
      <div className="min-w-0"><h2 className="text-lg font-semibold">解释与重新分析</h2><p role="status" className="mt-1 text-sm text-muted-foreground">{view ? view.clarifications.length ? `${view.clarifications.length} 项解释待确认 · 其余已知判断仍保留` : '当前没有待确认解释' : loading ? '正在读取解释问题…' : '解释问题尚未读取'}</p></div>
      <div className="flex items-center gap-2"><Button variant="ghost" size="icon" aria-label="读取最新解释与分析" disabled={loading || saving} onClick={() => void read()}><RefreshCw className={loading ? 'animate-spin' : ''}/></Button><Button ref={toggle} variant="outline" aria-expanded={open} aria-controls="deep-feedback-content" onClick={() => setOpen(value => !value)}>{open ? <ChevronUp/> : <ChevronDown/>}{open ? '收起解释' : '查看解释'}</Button></div>
    </header>
    <div id="deep-feedback-content" hidden={!open} className="min-w-0">
      <div className="space-y-3 px-6 empty:hidden max-sm:px-4">
        {error && <Alert variant="destructive"><AlertTitle>{pending ? '操作结果待确认' : '解释暂未更新'}</AlertTitle><AlertDescription><p>{error}</p>{pending && <Button variant="outline" disabled={saving} onClick={() => void recover()}>查看原操作</Button>}</AlertDescription></Alert>}
        {notice && <Alert><Check/><AlertTitle>{receipt?.analysis_round_id ? '已保存并接收新分析' : '说明已保存'}</AlertTitle><AlertDescription>{notice} 确认解释不保证审核通过。</AlertDescription></Alert>}
        {pending && !error && <p role="status" className="text-sm text-muted-foreground">{saving ? '正在保存明确修改…' : '原操作结果待确认。'}{!saving && <Button variant="link" onClick={() => void recover()}>查看原操作</Button>}</p>}
      </div>
      {view && <>
        {view.input_changed && <div className="space-y-3 px-6 py-3 text-sm max-sm:px-4"><p role="status">说明已保存。下方结果使用此前输入，新的依据尚未产生分析结果。</p><p className="text-xs text-muted-foreground">将重新分析本任务的全部文件{analysisTargets.length > 0 && `（${analysisTargets.length} 份）：${analysisTargets.join('、')}`}。</p><Button variant={question ? 'outline' : 'default'} disabled={saving || Boolean(pending)} onClick={() => void apply({intent: 'save_and_analyze', confirmation_source: 'user'})}>按已保存说明开始分析</Button></div>}
        {view.appendix.length > 0 && <Disclosure className="mx-6 my-4 border-b pb-4 text-sm max-sm:mx-4" summary={<>本次已确认说明（{view.appendix.length}）</>}><div className="space-y-3">{view.appendix.map(entry => <ConfirmedExplanation key={entry.id} entry={entry} files={files}/>)}</div></Disclosure>}
        {view.clarifications.length > 1 && <nav aria-label="待确认解释" className="flex flex-wrap gap-2 px-6 py-3 max-sm:px-4">{view.clarifications.map((row, at) => <Button key={row.id} variant={row.id === question?.id ? 'secondary' : 'ghost'} aria-pressed={row.id === question?.id} onClick={() => setSelected(row.id)}>解释 {at + 1}<span className="max-w-44 truncate">{row.question}</span></Button>)}</nav>}
        {question ? <DeepQuestion key={question.id + question.fingerprint} question={question} results={view.results} rules={rules} files={files} analysisTargets={analysisTargets} roundId={roundId} renderOriginal={renderOriginal} disabled={saving || Boolean(pending)} onApply={change => void apply(change)} onClose={close}/> : <div className="px-6 pb-5 text-sm text-muted-foreground max-sm:px-4">已生成的判断和原件仍可在下方查看。未解决的内容不会被默认判为通过。</div>}
        {view.current_round_id !== roundId && <section aria-label="新输入分析" className="space-y-3 border-t px-6 py-5 max-sm:px-4"><h3 className="font-semibold">新输入的分析</h3><p className="text-sm text-muted-foreground">{view.input_changed ? '新说明已保存，下方计算尚未采用这次修改。' : '已采用确认后的规则说明。此前判断保留在下方，不作为新输入的当前结论。'}</p><Button variant="outline" onClick={() => void read()} disabled={loading}>读取新分析结果</Button><DeepResults view={view} files={files}/></section>}
      </>}
    </div>
  </section>
}


function ConfirmedExplanation({entry, files}: {entry: DeepFeedbackView['appendix'][number]; files: FileVersion[]}) {
  const filename = (id: string) => files.find(file => file.id === id)?.original_filename || '不可读取的文件'
  return <article className="space-y-2 border-l-2 pl-3 text-sm"><p className="whitespace-pre-wrap">{entry.content}</p><div aria-label="适用范围" className="space-y-1 text-xs text-muted-foreground"><p>含义所在文件：{entry.scope.document_ids.map(filename).join('、')}</p><p>适用审核规则：{entry.scope.rule_targets ? entry.scope.rule_targets.map(target => `${filename(target.document_id)} · ${target.rule_id}`).join('、') : entry.scope.rule_ids.join('、')}</p></div></article>
}

function DeepQuestion({question, results = [], rules, files, analysisTargets, roundId, renderOriginal, disabled, onApply, onClose}: {question: Question; results?: DeepFeedbackView['results']; rules: FeedbackRule[]; files: FileVersion[]; analysisTargets: string[]; roundId: string; renderOriginal?: OriginalRenderer; disabled: boolean; onApply: (change: Change) => void; onClose: () => void}) {
  const scope = question.scope
  const [candidate, setCandidate] = useState(''), [content, setContent] = useState('')
  const [kind, setKind] = useState<NonNullable<RuleChangeRequest['kind']>>('interpretation')
  const [documents, setDocuments] = useState<string[]>(scope.document_ids)
  const [expanded, setExpanded] = useState<string[]>(['0', '1'])
  const [visited, setVisited] = useState<string[]>(['0', '1'])
  const candidates = question.candidates
  const affects = (documentId: string | undefined, ruleId: string | undefined) => Boolean(documentId && ruleId && (scope.rule_targets
    ? scope.rule_targets.some(target => target.document_id === documentId && target.rule_id === ruleId)
    : scope.document_ids.includes(documentId) && scope.rule_ids.includes(ruleId)))
  const related = results.filter(result => result.current).flatMap(result => result.findings.flatMap(finding => {
    const locator = finding.locator as components['schemas']['FindingLocator'] | null
    return affects(locator?.file_version_id, locator?.rule_item_id) ? locator?.semantic?.sources || [] : []
  }))
  const located: Question['sources'] = [...question.sources]
  for (const source of related) if (!located.some(row => row.document_id === source.file_version_id)) located.push({document_id: source.file_version_id, locator: source.locator, excerpt: source.excerpt})
  const evidence = [...located, ...files.filter(file => scope.document_ids.includes(file.id) && !located.some(source => source.document_id === file.id)).map(file => ({document_id: file.id, locator: null}))]
  const affectedRules = rules.filter(item => affects(item.documentId, item.rule.id))
  const canSubmit = !disabled && candidate !== '__none' && Boolean(candidate && content.trim() && documents.length)
  function choose(id: string) {setCandidate(id); setContent(candidates.find(row => row.id === id)?.explanation || '')}
  function submit(intent: RuleChangeRequest['intent']) {
    if (!canSubmit) return
    const exact = candidates.find(row => row.id === candidate && row.explanation.trim() === content.trim())
    onApply({...(question.id ? {question_id: question.id, expected_question_fingerprint: question.fingerprint} : {}), ...(exact && kind !== 'standard_change' ? {candidate_id: candidate} : {}), kind, content: content.trim(), scope: {...scope, document_ids: documents}, intent, confirmation_source: 'user'})
  }
  return <div className="grid min-w-0 min-[1100px]:grid-cols-[minmax(0,1.4fr)_minmax(20rem,1fr)]">
    <section aria-label="待确认问题的原件对照" className="min-w-0 border-b p-6 max-sm:p-4 min-[1100px]:border-r min-[1100px]:border-b-0">
      <h3 className="font-semibold">先对照原文</h3><p className="mt-2 text-sm leading-relaxed">{question.question}</p>
      <div className="mt-5 grid min-w-0 gap-5 min-[1280px]:grid-cols-2">{evidence.map((source, at) => <FeedbackEvidence key={`${source.document_id}-${at}`} source={source} at={at} file={files.find(row => row.id === source.document_id)} open={expanded.includes(String(at))} visited={visited.includes(String(at))} roundId={roundId} renderOriginal={renderOriginal} onToggle={() => {if (!expanded.includes(String(at))) setVisited(current => current.includes(String(at)) ? current : [...current, String(at)]); setExpanded(current => current.includes(String(at)) ? current.filter(id => id !== String(at)) : [...current, String(at)])}}/>)}</div>
      {affectedRules.length > 0 && <section aria-label="待确认解释的规则原文" className="mt-6 space-y-3 border-t pt-4"><h4 className="text-sm font-medium">相关审核规则</h4>{Array.from(new Map(affectedRules.map(item => [`${item.documentId}:${item.rule.id}`, item])).values()).map(item => <article key={`${item.documentId}:${item.rule.id}`} className="space-y-2 border-l-2 pl-3 text-sm leading-relaxed"><p className="text-xs text-muted-foreground">{item.filename}</p><p className="whitespace-pre-wrap"><span className="font-medium">{item.rule.id} · </span>{item.rule.rule_to_check}</p></article>)}</section>}
    </section>
    <form aria-label="确认规则解释" className="min-w-0 space-y-6 p-6 max-sm:p-4" onSubmit={event => {event.preventDefault(); submit('save_and_analyze')}}>
      <header className="flex items-start justify-between gap-3"><div><Badge variant="outline">需要确认</Badge><h3 className="mt-3 text-lg font-semibold">{question.question}</h3></div><Button type="button" variant="ghost" size="icon" aria-label="关闭当前解释" onClick={onClose}><X/></Button></header>
      <fieldset disabled={disabled} className="space-y-3"><legend className="mb-3 text-sm font-medium">选择你的解释</legend><RadioGroup value={candidate} onValueChange={choose} aria-label="解释候选">{candidates.map(row => <label key={row.id} className="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-3 has-data-checked:border-foreground"><RadioGroupItem className="mt-0.5" value={row.id}/><span className="min-w-0"><span className="block text-sm font-medium">{row.label}</span><span className="mt-1 block text-sm leading-relaxed text-muted-foreground">{row.explanation}</span></span></label>)}<label className="flex cursor-pointer items-center gap-3 py-2"><RadioGroupItem value="__custom"/><span className="text-sm">填写其他解释</span></label><label className="flex cursor-pointer items-center gap-3 py-2"><RadioGroupItem value="__none"/><span className="text-sm">所有候选都不符合</span></label></RadioGroup></fieldset>
      {candidate === '__none' ? <Alert><AlertTitle>该问题保持未解</AlertTitle><AlertDescription>不会保存候选为规则，也不会默认判为通过。你可以稍后处理，或选择“填写其他解释”给出修正。</AlertDescription></Alert> : <>
        <div className="space-y-2"><Label htmlFor="deep-explanation">拟保存的补充说明</Label><Textarea id="deep-explanation" aria-describedby="deep-draft-state" disabled={disabled || !candidate} value={content} onChange={event => setContent(event.target.value)} placeholder="选择候选或填写明确解释。" rows={4}/><p id="deep-draft-state" role="status" className="text-xs text-muted-foreground">{content ? '已编辑，尚未提交。' : '候选不会自动生效，请先选择或填写解释。'}</p></div>
        <fieldset disabled={disabled} className="space-y-3"><legend className="mb-2 text-sm font-medium">说明的作用范围</legend>{question.document_ids.map(id => <label key={id} className="flex min-w-0 items-start gap-3"><Checkbox checked={documents.includes(id)} onCheckedChange={checked => setDocuments(current => checked ? [...current, id] : current.filter(value => value !== id))}/><span className="min-w-0 text-sm"><FileName name={files.find(row => row.id === id)?.original_filename || '本次资料'}/></span></label>)}<p className="text-xs leading-relaxed text-muted-foreground">以上选中文件中的含义，仅用于以下审核规则，不会推广到其他资料：</p><ul aria-label="受影响的文件与要求" className="space-y-1 text-xs text-muted-foreground">{scope.rule_targets ? scope.rule_targets.map(target => <li key={`${target.document_id}:${target.rule_id}`} className="break-words">{files.find(file => file.id === target.document_id)?.original_filename || '本次待审文件'} · {target.rule_id}</li>) : affectedRules.map(item => <li key={`${item.documentId}:${item.rule.id}`} className="break-words">{item.filename} · {item.rule.id}</li>)}</ul></fieldset>
        <Disclosure className="text-sm" summary={<>这次需要改变审核规则？</>}><div className="space-y-2"><Label htmlFor="deep-change-kind">修改作用</Label><select id="deep-change-kind" value={kind} disabled={disabled} onChange={event => setKind(event.target.value as NonNullable<RuleChangeRequest['kind']>)} className="h-9 w-full rounded-md border bg-background px-3"><option value="interpretation">补充规则解释</option><option value="mapping">明确本次文件的对应含义</option><option value="standard_change">修改审核规则</option></select>{kind === 'standard_change' && <div aria-label="审核规则变更对照" className="space-y-3 border-l-2 pl-3"><div><h4 className="font-medium">原审核规则</h4>{affectedRules.map(item => <p key={`${item.documentId}:${item.rule.id}`} className="mt-1 whitespace-pre-wrap leading-relaxed">{item.rule.rule_to_check}</p>)}</div><div><h4 className="font-medium">新审核规则</h4><p className="mt-1 whitespace-pre-wrap leading-relaxed">{content || '请在上方填写新要求。'}</p></div><p className="text-xs text-muted-foreground">按新要求重新分析。人工改判请使用下方结果中的人工复核入口。</p></div>}</div></Disclosure>
      </>}
      <div className="space-y-3 border-t pt-4"><p className="text-xs leading-relaxed text-muted-foreground">{kind === 'standard_change' ? '将保存新的审核规则并开始一次明确的新分析。' : '确认后，补充说明成为新输入的规则依据，并开始一次新的分析。'} 解释被确认后，原件中的冲突仍会保留。</p><p className="text-xs leading-relaxed text-muted-foreground">说明适用范围见上方；将重新分析本任务的全部文件{analysisTargets.length > 0 && `（${analysisTargets.length} 份）：${analysisTargets.join('、')}`}。</p><div className="flex flex-wrap gap-2"><Button type="submit" disabled={!canSubmit}>{kind === 'standard_change' ? '修改规则并重新分析' : '确认解释并继续'}</Button><Button type="button" variant="outline" disabled={!canSubmit} onClick={() => submit('save_only')}>仅保存说明</Button><Button type="button" variant="ghost" onClick={onClose}>稍后处理</Button></div></div>
    </form>
  </div>
}

function FeedbackEvidence({source, at, file, open, visited, roundId, renderOriginal, onToggle}: {source: Omit<Question['sources'][number], 'locator'> & {locator: Record<string, unknown> | null}; at: number; file?: FileVersion; open: boolean; visited: boolean; roundId: string; renderOriginal?: OriginalRenderer; onToggle: () => void}) {
  const location = source.locator ? sourceLocation(source.locator) : ''
  // A stable focus object lets the original reader keep the user's position
  // when a candidate, draft or scope changes beside it.
  const focus = useMemo(() => location ? {location, requestId: 0} : undefined, [location])
  const locator = source.locator as AuditEvidence['locator']
  return <section className="min-w-0 space-y-3 border-t pt-3" aria-label={`解释证据 ${at + 1}`}>
    <FileName name={file?.original_filename || '本次资料'} colored/><p className="break-words text-xs text-muted-foreground">{location || '同轮资料，可查看完整上下文；未指定局部位置'}</p>
    <p className="whitespace-pre-wrap text-sm leading-relaxed">{source.excerpt || (file ? '请核对原件中的表达与当前解释。' : '此引用不在当前可读取资料中。')}</p>
    {file && <Button variant="outline" size="sm" aria-expanded={open} onClick={onToggle}>{open ? '收起原件' : '展开原件'}<span className="sr-only"> {file.original_filename}</span></Button>}
    <div hidden={!open} className="min-w-0 [&>div]:h-[26rem] [&>div]:min-h-0">{visited && file && (/\.(xlsx|xlsm|xls|docx)$/i.test(file.original_filename) ? <div className="flex min-w-0"><RemoteSourcePane nativeOffice file={{file_id: file.file_id, file_version_id: file.id, original_filename: file.original_filename, role: 'target', rule_version_id: null}} roundId={roundId} focus={focus}/></div> : renderOriginal?.(file, {file_version_id: file.id, locator, excerpt: '', location_gap: null, rendition: 'original', derived_file_version_id: null}, roundId))}</div>
  </section>
}

function DeepResults({view, files}: {view: DeepFeedbackView; files: FileVersion[]}) {
  const labels: Record<string, string> = resultLabel
  return <div className="space-y-5">{view.results.filter(result => result.current).map(result => {
    const index = view.results.indexOf(result), previous = view.results[index - 1]
    const adopted = result.inputs.flatMap(input => (input.rule as {body?: {appendix?: Array<{id: string; content: string}>}} | undefined)?.body?.appendix || [])
    const explanations = Array.from(new Map(adopted.map(entry => [entry.id, entry])).values())
    return <section key={result.round_id} className="space-y-3" aria-label="当前分析判断">
      <p role="status" className="text-sm">{view.input_changed ? '此前结果：新的说明尚未用于分析。' : result.execution === 'queued' ? '新输入已接收，等待分析。' : result.execution === 'running' ? '正在按新输入核对。' : result.execution === 'completed' ? '新输入分析已结束。' : '本次分析未完成，请核对执行状态。'}</p>
      {explanations.length > 0 && <div className="space-y-2 text-sm"><h4 className="font-medium">本次分析采用的说明</h4>{explanations.map(entry => <p key={entry.id} className="whitespace-pre-wrap leading-relaxed">{entry.content}</p>)}</div>}
      {result.findings.map((finding, at) => {
        const before = previous?.findings.find(row => row.item_key === finding.item_key)
        const locator = finding.locator as components['schemas']['FindingLocator'] | null
        const filename = files.find(file => file.id === locator?.file_version_id)?.original_filename || '原件未定位'
        const identity = `${filename} · ${locator?.rule_item_id || '条款未定位'}`
        return <article key={String(finding.item_key || at)} aria-label={identity} className="space-y-2 border-t py-3"><p className="break-words text-xs text-muted-foreground">{identity}</p><div className="flex flex-wrap items-center gap-2"><Badge variant={Object.hasOwn(resultLabel, String(finding.status)) ? resultTone(finding.status as ResultStatus) : 'outline'}>{labels[String(finding.status)] || '结果待确认'}</Badge><h4 className="text-sm font-medium">{String(finding.item || finding.item_key || '审核规则')}</h4></div>
          {before && <p className="text-sm" aria-label="逐项结果变化">{before.status === finding.status ? `结论未变：${labels[String(finding.status)] || '结果待确认'}` : `此前：${labels[String(before.status)] || '结果待确认'} → 当前：${labels[String(finding.status)] || '结果待确认'}`}</p>}
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{String(finding.classification || '')}</p>{Boolean(finding.suggestions) && <p className="whitespace-pre-wrap text-sm text-muted-foreground">{String(finding.suggestions)}</p>}</article>
      })}
    </section>
  })}</div>
}
