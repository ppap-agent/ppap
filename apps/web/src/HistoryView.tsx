import {useEffect, useState} from 'react'
import {dateLabel, executionLabels, resultLabels, analysisModeLabels, request, type Round} from './api'
import {Button, ButtonLink, FeedbackState, PageHeader} from './ui/primitives'
import {ExecutionBadge} from './audit/StatusBadge'

export default function HistoryView() {
  const [rows, setRows] = useState<Round[]>([])
  const [query, setQuery] = useState('')
  const [execution, setExecution] = useState('')
  const [unreviewed, setUnreviewed] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [next, setNext] = useState<number | null>(null)

  async function load(reset = true, nextQuery = query, nextExecution = execution, nextUnreviewed = unreviewed) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const result = await request<{rounds: Round[]; next_offset: number | null}>('/rounds?' + new URLSearchParams({
        q: nextQuery, execution: nextExecution, unreviewed: String(nextUnreviewed), offset: String(reset ? 0 : next || 0),
      }))
      setRows(current => reset ? result.rounds : current.concat(result.rounds))
      setNext(result.next_offset)
    } catch (e) {
      setError(e instanceof Error ? e.message : '无法加载审核结果')
    } finally { setBusy(false) }
  }

  useEffect(() => { void load() }, [])

  function total(row: Round) {
    return Object.values(row.result_counts || {}).reduce((sum, n) => sum + n, 0)
  }

  const filtered = Boolean(query || execution || unreviewed)
  const showToolbar = rows.length > 0 || filtered

  return (
    <section className="page-content">
      <PageHeader
        title="审核结果"
        description="查看每轮机器检查和人工复核进度，并继续未完成的复核。"
        actions={rows.length > 0 ? <ButtonLink variant="primary" href="/">新建审核</ButtonLink> : undefined}
      />
      {showToolbar && (
        <form className="toolbar history-toolbar" onSubmit={event => { event.preventDefault(); void load() }}>
          <input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索文件或规则" aria-label="搜索审核结果" disabled={busy} />
          <select value={execution} aria-label="执行状态" disabled={busy} onChange={event => { const value = event.target.value; setExecution(value); void load(true, query, value, unreviewed) }}>
            <option value="">全部状态</option>
            {Object.entries(executionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <label className="inline-check"><input type="checkbox" checked={unreviewed} disabled={busy} onChange={event => { const value = event.target.checked; setUnreviewed(value); void load(true, query, execution, value) }} />待复核</label>
          <Button type="submit" disabled={busy}>搜索</Button>
        </form>
      )}
      {error && <FeedbackState tone="error" inline title="无法加载审核结果" detail={error} testId="history-error" />}
      {busy && !rows.length && <FeedbackState tone="loading" detail="正在加载审核结果…" />}
      {!busy && !rows.length && !error && (
        <FeedbackState
          title={filtered ? '没有符合条件的审核' : '还没有审核结果'}
          detail={filtered ? '调整筛选条件，或清除筛选查看全部记录。' : '创建一次审核后，结果会保留在这里。'}
          testId="history-empty"
        >
          {filtered
            ? <Button type="button" onClick={() => { setQuery(''); setExecution(''); setUnreviewed(false); void load(true, '', '', false) }}>清除筛选</Button>
            : <ButtonLink variant="primary" href="/">新建审核</ButtonLink>}
        </FeedbackState>
      )}
      {!!rows.length && (
        <div className="table-scroll" data-testid="history-list">
          <table className="responsive-table history-table">
            <thead><tr><th>审核资料</th><th>提交时间</th><th>检查结果</th><th>人工复核</th><th><span className="sr-only">操作</span></th></tr></thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id}>
                  <td className="record-title">
                    <a href={'/?round=' + row.id}>{row.inputs.filter(item => item.role === 'target').map(item => item.original_filename).join('、')}</a>
                  </td>
                  <td data-label="提交时间" data-testid="history-version"><time dateTime={row.created_at}>{dateLabel(row.created_at)}</time>{row.predecessor_id && <small className="repeat-label">重审</small>}{row.analysis_mode === 'deep' && <small className="repeat-label">{analysisModeLabels.deep}</small>}</td>
                  <td data-label="检查结果" data-testid="history-result">
                    {row.execution !== 'completed'
                      ? <ExecutionBadge execution={row.execution} />
                      : <div className="result-counts">{['NG', 'ERR', 'PASS'].map(status => row.result_counts?.[status] ? <span key={status} className={'status-' + status}>{resultLabels[status]} {row.result_counts?.[status]}</span> : null)}{!total(row) && <span>暂无检查项</span>}</div>}
                  </td>
                  <td data-label="已复核">{total(row) ? (row.reviewed_count || 0) + ' / ' + total(row) : '—'}</td>
                  <td className="row-action"><a href={'/?round=' + row.id + (total(row) > (row.reviewed_count || 0) ? '&review=next' : '')}>{total(row) > (row.reviewed_count || 0) ? '继续复核' : '查看结果'} →</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next !== null && <Button type="button" disabled={busy} onClick={() => void load(false)}>加载更多</Button>}
    </section>
  )
}
