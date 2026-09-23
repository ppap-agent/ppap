import {useEffect, useState} from 'react'
import {dateLabel, executionLabels, resultLabels, analysisModeLabels, request, type Round} from './api'
import {productHref} from './navigation/route'
import {ExecutionBadge} from './audit/StatusBadge'
import {Badge, Button, Checkbox, DataTable, FeedbackState, Field, FilterBar, Inline, Input, Link, Page, PageHeader, Select, Text, type BadgeTone, type DataColumn} from './ui'

function countTone(status: string): BadgeTone {
  if (status === 'PASS') return 'success'
  if (status === 'NG') return 'warning'
  return 'danger'
}

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
  const columns: Array<DataColumn<Round>> = [
    {
      key: 'files',
      header: '审核资料',
      role: 'title',
      cell: row => <Link href={productHref({page: 'audit-round', roundId: row.id})}>{row.inputs.filter(item => item.role === 'target').map(item => item.original_filename).join('、')}</Link>,
    },
    {
      key: 'when',
      header: '提交时间',
      role: 'when',
      testId: 'history-version',
      cell: row => (
        <Inline gap="xs" wrap>
          <Text as="span" size="sm" tone="muted">{dateLabel(row.created_at)}</Text>
          {row.predecessor_id ? <Text as="small" size="xs" tone="muted">重审</Text> : null}
          {row.analysis_mode === 'deep' ? <Text as="small" size="xs" tone="muted">{analysisModeLabels.deep}</Text> : null}
        </Inline>
      ),
    },
    {
      key: 'result',
      header: '检查结果',
      role: 'result',
      testId: 'history-result',
      cell: row => row.execution !== 'completed'
        ? <ExecutionBadge execution={row.execution} />
        : (
          <Inline gap="sm" wrap>
            {['NG', 'ERR', 'PASS'].map(status => row.result_counts?.[status]
              ? <Badge key={status} tone={countTone(status)}>{resultLabels[status]} {row.result_counts[status]}</Badge>
              : null)}
            {!total(row) ? <Text as="span" tone="muted">暂无检查项</Text> : null}
          </Inline>
        ),
    },
    {
      key: 'review',
      header: '人工复核',
      role: 'meta',
      cell: row => <Text as="span" size="sm" tone="muted">{total(row) ? (row.reviewed_count || 0) + ' / ' + total(row) : '—'}</Text>,
    },
    {
      key: 'open',
      header: '操作',
      role: 'action',
      cell: row => {
        const pending = total(row) > (row.reviewed_count || 0)
        return <Link href={productHref({page: 'audit-round', roundId: row.id, review: pending ? 'next' : undefined})}>{pending ? '继续复核' : '查看结果'} →</Link>
      },
    },
  ]

  return (
    <Page>
      <PageHeader
        title="审核结果"
        description="查看每轮机器检查和人工复核进度，并继续未完成的复核。"
        actions={rows.length > 0 ? <Link variant="primary" href={productHref({page: 'audit-new'})}>新建审核</Link> : undefined}
      />
      {showToolbar && (
        <FilterBar onSubmit={() => { void load() }}>
          <Input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索文件或规则" aria-label="搜索审核结果" disabled={busy} />
          <Select value={execution} aria-label="执行状态" disabled={busy} onChange={event => { const value = event.target.value; setExecution(value); void load(true, query, value, unreviewed) }}>
            <option value="">全部状态</option>
            {Object.entries(executionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </Select>
          <Checkbox label="待复核" checked={unreviewed} disabled={busy} onChange={event => { const value = event.target.checked; setUnreviewed(value); void load(true, query, execution, value) }} />
          <Button type="submit" disabled={busy}>搜索</Button>
        </FilterBar>
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
            : <Link variant="primary" href={productHref({page: 'audit-new'})}>新建审核</Link>}
        </FeedbackState>
      )}
      {!!rows.length && <DataTable rows={rows} columns={columns} layout="records" label="审核结果" testId="history-list" />}
      {next !== null && <Button type="button" disabled={busy} onClick={() => void load(false)}>加载更多</Button>}
    </Page>
  )
}
