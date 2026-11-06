import {useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {message} from '../journey/remote'
import {resultLabel} from './status-labels'
import {sameSnapshot} from './result-projection'
import {reviewHintText, reviewHintUrl} from './review-hints-contract'

type Hint = components['schemas']['ReviewHint']
export function ReviewHintDetails({roundId, hint, onHandled, onFeedback}: {roundId: string; hint: Hint; onHandled: () => void; onFeedback: () => void}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function respond(action: 'accepted' | 'ignored') {
    if (busy) return
    setBusy(true); setError('')
    try {
      const receipt = await request<components['schemas']['ReviewHintReceipt']>(`/rounds/${encodeURIComponent(roundId)}/review-hints`, 'PUT', {item_key: hint.item_key, action, hint})
      if (receipt.round_id !== roundId || receipt.item_key !== hint.item_key || receipt.action !== action || !sameSnapshot(receipt.hint, hint)) throw new Error('提示记录已变化，请重新读取结果。')
      onHandled()
      if (action === 'accepted') onFeedback()
    } catch (cause) {setError(message(cause))} finally {setBusy(false)}
  }
  return <section aria-label="建议复核对比" className="space-y-3 rounded-md border p-3 text-sm">
    <p className="font-medium">建议复核</p><p>{reviewHintText(hint)}</p>
    <div className="grid grid-cols-2 gap-3"><div><p className="text-muted-foreground">本次结果</p><p>{resultLabel[hint.current_status]}</p></div><div><p className="text-muted-foreground">对比结果 · {hint.compared_filename}</p><p>{resultLabel[hint.compared_status]}</p><a className="underline" href={reviewHintUrl(hint)} target="_blank" rel="noreferrer">打开对比审核与依据</a></div></div>
    <p className="text-xs text-muted-foreground">提示不代表判错，不改判、不计入报告或覆盖统计，也不调用模型。采纳只进入反馈，启动优化另行确认。</p>
    <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={() => void respond('accepted')}>已核对，这条判错了</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => void respond('ignored')}>忽略此提示</Button></div>
    {error && <p role="alert">{error}</p>}
  </section>
}
