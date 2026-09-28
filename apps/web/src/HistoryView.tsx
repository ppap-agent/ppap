import {useEffect, useState} from 'react'
import {dateLabel, executionLabels, resultLabels, analysisModeLabels, request, type FindingStatus, type Round, type RoundList} from './api'
import {productHref} from './navigation/route'
import {ExecutionBadge} from './audit/StatusBadge'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Checkbox} from '@/components/ui/checkbox'
import {Input} from '@/components/ui/input'
import {NativeSelect} from '@/components/ui/native-select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table'
import {DataSurface, FilterBar, PageHeader, PageLayout} from './ui/PageLayout'

function countVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'ERR') return 'destructive'
  if (status === 'PASS') return 'default'
  if (status === 'NG' || status === 'UNABLE') return 'outline'
  return 'secondary'
}

const resultOrder: FindingStatus[] = ['NG', 'ERR', 'UNABLE', 'NA', 'PASS']
type HistoryFilters = {query: string; execution: string; unreviewed: boolean}

function targetName(row: Round) {
  return row.inputs.filter(item => item.role === 'target').map(item => item.original_filename).join('、') || '未命名审核'
}

function HistoryMobileRow({row}: {row: Round}) {
  const count = Object.values(row.result_counts || {}).reduce((sum, value) => sum + value, 0)
  const pending = count > (row.reviewed_count || 0)
  return <article data-testid="history-version" className="flex flex-col gap-3 border-b py-4 last:border-b-0">
    <div className="flex min-w-0 items-start justify-between gap-3">
      <div className="min-w-0">
        <a className="block truncate font-medium text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: row.id})}>{targetName(row)}</a>
        <p className="m-0 text-sm text-muted-foreground">{[dateLabel(row.created_at), row.analysis_mode === 'deep' ? analysisModeLabels.deep : null, row.predecessor_id ? '重审' : null].filter(Boolean).join(' · ')}</p>
      </div>
      {row.execution !== 'completed' && <ExecutionBadge execution={row.execution} />}
    </div>
    {row.execution === 'completed' && <div className="flex flex-wrap gap-2">
      {resultOrder.map(status => row.result_counts?.[status] ? <Badge key={status} variant={countVariant(status)}>{resultLabels[status]} {row.result_counts[status]}</Badge> : null)}
      {!count && <span className="text-sm text-muted-foreground">暂无检查项</span>}
    </div>}
    <div className="flex items-center justify-between gap-3 border-t pt-3 text-sm">
      <span className="text-muted-foreground">{count ? `复核 ${row.reviewed_count || 0} / ${count}` : '暂无复核项'}</span>
      <a className="shrink-0 text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: row.id, review: pending ? 'next' : undefined})}>{pending ? '继续复核 →' : '查看结果 →'}</a>
    </div>
  </article>
}

export default function HistoryView() {
  const [rows, setRows] = useState<Round[]>([])
  const [query, setQuery] = useState('')
  const [execution, setExecution] = useState('')
  const [unreviewed, setUnreviewed] = useState(false)
  const [applied, setApplied] = useState<HistoryFilters>({query: '', execution: '', unreviewed: false})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [next, setNext] = useState<number | null>(null)

  async function load(reset = true, filters = applied) {
    if (busy) return
    if (reset) {
      setRows([])
      setNext(null)
      setApplied(filters)
    }
    setBusy(true)
    setError('')
    try {
      const result = await request<RoundList>('/rounds?' + new URLSearchParams({
        q: filters.query, execution: filters.execution, unreviewed: String(filters.unreviewed), offset: String(reset ? 0 : next || 0),
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

  const filtered = Boolean(applied.query || applied.execution || applied.unreviewed)
  return (
    <PageLayout>
      <PageHeader title="审核结果" action={<Button asChild size="sm"><a href={productHref({page: 'audit-new'})}>新建审核</a></Button>} />
      <FilterBar as="form" role="search" label="筛选审核结果" className="grid items-center gap-2 lg:grid-cols-[minmax(0,1fr)_14rem_auto]" onSubmit={event => { event.preventDefault(); void load(true, {query, execution, unreviewed}) }}>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-2"><Input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索文件或规则" aria-label="搜索审核结果" disabled={busy} /><Button type="submit" variant="outline" disabled={busy}>搜索</Button></div>
        <NativeSelect value={execution} aria-label="执行状态" disabled={busy} onChange={event => setExecution(event.target.value)}>
          <option value="">全部执行状态</option>
          {Object.entries(executionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </NativeSelect>
        <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 text-sm text-muted-foreground"><Checkbox checked={unreviewed} disabled={busy} onCheckedChange={checked => setUnreviewed(checked === true)} /><span>仅待复核</span></label>
      </FilterBar>
      {error && <Alert variant="destructive" data-testid="history-error"><AlertTitle>无法加载审核结果</AlertTitle><AlertDescription><div className="flex flex-wrap items-center gap-2">{error}<Button type="button" variant="outline" disabled={busy} onClick={() => { void load(true, {query, execution, unreviewed}) }}>重试</Button></div></AlertDescription></Alert>}
      {busy && !rows.length && <Alert><AlertDescription>正在加载审核结果…</AlertDescription></Alert>}
      {!busy && !rows.length && !error && (
        <Alert data-testid="history-empty"><AlertTitle>{filtered ? '没有符合条件的审核' : '还没有审核结果'}</AlertTitle><AlertDescription>{filtered ? '调整筛选条件，或清除筛选查看全部记录。' : '创建一次审核后，结果会保留在这里。'}{filtered && <Button type="button" className="mt-2" onClick={() => { setQuery(''); setExecution(''); setUnreviewed(false); void load(true, {query: '', execution: '', unreviewed: false}) }}>清除筛选</Button>}</AlertDescription></Alert>
      )}
      {!!rows.length && <div data-testid="history-list">
        <div data-testid="history-result" className="min-w-0">
        <div className="hidden lg:block"><DataSurface><Table><TableHeader><TableRow><TableHead>审核资料</TableHead><TableHead>检查结果</TableHead><TableHead>人工复核</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => {
          const pending = total(row) > (row.reviewed_count || 0)
          return <TableRow key={row.id}><TableCell><div className="flex flex-col gap-1"><a className="text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: row.id})}>{targetName(row)}</a><span className="text-sm text-muted-foreground">{[dateLabel(row.created_at), row.analysis_mode === 'deep' ? analysisModeLabels.deep : null, row.predecessor_id ? '重审' : null].filter(Boolean).join(' · ')}</span></div></TableCell><TableCell>{row.execution !== 'completed' ? <ExecutionBadge execution={row.execution} /> : <div className="flex flex-wrap gap-2">{resultOrder.map(status => row.result_counts?.[status] ? <Badge key={status} variant={countVariant(status)}>{resultLabels[status]} {row.result_counts[status]}</Badge> : null)}{!total(row) && <span className="text-muted-foreground">暂无检查项</span>}</div>}</TableCell><TableCell><span className="text-sm text-muted-foreground">{total(row) ? <>复核 {row.reviewed_count || 0} / {total(row)}</> : '—'}</span></TableCell><TableCell><a className="text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: row.id, review: pending ? 'next' : undefined})}>{pending ? '继续复核' : '查看结果'} →</a></TableCell></TableRow>
        })}</TableBody></Table></DataSurface></div>
        <div className="lg:hidden"><div className="divide-y">{rows.map(row => <HistoryMobileRow key={row.id} row={row} />)}</div></div>
        </div>
      </div>}
      {next !== null && <Button type="button" disabled={busy} onClick={() => void load(false)}>加载更多</Button>}
    </PageLayout>
  )
}
