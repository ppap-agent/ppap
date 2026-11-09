import {displayTime} from '../helpers'
import {useState} from 'react'
import {Button} from '../components/ui/button'
import type {AffectedRoundsView, CheckerFeedbackAdapter} from './checker-feedback-contract'

/** A read only, bounded history scan. Opening a round never requests a re-audit. */
export function AffectedFeedbackRounds({revision, clause, round, adapter}: {revision: string; clause: string; round: string; adapter: CheckerFeedbackAdapter}) {
  const [page, setPage] = useState<AffectedRoundsView | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function read(after?: string) {
    if (busy || !adapter.affected) return
    setBusy(true); setError('')
    try {setPage(await adapter.affected(revision, clause, round, after))}
    catch {setError('历史结果暂不可读，请稍后重新查看。')}
    finally {setBusy(false)}
  }
  return <section aria-label="可能受影响的历史结果" className="space-y-2 border-t pt-3">
    <Button variant="outline" disabled={busy} onClick={() => void read()}>查看同一条款的历史结果</Button>
    <p className="text-xs text-muted-foreground">仅查看本企业有权访问的记录。旧结果不变，打开后仍需明确选择重审。</p>
    {error && <p role="alert">{error}</p>}
    {page && <><p className="text-sm">本页找到 {page.items.length} 轮可能受影响；已检查 {page.scanned_rounds} 轮历史，不代表全部历史。</p>
      {page.items.map(item => <a key={item.round_id} className="block text-sm underline" href={`/audit?round=${encodeURIComponent(item.round_id)}&file=${encodeURIComponent(item.file_version_id)}&clause=${encodeURIComponent(item.rule_clause_id)}&basis=1`}>{displayTime(item.created_at)} · 查看结果并选择重审</a>)}
      {page.next_cursor && <Button variant="ghost" disabled={busy} onClick={() => void read(page.next_cursor!)}>继续查看历史</Button>}
    </>}
  </section>
}
