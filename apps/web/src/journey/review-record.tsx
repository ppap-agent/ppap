import {createContext, useContext, useEffect, useRef, useState} from 'react'
import {request, type Round} from '../api'
import {Button} from '../components/ui/button'
import type {DecisionEvidence, DecisionInput} from './decision'
import {decisionInputKey, judgmentKey, manualReady, isManual, type Judgment} from './judgment-state'
import {sourceKey} from './source-documents'
import {message, useRemote} from './remote'
import {reviewOf, reviewMatches} from '../review-contract'
export function serverJudgments(round: Round, input: DecisionInput): Record<string, Judgment> {
  if (round.id !== input.roundId) return {}
  return Object.fromEntries(input.evidence.flatMap(row => {
    const review = reviewOf(round, row.id)
    if (!review || !['confirm', 'disagree', 'unknown', 'manual-satisfied', 'manual-unsatisfied'].includes(review.judgment)) return []
    const judgment: Judgment = {value: review.judgment as Judgment['value'], reason: review.rationale, citations: review.citations?.map(cite => ({source: sourceKey(input.files.find(file => file.material?.file_version_id === cite.file_version_id) ?? {name: '来源待确认', revision: '', sourceVersion: 0}), location: cite.location, excerpt: cite.excerpt}))}
    return [[row.id, {...judgment, confirmed: judgmentKey(input, row, judgment)}]]
  }))
}
export const RoundReviewContext = createContext<{round: Round; onChange: (round: Round) => void} | null>(null)
export function ReviewRecord({input, row, judgment, onChange, frozen, afterSave}: {input: DecisionInput; row: DecisionEvidence; judgment: Judgment; onChange: (next: Judgment) => void; frozen: boolean; afterSave?: () => void}) {
  const session = useContext(RoundReviewContext)
  const remote = useRemote<Round>(!session && input.roundId ? `/rounds/${input.roundId}` : null)
  const current = session?.round ?? remote.value
  const [operation, setOperation] = useState<'save' | 'readback' | null>(null), [notice, setNotice] = useState('')
  const busy = operation !== null
  const checkpoint = judgment.pendingSave
  const pending = checkpoint && checkpoint.roundId === input.roundId && checkpoint.request.item_key === row.id ? checkpoint.request : null
  const working = useRef(false), initialized = useRef(false), latest = useRef(judgment); latest.current = judgment
  const mounted = useRef(true), currentFrozen = useRef(frozen); currentFrozen.current = frozen
  const owner = decisionInputKey(input), currentOwner = useRef(owner); currentOwner.current = owner
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  const review = current ? reviewOf(current, row.id) : null
  useEffect(() => {
    if (initialized.current || !current || latest.current.pendingSave) return
    initialized.current = true
    if (!review || !['confirm', 'disagree', 'unknown', 'manual-satisfied', 'manual-unsatisfied'].includes(review.judgment)) return
    const restored: Judgment = {value: review.judgment as Judgment['value'], reason: review.rationale, citations: review.citations?.map(cite => ({source: sourceKey(input.files.find(file => file.material?.file_version_id === cite.file_version_id) ?? {name: '来源待确认', revision: '', sourceVersion: 0}), location: cite.location, excerpt: cite.excerpt}))}
    if (latest.current.value === 'pending' && !latest.current.reason.trim() && !latest.current.citations?.length || latest.current.confirmed) onChange({...restored, confirmed: judgmentKey(input, row, restored)})
  }, [current, review])
  if (!input.roundId) return null
  const valid = judgment.value !== 'pending' && Boolean(judgment.reason.trim()) && (!isManual(judgment) || manualReady(input, judgment))
  const restored = current ? serverJudgments(current, input)[row.id] : undefined
  const saved = Boolean(!pending && judgment.confirmed === judgmentKey(input, row, judgment) && restored && judgmentKey(input, row, restored) === judgmentKey(input, row, judgment))
  const citationWire = (value: Judgment) => (value.citations ?? []).map(cite => ({file_version_id: input.files.find(file => sourceKey(file) === cite.source)?.material?.file_version_id || '', location: cite.location.trim(), excerpt: cite.excerpt.trim()}))
  async function save(readback = false, continueAfter = false) {
    if (working.current || frozen || !current || current.id !== input.roundId || !pending && (!valid || saved) || row.id === 'no-result') return
    const continueOnSuccess = continueAfter ? afterSave : undefined
    const citations = citationWire(judgment)
    const payload = pending ?? {item_key: row.id, judgment: judgment.value, rationale: judgment.reason.trim(), next_step: '', version: review?.version || 0, citations}
    working.current = true; setOperation(readback ? 'readback' : 'save'); setNotice('')
    try {
      if (!readback) onChange({...judgment, pendingSave: {roundId: input.roundId!, request: payload}})
      const round = await request<Round>(readback ? `/rounds/${input.roundId}` : `/rounds/${input.roundId}/reviews`, readback ? 'GET' : 'PUT', readback ? undefined : payload)
      if (!mounted.current || currentOwner.current !== owner || round.id !== input.roundId) return
      if (session) session.onChange(round)
      else remote.setValue(round)
      const observed = reviewOf(round, row.id)
      const matches = reviewMatches(round, payload)
      if (matches) {
        const unchanged = latest.current.value === payload.judgment && latest.current.reason.trim() === payload.rationale && JSON.stringify(citationWire(latest.current)) === JSON.stringify(payload.citations || [])
        if (unchanged) onChange(serverJudgments(round, input)[row.id])
        else onChange({...latest.current, pendingSave: undefined})
        setNotice(unchanged ? '人工复核已保存。' : '此前复核已保存；当前修改尚未保存。')
        if (unchanged && !currentFrozen.current) continueOnSuccess?.()
      }
      else if (readback) {onChange({...latest.current, pendingSave: undefined}); setNotice(observed ? '服务器记录与本次输入不同，请核对后再保存。' : '本次复核尚未保存，输入已保留，可以重试。')}
      else throw new Error('复核结果不一致，请核对服务器记录。')
    } catch (cause) {
      if (!mounted.current || currentOwner.current !== owner) return
      const status = (cause as {status?: number}).status
      if (status && status < 500) onChange({...latest.current, pendingSave: undefined})
      setNotice((cause as {status?: number}).status && Number((cause as {status?: number}).status) >= 500 ? '保存结果待确认，输入已保留。' : message(cause))
    } finally {working.current = false; if (mounted.current) setOperation(null)}
  }
  const disabled = busy || !current || current.id !== input.roundId || !pending && (!valid || saved) || row.id === 'no-result'
  return <div className="sticky bottom-0 z-10 space-y-3 border-t bg-background py-3 text-sm"><p role="status" className="text-muted-foreground">{busy ? operation === 'save' ? '正在保存人工复核…' : '正在核对服务器记录…' : notice || remote.error || (pending ? '保存结果待确认，输入已保留。' : saved ? '已保存到本轮审核' : !valid ? judgment.value === 'pending' ? '选择人工判断后，填写理由。' : isManual(judgment) && !manualReady(input, judgment) ? '补全证据位置、摘录和判断理由。' : '填写判断理由后保存。' : '当前修改尚未保存')}</p>{!frozen && <div className="flex flex-wrap items-center gap-2"><Button disabled={disabled} onClick={() => void save(Boolean(pending))}>{busy ? operation === 'save' ? '正在保存…' : '正在核对…' : pending ? '核对保存结果' : '保存人工复核'}</Button>{afterSave && !saved && <Button variant="outline" disabled={disabled} onClick={() => void save(Boolean(pending), true)}>{pending ? '核对后继续' : '保存并继续'}</Button>}</div>}</div>
}
