import {useEffect, useState} from 'react'
import {dateLabel, request, type QuotaEstimate} from './api'

interface Usage {id: string; created_at: string; consumed_points: number; is_free_trial: boolean; round_id?: string; user_id?: string}

export default function UsageView() {
  const [rows, setRows] = useState<Usage[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [next, setNext] = useState<number | null>(null)
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [quota, setQuota] = useState<QuotaEstimate | null>(null)
  const [quotaError, setQuotaError] = useState('')

  async function load(reset = true) {
    if (busy) return
    setError('')
    if (since && until && since > until) { setError('起始日期不能晚于截止日期。'); return }
    setBusy(true)
    try {
      const query = new URLSearchParams({offset: String(reset ? 0 : next || 0)})
      if (since) query.set('since', since)
      if (until) query.set('until', until)
      const data = await request<{events: Usage[]; next_offset: number | null}>('/usage?' + query)
      setRows(current => reset ? data.events : current.concat(data.events))
      setNext(data.next_offset)
    } catch (e) {
      setError(e instanceof Error ? e.message : '无法读取用量')
    } finally { setBusy(false) }
  }

  async function loadQuota() {
    setQuotaError('')
    try {
      setQuota(await request<QuotaEstimate>('/integrations/feishu/usage/quota'))
    } catch {
      setQuota(null)
      setQuotaError('无法读取 Partner AI')
    }
  }

  useEffect(() => { void load(); void loadQuota() }, [])

  return (
    <section className="page-content usage-page">
      {quotaError
        ? <p className="notice" role="status" data-testid="quota-summary">{quotaError}</p>
        : <p className="scope-note" data-testid="quota-summary">{quota ? `试用剩余 ${quota.remaining_trial_ai_points ?? '—'} 点 · ${quota.detailed_message || quota.message || (quota.quota_sufficient ? 'Partner AI 可用' : 'Partner AI 不可用')}` : '正在读取额度…'}</p>}
      {(rows.length > 0 || since || until) && (
      <form className="toolbar usage-toolbar" onSubmit={event => { event.preventDefault(); void load() }}>
        <label>起始日期<input value={since} onChange={event => setSince(event.target.value)} type="date" disabled={busy} /></label>
        <label>截止日期<input value={until} onChange={event => setUntil(event.target.value)} type="date" disabled={busy} /></label>
        <button type="submit" disabled={busy}>查询</button>
      </form>
      )}
      {error && <p className="error" role="alert" data-testid="usage-error">{error}</p>}
      {busy && !rows.length && <p className="loading-state" role="status">正在加载…</p>}
      {!busy && !rows.length && !error && <div className="empty-state" data-testid="usage-empty"><h2>当前范围没有用量记录</h2></div>}
      {!!rows.length && (
        <div className="table-scroll" data-testid="usage-list">
          <table className="responsive-table usage-table">
            <thead><tr><th>时间</th><th>用量（点）</th><th>类型</th><th>发起人</th><th><span className="sr-only">关联审核</span></th></tr></thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id}>
                  <td data-label="时间">{dateLabel(row.created_at)}</td>
                  <td data-label="用量（点）">{row.consumed_points.toLocaleString('zh-CN')}<span className="mobile-unit">点</span></td>
                  <td data-label="类型">{row.is_free_trial ? '试用' : '额度'}</td>
                  <td data-label="发起人">{row.user_id || '—'}</td>
                  <td className="row-action">{row.round_id && <a href={'/?round=' + row.round_id}>查看审核 →</a>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next !== null && <button type="button" className="load-more" disabled={busy} onClick={() => void load(false)}>加载更多</button>}
    </section>
  )
}
