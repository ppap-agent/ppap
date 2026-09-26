import {useEffect, useState} from 'react'
import {dateLabel, request, type QuotaEstimate} from './api'
import {productHref} from './navigation/route'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Input} from '@/components/ui/input'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table'
import {DataSurface, FilterBar, PageHeader, PageLayout} from './ui/PageLayout'

interface Usage {id: string; created_at: string; consumed_points: number; is_free_trial: boolean; round_id?: string}

function UsageMobileRow({row}: {row: Usage}) {
  return (
    <article className="grid gap-2 py-4 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-4">
        <time className="text-sm text-muted-foreground">{dateLabel(row.created_at)}</time>
        <Badge variant={row.is_free_trial ? 'outline' : 'secondary'}>{row.is_free_trial ? '试用' : '额度'}</Badge>
      </div>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-xl font-semibold tracking-tight">{row.consumed_points.toLocaleString('zh-CN')} <span className="text-sm font-normal text-muted-foreground">点</span></span>
        {row.round_id && <a className="text-sm font-medium text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: row.round_id})}>查看审核 →</a>}
      </div>
    </article>
  )
}

export default function UsageView() {
  const [rows, setRows] = useState<Usage[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [next, setNext] = useState<number | null>(null)
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [quota, setQuota] = useState<QuotaEstimate | null>(null)

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
    try {
      setQuota(await request<QuotaEstimate>('/integrations/feishu/usage/quota'))
    } catch {
      setQuota(null)
    }
  }

  useEffect(() => { void load(); void loadQuota() }, [])

  return (
    <PageLayout>
      <PageHeader title="用量记录" description={quota?.remaining_trial_ai_points != null && <span data-testid="quota-summary">试用剩余 {quota.remaining_trial_ai_points.toLocaleString('zh-CN')} 点</span>} />
      <FilterBar as="form" role="search" label="筛选用量记录" className="grid gap-3 sm:grid-cols-2 lg:flex lg:flex-wrap lg:items-end" onSubmit={event => { event.preventDefault(); void load() }}>
        <label className="grid min-w-0 gap-1.5 text-sm font-medium lg:w-60">起始日期<Input value={since} onChange={event => setSince(event.target.value)} type="date" disabled={busy} /></label>
        <label className="grid min-w-0 gap-1.5 text-sm font-medium lg:w-60">截止日期<Input value={until} onChange={event => setUntil(event.target.value)} type="date" disabled={busy} /></label>
        <Button type="submit" disabled={busy}>查询</Button>
      </FilterBar>
      {error && <Alert variant="destructive" data-testid="usage-error"><AlertTitle>无法读取用量</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {busy && !rows.length && <Alert><AlertDescription>正在加载用量记录…</AlertDescription></Alert>}
      {!busy && !rows.length && !error && <Alert data-testid="usage-empty"><AlertTitle>暂无用量记录</AlertTitle></Alert>}
      {!!rows.length && <div data-testid="usage-list">
        <div className="hidden lg:block"><DataSurface>
          <Table>
            <TableHeader><TableRow><TableHead>时间</TableHead><TableHead>用量（点）</TableHead><TableHead>类型</TableHead><TableHead>关联审核</TableHead></TableRow></TableHeader>
            <TableBody>
              {rows.map(row => (
                <TableRow key={row.id}>
                  <TableCell>{dateLabel(row.created_at)}</TableCell>
                  <TableCell><span className="font-medium">{row.consumed_points.toLocaleString('zh-CN')} <span className="text-xs text-muted-foreground">点</span></span></TableCell>
                  <TableCell><Badge variant={row.is_free_trial ? 'outline' : 'secondary'}>{row.is_free_trial ? '试用' : '额度'}</Badge></TableCell>
                  <TableCell>{row.round_id ? <a className="text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: row.round_id})}>查看审核 →</a> : null}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DataSurface></div>
        <div className="divide-y lg:hidden">{rows.map(row => <UsageMobileRow key={row.id} row={row} />)}</div>
      </div>}
      {next !== null && <Button type="button" disabled={busy} onClick={() => void load(false)}>加载更多</Button>}
    </PageLayout>
  )
}
