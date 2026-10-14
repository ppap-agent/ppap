import {createContext, useContext, useEffect, useRef, useState} from 'react'
import {request, type ReviewRequest, type Round} from '../api'
import {Button} from '../components/ui/button'
import type {DecisionEvidence, DecisionInput} from './decision'
import {judgmentKey, manualReady, isManual, type Judgment} from './judgment-state'
import {sourceKey} from './source-documents'
import {message, useRemote} from './remote'

function reviewOf(round: Round, key: string) {
  return round.findings.find(row => row.item_key === key)?.review ?? round.coverage.rule_items?.find(row => `coverage:${row.file_version_id}:${row.id}` === key)?.review ?? null
}
export const RoundReviewContext = createContext<{round: Round; onChange: (round: Round) => void} | null>(null)
export function ReviewRecord({input, row, judgment, onChange, frozen}: {input: DecisionInput; row: DecisionEvidence; judgment: Judgment; onChange: (next: Judgment) => void; frozen: boolean}) {
  const session = useContext(RoundReviewContext)
  const remote = useRemote<Round>(!session && input.roundId ? `/rounds/${input.roundId}` : null)
  const current = session?.round ?? remote.value
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState(''), [pending, setPending] = useState<ReviewRequest | null>(null)
  const working = useRef(false), initialized = useRef(false), latest = useRef(judgment); latest.current = judgment
  const review = current ? reviewOf(current, row.id) : null
  useEffect(() => {
    if (initialized.current || !current) return
    initialized.current = true
    if (!review || !['confirm', 'disagree', 'unknown', 'manual-satisfied', 'manual-unsatisfied'].includes(review.judgment)) return
    const restored: Judgment = {value: review.judgment as Judgment['value'], reason: review.rationale, citations: review.citations?.map(cite => ({source: sourceKey(input.files.find(file => file.material?.file_version_id === cite.file_version_id) ?? {name: '来源待核对', revision: '', sourceVersion: 0}), location: cite.location, excerpt: cite.excerpt}))}
    if (latest.current.value === 'pending' || latest.current.confirmed) onChange({...restored, confirmed: judgmentKey(input, row, restored)})
  }, [current, review])
  if (!input.roundId) return null
  const valid = judgment.value !== 'pending' && Boolean(judgment.reason.trim()) && (!isManual(judgment) || manualReady(input, judgment))
  const saved = Boolean(judgment.confirmed === judgmentKey(input, row, judgment) && review)
  async function save(readback = false) {
    if (working.current || !current) return
    const citations = (judgment.citations ?? []).map(cite => ({file_version_id: input.files.find(file => sourceKey(file) === cite.source)?.material?.file_version_id || '', location: cite.location, excerpt: cite.excerpt}))
    const payload = pending ?? {item_key: row.id, judgment: judgment.value, rationale: judgment.reason.trim(), next_step: '', version: review?.version || 0, citations}
    working.current = true; setBusy(true); setNotice('')
    try {
      if (!readback) setPending(payload)
      const round = await request<Round>(readback ? `/rounds/${input.roundId}` : `/rounds/${input.roundId}/reviews`, readback ? 'GET' : 'PUT', readback ? undefined : payload)
      if (session) session.onChange(round)
      else remote.setValue(round)
      const observed = reviewOf(round, row.id)
      const matches = observed?.version === payload.version + 1 && observed.judgment === payload.judgment && observed.rationale === payload.rationale && (observed.citations || []).length === (payload.citations || []).length && (observed.citations || []).every((cite, index) => ['file_version_id', 'location', 'excerpt'].every(key => cite[key as keyof typeof cite] === payload.citations?.[index]?.[key as keyof typeof cite]))
      if (matches) {
        const unchanged = judgmentKey(input, row, latest.current) === judgmentKey(input, row, judgment)
        if (unchanged) onChange({...judgment, confirmed: judgmentKey(input, row, judgment)})
        setPending(null); setNotice(unchanged ? '人工复核已保存。' : '此前复核已保存；当前修改尚未保存。')
      }
      else if (readback) {setPending(null); setNotice('服务器记录与本次输入不同，请核对后再保存。')}
      else throw new Error('复核回执不一致，请核对服务器记录。')
    } catch (cause) {
      if ((cause as {status?: number}).status) setPending(null)
      setNotice(message(cause))
    } finally {working.current = false; setBusy(false)}
  }
  return <div className={"flex flex-wrap items-center justify-between gap-3 text-sm"}><span role="status" className={"text-muted-foreground"}>{notice || remote.error || (saved ? '已保存到本轮审核' : '尚未保存人工复核')}</span>{!frozen && <Button disabled={busy || !current || !pending && (!valid || saved) || row.id === 'no-result'} onClick={() => void save(Boolean(pending))}>{busy ? '正在保存…' : pending ? '核对保存结果' : '保存人工复核'}</Button>}</div>
}
