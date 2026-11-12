import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/3deae156470647bda90de9665bafccff
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Download, RefreshCw} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Progress} from '../components/ui/progress'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {Segmented} from '../components/segmented'
import {Pager} from '../components/pager'
import {ArchiveHistory} from './history'
import {readArchiveAddress, writeArchiveAddress, type AuditArchiveAdapter} from './archive-contract'
import type {OriginalRenderer} from './review-content'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {sameSnapshot} from './result-projection'
import {readAuditRoute} from './routes'
import {readUsageQuery, settlementStates, writeUsageQuery, usageNotices, validUsagePage, type AuditUsageAdapter, type UsagePage, type UsageMetric, type UsageQuery} from './usage-contract'

const states = {charged: '已扣费', pending: '待扣费', processing: '扣费处理中', unknown: '待确认', trial: '免费额度已扣'}
const stateTone = {charged: 'success', pending: 'warning', processing: 'info', unknown: 'warning', trial: 'outline'} as const
const ranges = [['7d', '最近 7 天'], ['30d', '最近 30 天'], ['90d', '最近 90 天'], ['all', '全部记录']] as const
const zoneLabel = (zone: string) => ({'Asia/Shanghai': '北京时间'} as Record<string, string>)[zone] || zone
const metricText = (metric?: UsageMetric) => metric?.value === null || !metric ? '—' : metric.value.toLocaleString('zh-CN')
/** 2026-11-09 14:32 in the ledger's own time zone. */
function timeText(value: string, timezone: string) {
  const parts = new Intl.DateTimeFormat('en', {timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(new Date(value))
  const part = (type: string) => parts.find(row => row.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`
}
const known = (metric?: UsageMetric): metric is UsageMetric & {value: number} => metric?.value !== null && metric?.value !== undefined
export function AuditUsage({active, adapter, archiveAdapter, reportAdapter, sharingAdapter, renderOriginal, onOpenTask, onSupport, headerActions}: {active: boolean; adapter?: AuditUsageAdapter; archiveAdapter?: AuditArchiveAdapter; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; renderOriginal?: OriginalRenderer; onOpenTask: (id: string) => void; onSupport: (notice: string) => void; headerActions?: HTMLElement | null}) {
  const [query, setQuery] = useState(() => readUsageQuery(location.search)), [search, setSearch] = useState(query.search), [page, setPage] = useState<UsagePage | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0), [address, setAddress] = useState(() => readArchiveAddress(location.search)), [route, setRoute] = useState(0)
  const epoch = useRef(0), organization = useRef('')
  const [exporting, setExporting] = useState(false), [exportError, setExportError] = useState(''), [balance, setBalance] = useState<number | null | undefined>(undefined)
  useEffect(() => {
    if (!active) return
    const changed = () => {
      if (readAuditRoute(new URL(location.href)).surface !== 'usage') return
      const nextAddress = readArchiveAddress(location.search), next = readUsageQuery(location.search)
      setAddress(nextAddress); setRoute(value => value + 1)
      setQuery(current => sameSnapshot(current, next) ? current : next); setSearch(next.search)
    }
    changed(); window.addEventListener('popstate', changed); return () => window.removeEventListener('popstate', changed)
  }, [active])
  useEffect(() => {
    const run = ++epoch.current
    if (!active) {setLoading(false); return}
    if (!adapter) {setPage(null); setLoading(false); setError('用量暂不可用，请稍后重试。'); return}
    setLoading(true); setError('')
    void adapter.readUsage(structuredClone(query)).then(next => {
      if (epoch.current !== run) return
      if (!validUsagePage(next, query, organization.current)) throw new Error('usage_projection_mismatch')
      organization.current = next.organization_id; setPage(next)
    }).catch(failure => {if (epoch.current === run) {const status = failure instanceof Error && 'status' in failure ? failure.status : null; if (status === 401 || status === 403) setPage(null); setError(status === 401 ? '登录已失效。恢复登录后再读取用量。' : status === 403 ? '当前账号无权读取企业用量。' : '用量读取失败，当前余额和结算状态待确认。')}}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [active, adapter, query, reload])
  // The Feishu balance is read beside the ledger; a failed read hides the line rather than showing a guessed number.
  useEffect(() => {
    if (!active || !adapter?.readBalance) return
    let current = true
    void adapter.readBalance().then(value => {if (current) setBalance(value)}).catch(() => {if (current) setBalance(undefined)})
    return () => {current = false}
  }, [active, adapter, reload])
  async function exportEntries() {
    if (!adapter?.exportUsage || exporting) return
    setExporting(true); setExportError('')
    let url = ''
    try {
      const blob = await adapter.exportUsage(structuredClone(query))
      url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `用量明细-${page?.through_date || 'export'}.csv`; link.click()
    } catch {setExportError('用量明细导出未完成，请重试。')}
    finally {if (url) URL.revokeObjectURL(url); setExporting(false)}
  }
  function filter(update: Partial<UsageQuery>) {const next = {...query, ...update, page: update.page || 1}; epoch.current += 1; setQuery(next); writeUsageQuery(next)}
  function navigate(documentId: string | null, reference: typeof address.reference, report = false, replace = false) {writeArchiveAddress(documentId, reference, report, replace, 'usage'); setAddress(readArchiveAddress(location.search))}
  if (!active) return null
  const detailRequested = Boolean(address.documentId || address.reference || address.invalid || address.report)
  if (detailRequested) return !address.invalid && address.documentId && address.reference && archiveAdapter ? <ArchiveHistory key={`${address.documentId}:${route}`} document={null} documentId={address.documentId} initialAddress={address} adapter={archiveAdapter} onNavigate={(reference, report, replace) => navigate(address.documentId, reference, report, replace)} onBack={() => navigate(null, null)} backLabel="返回用量记录" onOpenTask={onOpenTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal}/> : <Alert><AlertTitle>这次消费的审核记录暂不可用</AlertTitle><AlertDescription>无法打开这次审核，请返回用量记录。<Button variant="outline" onClick={() => navigate(null, null)}>返回用量记录</Button></AlertDescription></Alert>
  const current = page && sameSnapshot(page.query, query) ? page : null, summary = page?.summary
  // Each charge is a fixed number of points per file; the price line shows only when a row says what it is.
  // Deep mode adds 250 per file whose deep result was committed, so only a uniform page names one price.
  const price = current?.entries.length && current.entries.every(row => row.points === current.entries[0].points) ? current.entries[0].points : undefined
  const change = known(summary?.consumption_count) && known(summary?.previous_consumption_count) ? summary.consumption_count.value - summary.previous_consumption_count.value : null
  const used = known(summary?.free_remaining) && known(summary?.free_total) ? summary.free_total.value - summary.free_remaining.value : null
  const metrics: {title: string; value: string; unit: string; note?: string | null; tone?: string; meter?: number}[] = summary ? [
    {title: '本月消费次数', value: metricText(summary.consumption_count), unit: '次', note: summary.consumption_count.reason || summary.previous_consumption_count?.reason || (change === null ? null : `较上月 ${change >= 0 ? '+' : '-'}${Math.abs(change).toLocaleString('zh-CN')} 次`)},
    {title: '消费点数', value: metricText(summary.charged_points), unit: '点', note: summary.charged_points.reason || (price ? `按 ${price} 点/份计费` : null)},
    {title: '待扣费点数', value: metricText(summary.pending_points), unit: '点', note: summary.pending_points.reason || '额度充值后自动扣费', tone: known(summary.pending_points) && summary.pending_points.value > 0 ? 'text-warning' : undefined},
    {title: '免费额度', value: `${used === null ? '—' : used.toLocaleString('zh-CN')} / ${metricText(summary.free_total)}`, unit: '点', note: summary.free_remaining.reason || summary.free_total.reason, meter: used !== null && known(summary.free_total) && summary.free_total.value ? Math.min(100, used / summary.free_total.value * 100) : undefined},
  ] : []
  const refresh = <Button variant="outline" type="button" disabled={loading} onClick={() => setReload(value => value + 1)}><RefreshCw/>刷新用量</Button>
  const exportButton = adapter?.exportUsage && <Button variant="outline" type="button" disabled={exporting || !current?.total} onClick={() => void exportEntries()}><Download/>{exporting ? '正在导出…' : '导出明细'}</Button>
  return <section className="min-w-0 space-y-4" aria-label="企业用量记录">
    {headerActions && createPortal(<>{exportButton}{refresh}</>, headerActions)}
    <header className="flex flex-wrap items-center justify-between gap-3"><div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1"><h1 className="text-xl font-semibold">用量记录</h1><p className="text-xs text-muted-foreground">记录每份文件的审核消耗，可按时间范围筛选并导出明细</p></div>{!headerActions && <div className="flex gap-2">{exportButton}{refresh}</div>}</header>
    {exportError && <Alert variant="destructive"><AlertTitle>导出未完成</AlertTitle><AlertDescription>{exportError}<Button variant="outline" disabled={exporting} onClick={() => void exportEntries()}>重新导出</Button></AlertDescription></Alert>}
    {/* Until the first summary arrives the loading line below speaks; no dashes stand in for numbers. */}{summary && <section aria-label="自然月用量汇总" className="space-y-2"><p className="text-xs text-muted-foreground">统计月份 {summary.month} · {zoneLabel(summary.timezone)}{page?.organization_name && !page.organization_name.startsWith('feishu:') && <> · {page.organization_name}</>} · <time dateTime={summary.as_of}>更新于 {timeText(summary.as_of, summary.timezone)}</time></p>{(loading || error) && <p role="status" className="text-sm">以下是上次已读汇总，尚未确认当前状态。</p>}<div className="grid grid-cols-4 gap-3.5">{metrics.map(metric => <div role="group" aria-label={metric.title} key={metric.title} className="flex min-w-0 flex-col gap-1.5 rounded-md border bg-card px-4.5 py-4"><p className="text-xs text-muted-foreground">{metric.title}</p><p className={`break-words text-2xl leading-tight font-semibold tabular-nums ${metric.tone ?? ''}`}>{metric.value}{!metric.value.startsWith('—') && <span className="ml-1 text-xs font-normal text-muted-foreground">{metric.unit}</span>}</p>{metric.meter !== undefined && <Progress className="h-1.5" aria-label="免费额度已用比例" value={metric.meter}/>}{metric.note && <p className="text-xs text-muted-foreground">{metric.note}</p>}</div>)}</div></section>}
    {current?.notices.map(notice => <Alert key={notice}><AlertTitle>额度提示</AlertTitle><AlertDescription>{usageNotices[notice]}<Button variant="outline" onClick={() => onSupport(usageNotices[notice])}>联系我们</Button></AlertDescription></Alert>)}
    {current?.unattributed && current.unattributed.slice_count > 0 && <Alert><AlertTitle>另有 {current.unattributed.points.toLocaleString('zh-CN')} 点历史费用待确认</AlertTitle><AlertDescription>{current.unattributed.reason}</AlertDescription></Alert>}
    <form aria-label="筛选用量记录" className="flex flex-wrap items-center gap-2" onSubmit={event => {event.preventDefault(); filter({search: search.trim()})}}><Segmented label="用量时间范围" value={query.range} options={ranges} onChange={range => filter({range})}/><div className="w-36"><Select value={query.settlement || 'all'} onValueChange={value => filter({settlement: settlementStates.find(state => state === value) || ''})}><SelectTrigger aria-label="全部扣费状态" className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部扣费状态</SelectItem>{settlementStates.map(state => <SelectItem key={state} value={state}>{states[state]}</SelectItem>)}</SelectContent></Select></div><Input aria-label="搜索文件名或发起人" className="w-56" value={search} placeholder="搜索文件名或发起人" onChange={event => setSearch(event.target.value)}/>{balance !== undefined && <p className="ml-auto text-xs text-muted-foreground">{balance === null ? '飞书通用AI点数余额暂时无法读取' : <>飞书通用AI点数余额：<b className="font-semibold text-foreground tabular-nums">{balance.toLocaleString('zh-CN')}</b> 点</>}</p>}</form>
    {loading && <ReadSkeleton label="正在读取企业用量…" kind={current ? 'inline' : 'table'}/>}{error && <Alert variant="destructive"><AlertTitle>用量读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取用量</Button></AlertDescription></Alert>}
    {current && <div className="min-w-0 space-y-3">
      {error && <p role="status">下方保留上次读取的记录，不代表当前余额或最新结算。</p>}
      <p className="text-xs text-muted-foreground">明细范围：{current.from_date || '全部历史'} 至 {current.through_date}</p>
      {current.entries.length > 0 && <div className="overflow-hidden rounded-lg border bg-card"><Table aria-label="逐文件用量记录" className="min-w-[56rem]">
        <TableHeader variant="muted"><TableRow><TableHead className="w-40">时间</TableHead><TableHead>飞书订单号</TableHead><TableHead>文档名称</TableHead><TableHead>扣费状态</TableHead><TableHead>发起人</TableHead><TableHead className="w-32">扣费点数</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
        <TableBody>{current.entries.map(row => <TableRow key={row.usage_id} aria-label={`消费记录 ${row.usage_id}`}>
          {/* When it was accepted leads; when it was charged follows in the same cell. */}
          <TableCell className="whitespace-nowrap"><time dateTime={row.occurred_at} title={row.occurred_at}>{timeText(row.occurred_at, current.summary.timezone)}</time>{row.debited_at && <p className="mt-1 text-xs text-muted-foreground">扣费 <time dateTime={row.debited_at} title={row.debited_at}>{timeText(row.debited_at, current.summary.timezone)}</time></p>}</TableCell>
          <TableCell className="max-w-48 whitespace-normal">{row.orders.length ? <span className="grid gap-0.5 font-mono text-xs">{row.orders.map(order => <span key={order.order_id} className="break-all">{order.order_id}</span>)}</span> : <span className="text-xs text-muted-foreground">{row.order_reason || '—'}</span>}</TableCell>
          <TableCell className="min-w-48 max-w-72 whitespace-normal"><FileName name={row.filename} colored/>{row.audit_sequence !== null && <p className="mt-1 pl-8 text-xs text-muted-foreground">第 {row.audit_sequence} 次审核</p>}</TableCell>
          <TableCell className="max-w-56 whitespace-normal"><Badge variant={stateTone[row.settlement.state]} title={row.orders.map(order => `订单 ${order.order_id}`).join('、') || undefined}>{states[row.settlement.state]}</Badge>{row.settlement.reason && <p className="mt-1 text-xs text-muted-foreground">{row.settlement.reason}</p>}</TableCell>
          <TableCell>{row.initiator}</TableCell>
          <TableCell className="whitespace-normal"><p className="tabular-nums">{row.points}</p><p className="text-xs text-muted-foreground">{row.allocation.free_points !== null && row.allocation.paid_points !== null ? `免费 ${row.allocation.free_points} · 付费 ${row.allocation.paid_points}` : row.allocation.reason}</p></TableCell>
          <TableCell className="max-w-48 whitespace-normal"><Button variant="link" size="sm" disabled={loading || Boolean(error) || !archiveAdapter || row.audit_access.state !== 'allowed' || !row.document_id} onClick={() => navigate(row.document_id, row.reference)}>查看</Button>{row.audit_access.state !== 'allowed' && <p className="text-xs text-muted-foreground">{row.audit_access.reason || '未取得这次审核的访问许可。'}</p>}</TableCell>
        </TableRow>)}</TableBody>
      </Table></div>}
      {!current.entries.length && !loading && !error && <p className="py-10 text-center text-sm text-muted-foreground">当前筛选下没有消费记录。</p>}
      <Pager total={current.total} page={query.page} disabled={loading || Boolean(error)} onPage={value => filter({page: value})}/>
    </div>}
  </section>
}
