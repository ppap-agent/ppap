import {createContext, useEffect, useId, useRef, useState, type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import type {components} from '../generated/openapi'
import {reviewMatches, reviewOf, type ReviewRequest, type ReviewRound} from '../review-contract'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Textarea} from '../components/ui/textarea'
import {judgmentLabels} from '../journey/judgment-state'
import {sameSnapshot} from './result-projection'
import {showToast} from '../components/toast'

export type AuditReviewAdapter = {read: (roundId: string) => Promise<ReviewRound>; save: (roundId: string, request: ReviewRequest) => Promise<ReviewRound>}
type Target = {roundId: string; itemKey: string; title: string; files: {id: string; name: string}[]; attempt: number | null}
type Values = Pick<ReviewRequest, 'judgment' | 'rationale' | 'next_step'> & {citations: components['schemas']['ReviewCitation'][]}
type Draft = {target: Target; values: Values; phase: 'loading' | 'editing' | 'sending' | 'reading' | 'unknown' | 'conflict' | 'saved' | 'error'; message: string; observation: number; base?: ReviewRound; observed?: ReviewRound; request?: ReviewRequest; retryAllowed?: boolean}
type Presentation = {container?: HTMLElement; onContinue?: () => void}
export const ReviewEditorContext = createContext<{open: (target: Target, presentation?: Presentation) => void; release: (roundId: string, itemKey: string) => void; revision: number} | null>(null)
const keyOf = (target: Target) => JSON.stringify([target.roundId, target.itemKey])
const judgments = ['confirm', 'disagree', 'unknown', 'manual-satisfied', 'manual-unsatisfied'] as const
const unresolved = (draft: Draft) => ['sending', 'reading', 'unknown'].includes(draft.phase)
function valuesOf(round: ReviewRound, key: string): Values {
  const review = reviewOf(round, key)
  return {judgment: review && judgments.some(value => value === review.judgment) ? review.judgment : 'pending', rationale: review?.rationale || '', next_step: review?.next_step || '', citations: structuredClone(review?.citations || [])}
}
function eligible(round: ReviewRound, target: Target) {
  const versions = round.inputs.map(row => row.file_version_id)
  return round.id === target.roundId && ['completed', 'failed'].includes(round.execution) && round.result_attempt === target.attempt
    && new Set(versions).size === target.files.length && versions.length === target.files.length && target.files.every(file => versions.includes(file.id))
    && (round.findings.some(row => row.item_key === target.itemKey) || round.coverage.rule_items?.some(row => `coverage:${row.file_version_id}:${row.id}` === target.itemKey && row.coverage !== 'executed'))
}
function machineSnapshot(round: ReviewRound) {
  const {rule_items, ...coverage} = round.coverage
  return {id: round.id, inputs: round.inputs, execution: round.execution, result_attempt: round.result_attempt, findings: round.findings.map(({review: _review, ...row}) => row), coverage: {...coverage, rule_items: rule_items?.map(({review: _review, ...row}) => row)}}
}

/** Pending requests survive dialog and file switches, but never create local audit facts. */
export function AuditReviewProvider({adapter, children}: {adapter: AuditReviewAdapter; children: ReactNode}) {
  const drafts = useRef(new Map<string, Draft>())
  const presentations = useRef(new Map<string, Presentation>())
  const continueAfterSave = useRef(new Set<string>())
  const trigger = useRef<HTMLElement | null>(null), mounted = useRef(true)
  const [selected, setSelected] = useState<string | null>(null), [revision, setRevision] = useState(0), [, redraw] = useState(0)
  const id = useId(), refresh = () => redraw(value => value + 1)
  useEffect(() => {mounted.current = true; return () => {mounted.current = false; for (const draft of drafts.current.values()) draft.observation += 1}}, [])
  function close() {
    const current = selected ? drafts.current.get(selected) : undefined
    if (current) {
      current.observation += 1
      if (unresolved(current)) {current.phase = 'unknown'; current.message = '保存结果尚未确认，请先核对。'}
      else drafts.current.delete(selected!)
    }
    setSelected(null)
  }
  async function observe(draft: Draft, read: boolean) {
    const observation = ++draft.observation, request = draft.request && structuredClone(draft.request)
    draft.phase = request ? read ? 'reading' : 'sending' : 'loading'; draft.message = ''; draft.retryAllowed = false; refresh()
    try {
      const round = await (read || !request ? adapter.read(draft.target.roundId) : adapter.save(draft.target.roundId, request))
      if (!mounted.current || observation !== draft.observation) return
      if (!eligible(round, draft.target) || draft.base && !sameSnapshot(machineSnapshot(round), machineSnapshot(draft.base))) throw new Error('复核记录与本轮结果不一致，请重新读取审核记录。')
      if (!request) {draft.base = round; draft.values = valuesOf(round, draft.target.itemKey); draft.phase = 'editing'}
      else if (reviewMatches(round, request)) {
        draft.phase = 'saved'; draft.message = '人工复核已保存。'; draft.request = undefined; draft.base = round; setRevision(value => value + 1)
        const key = keyOf(draft.target)
        if (continueAfterSave.current.delete(key)) {
          showToast('已保存，继续下一项')
          setSelected(value => value === key ? null : value)
          presentations.current.get(key)?.onContinue?.()
        }
      } else if (read) {
        const observed = reviewOf(round, request.item_key)
        if ((observed?.version || 0) > request.version) {draft.observed = round; draft.phase = 'conflict'; draft.message = '此条复核已有其他修改。你的输入仍保留，请核对服务器记录。'}
        else {draft.phase = 'unknown'; draft.retryAllowed = sameSnapshot(observed, reviewOf(draft.base!, request.item_key)); draft.message = '尚未找到这次复核记录。可以再次核对，或重试保存相同内容。'}
      } else throw new Error('复核结果不一致，请先核对保存结果。')
    } catch (cause) {
      if (!mounted.current || observation !== draft.observation) return
      const status = (cause as {status?: number}).status
      draft.phase = request ? 'unknown' : 'error'
      draft.message = status === 403 ? '你目前没有此复核操作的权限。' : status === 401 ? '登录已失效，请恢复登录后重新核对。' : status === 409 ? '服务器记录已更新，请先核对保存结果。' : request ? '保存结果尚未确认，请先核对。' : cause instanceof Error ? cause.message : '暂时无法读取复核记录。'
    }
    refresh()
  }
  function open(target: Target, presentation: Presentation = {}) {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const key = keyOf(target)
    presentations.current.set(key, presentation)
    let draft = drafts.current.get(key)
    if (!draft) {
      draft = {target: structuredClone(target), values: {judgment: 'pending', rationale: '', next_step: '', citations: []}, phase: 'loading', message: '', observation: 0}
      drafts.current.set(key, draft); void observe(draft, true)
    }
    setSelected(key)
  }
  const draft = selected ? drafts.current.get(selected) : undefined
  const serverReview = draft?.observed ? reviewOf(draft.observed, draft.target.itemKey) : null
  const values = draft?.values, locked = draft?.phase !== 'editing'
  const valid = Boolean(draft && values && judgments.some(value => value === values.judgment) && values.rationale.trim() && values.rationale.trim().length <= 2000 && values.next_step.trim().length <= 2000 && values.citations.length <= 20 && (!values.judgment.startsWith('manual-') || values.citations.length > 0) && values.citations.every(cite => draft.target.files.some(file => file.id === cite.file_version_id) && cite.location.trim() && cite.location.trim().length <= 4000 && cite.excerpt.trim() && cite.excerpt.trim().length <= 4000))
  function save(andContinue = false) {
    if (!draft?.base || !values || locked || !valid) return
    if (andContinue) continueAfterSave.current.add(keyOf(draft.target))
    draft.request = {...values, rationale: values.rationale.trim(), next_step: values.next_step.trim(), citations: values.citations.map(cite => ({...cite, location: cite.location.trim(), excerpt: cite.excerpt.trim()})), item_key: draft.target.itemKey, version: reviewOf(draft.base, draft.target.itemKey)?.version || 0}
    void observe(draft, false)
  }
  function change(patch: Partial<Values>) {if (values && !locked) {Object.assign(values, patch); refresh()}}
  function release(roundId: string, itemKey: string) {
    const key = keyOf({roundId, itemKey} as Target)
    presentations.current.delete(key); continueAfterSave.current.delete(key)
    setSelected(value => value === key ? null : value)
  }
  const form = draft && values && <form className="space-y-4" onSubmit={event => {event.preventDefault(); save()}}>
    <p className="break-words text-sm font-medium">{draft.target.title}</p>
    {draft.phase === 'loading' ? <ReadSkeleton label="正在读取复核记录…" kind="detail"/> : <>
    <div className="grid gap-2"><Label htmlFor={`${id}-judgment`}>人工判断</Label><Select value={values.judgment} disabled={locked} onValueChange={judgment => change({judgment})}><SelectTrigger id={`${id}-judgment`} aria-label="人工判断"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="pending" disabled>请选择判断</SelectItem>{judgments.map(value => <SelectItem key={value} value={value}>{judgmentLabels[value]}</SelectItem>)}</SelectContent></Select></div>
    <div className="grid gap-2"><Label htmlFor={`${id}-reason`}>判断理由</Label><Textarea id={`${id}-reason`} value={values.rationale} maxLength={2000} disabled={locked} onChange={event => change({rationale: event.target.value})}/></div>
    <div className="grid gap-2"><Label htmlFor={`${id}-next`}>后续建议</Label><Input id={`${id}-next`} value={values.next_step} maxLength={2000} disabled={locked} onChange={event => change({next_step: event.target.value})}/></div>
    <section aria-label="复核引用" className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium">原件引用{values.judgment.startsWith('manual-') ? '（必填）' : '（选填）'}</h3><Button type="button" size="sm" variant="outline" disabled={locked || values.citations.length >= 20} onClick={() => change({citations: [...values.citations, {file_version_id: draft.target.files[0]?.id || '', location: '', excerpt: ''}]})}>添加引用</Button></div>{values.citations.map((cite, index) => <fieldset className="grid min-w-0 gap-3 border-t pt-3" key={index}><div className="flex min-w-0 items-center gap-2"><Select value={cite.file_version_id} disabled={locked} onValueChange={file_version_id => change({citations: values.citations.map((row, at) => at === index ? {...row, file_version_id} : row)})}><SelectTrigger className="min-w-0 flex-1" aria-label={`引用文件 ${index + 1}`}><SelectValue/></SelectTrigger><SelectContent>{draft.target.files.map(file => <SelectItem key={file.id} value={file.id}>{file.name}</SelectItem>)}</SelectContent></Select><Button type="button" variant="ghost" size="sm" disabled={locked} onClick={() => change({citations: values.citations.filter((_, at) => at !== index)})}>移除引用 {index + 1}</Button></div><Label htmlFor={`${id}-location-${index}`}>引用位置 {index + 1}</Label><Input id={`${id}-location-${index}`} value={cite.location} maxLength={4000} disabled={locked} onChange={event => change({citations: values.citations.map((row, at) => at === index ? {...row, location: event.target.value} : row)})}/><Label htmlFor={`${id}-excerpt-${index}`}>引用摘录 {index + 1}</Label><Textarea id={`${id}-excerpt-${index}`} value={cite.excerpt} maxLength={4000} disabled={locked} onChange={event => change({citations: values.citations.map((row, at) => at === index ? {...row, excerpt: event.target.value} : row)})}/></fieldset>)}</section>
    </>}
    {draft.message && <Alert><AlertTitle>{draft.phase === 'saved' ? '已保存' : draft.phase === 'conflict' ? '复核版本有变化' : '请核对复核记录'}</AlertTitle><AlertDescription><p role="status">{draft.message}</p></AlertDescription></Alert>}
    {draft.phase === 'conflict' && serverReview && <section aria-label="服务器复核记录" className="space-y-2 text-sm"><p className="font-medium">服务器复核 v{serverReview.version} · {Object.hasOwn(judgmentLabels, serverReview.judgment) ? judgmentLabels[serverReview.judgment as keyof typeof judgmentLabels] : serverReview.judgment}</p><p className="whitespace-pre-wrap">{serverReview.rationale}</p><p className="whitespace-pre-wrap">{serverReview.next_step}</p>{serverReview.citations?.map((cite, index) => <div key={index} className="space-y-1"><p>{draft.target.files.find(file => file.id === cite.file_version_id)?.name || '本轮外的引用'}</p><p className="whitespace-pre-wrap">{cite.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{cite.excerpt}</p></div>)}<Button type="button" variant="outline" onClick={() => {draft.base = draft.observed; draft.request = undefined; draft.phase = 'editing'; draft.message = '当前输入尚未保存。'; refresh()}}>以服务器版本继续编辑</Button></section>}
    <DialogFooter><Button type="button" variant="outline" onClick={close}>{draft.phase === 'saved' ? '完成' : unresolved(draft) ? '关闭，稍后核对' : '取消'}</Button>{draft.phase === 'editing' ? <><Button type="submit" disabled={!valid}>保存人工复核</Button>{presentations.current.get(selected!)?.onContinue && <Button type="button" disabled={!valid} onClick={() => save(true)}>保存并继续</Button>}</> : draft.phase === 'unknown' || draft.phase === 'error' ? <><Button type="button" onClick={() => void observe(draft, true)}>{draft.request ? '核对保存结果' : '重新读取复核记录'}</Button>{draft.retryAllowed && <Button type="button" variant="outline" onClick={() => void observe(draft, false)}>重试保存相同内容</Button>}</> : ['sending', 'reading'].includes(draft.phase) ? <Button disabled>{draft.phase === 'sending' ? '正在保存…' : '正在读取…'}</Button> : null}</DialogFooter>
  </form>
  const container = selected ? presentations.current.get(selected)?.container : undefined
  return <ReviewEditorContext.Provider value={{open, release, revision}}>{children}{container?.isConnected ? createPortal(<section aria-label="人工复核编辑" className="border-t pt-4">{form}</section>, container) : <Dialog open={Boolean(draft)} onOpenChange={value => {if (!value) close()}}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl" onCloseAutoFocus={event => {event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus()}}><DialogHeader><DialogTitle>人工复核</DialogTitle><DialogDescription>记录人工判断，不改变机器结果或正式报告资格。</DialogDescription></DialogHeader>{form}</DialogContent></Dialog>}</ReviewEditorContext.Provider>
}
import {ReadSkeleton} from '../components/read-skeleton'
