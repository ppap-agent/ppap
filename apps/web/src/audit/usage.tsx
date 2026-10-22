import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/3deae156470647bda90de9665bafccff
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {RefreshCw} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Card, CardContent, CardHeader, CardTitle} from '../components/ui/card'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {ArchiveHistory} from './history'
import {readArchiveAddress, writeArchiveAddress, type AuditArchiveAdapter} from './archive-contract'
import type {OriginalRenderer} from './review-content'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {sameSnapshot} from './result-projection'
import {readAuditRoute} from './routes'
import {readUsageQuery, writeUsageQuery, usageNotices, validUsagePage, type AuditUsageAdapter, type UsagePage, type UsageMetric, type UsageQuery} from './usage-contract'

const states = {charged: '已扣', pending: '待扣', processing: '扣费处理中', unknown: '待核对', trial: '免费额度已扣'}
const metricText = (metric?: UsageMetric) => metric?.value === null || !metric ? '—' : metric.value.toLocaleString('zh-CN')
const timeText = (value: string, timezone: string) => new Intl.DateTimeFormat('zh-CN', {timeZone: timezone, dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23'}).format(new Date(value))
export function AuditUsage({active, adapter, archiveAdapter, reportAdapter, sharingAdapter, renderOriginal, onOpenTask, onSupport, headerActions}: {active: boolean; adapter?: AuditUsageAdapter; archiveAdapter?: AuditArchiveAdapter; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; renderOriginal?: OriginalRenderer; onOpenTask: (id: string) => void; onSupport: (notice: string) => void; headerActions?: HTMLElement | null}) {
  const [query, setQuery] = useState(() => readUsageQuery(location.search)), [search, setSearch] = useState(query.search), [page, setPage] = useState<UsagePage | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0), [address, setAddress] = useState(() => readArchiveAddress(location.search)), [route, setRoute] = useState(0)
  const epoch = useRef(0), organization = useRef('')
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
  function filter(update: Partial<UsageQuery>) {const next = {...query, ...update, page: update.page || 1}; epoch.current += 1; setQuery(next); writeUsageQuery(next)}
  function navigate(documentId: string | null, reference: typeof address.reference, report = false, replace = false) {writeArchiveAddress(documentId, reference, report, replace, 'usage'); setAddress(readArchiveAddress(location.search))}
  if (!active) return null
  const detailRequested = Boolean(address.documentId || address.reference || address.invalid || address.report)
  if (detailRequested) return !address.invalid && address.documentId && address.reference && archiveAdapter ? <ArchiveHistory key={`${address.documentId}:${route}`} document={null} documentId={address.documentId} initialAddress={address} adapter={archiveAdapter} onNavigate={(reference, report, replace) => navigate(address.documentId, reference, report, replace)} onBack={() => navigate(null, null)} backLabel="返回用量记录" onOpenTask={onOpenTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal}/> : <Alert><AlertTitle>这次消费的审核记录暂不可用</AlertTitle><AlertDescription>无法打开这次审核，请返回用量记录。<Button variant="outline" onClick={() => navigate(null, null)}>返回用量记录</Button></AlertDescription></Alert>
  const current = page && sameSnapshot(page.query, query) ? page : null, summary = page?.summary
  const metrics: [string, string, string | null | undefined][] = [['本月消费次数', metricText(summary?.consumption_count), summary?.consumption_count.reason], ['本月已扣点数', metricText(summary?.charged_points), summary?.charged_points.reason], ['本月待扣点数', metricText(summary?.pending_points), summary?.pending_points.reason], ['免费额度剩余/总额', `${metricText(summary?.free_remaining)} / ${metricText(summary?.free_total)}`, summary?.free_remaining.reason || summary?.free_total.reason]]
  const refresh = <Button variant="outline" type="button" disabled={loading} onClick={() => setReload(value => value + 1)}><RefreshCw/>刷新用量</Button>
  return <section className="min-w-0 space-y-6" aria-label="企业用量记录">{headerActions ? <><h1 className="sr-only">用量记录</h1>{createPortal(refresh, headerActions)}</> : <header className="space-y-2"><h1 className="text-2xl font-semibold">用量记录</h1></header>}
    <section aria-label="自然月用量汇总" className="space-y-3"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-medium">{summary ? `${summary.month} 月用量 · ${summary.timezone}` : '月用量待读取'}</h2><p className="text-xs text-muted-foreground">{page?.organization_name}{summary && <> · <time dateTime={summary.as_of}>更新于 {timeText(summary.as_of, summary.timezone)}</time></>}</p></div><p className="text-xs text-muted-foreground">按审核受理月统计；补扣不新增消费。</p>{(loading || error) && summary && <p role="status" className="text-sm">以下是上次已读汇总，尚未确认当前状态。</p>}<div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(([title, value, reason]) => <Card role="group" aria-label={title} size="sm" key={title}><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent><p className="break-words text-2xl font-semibold tabular-nums">{value}</p>{reason && <p className="mt-2 text-xs text-muted-foreground">{reason}</p>}</CardContent></Card>)}</div></section>
    {current?.notices.map(notice => <Alert key={notice}><AlertTitle>额度提示</AlertTitle><AlertDescription>{usageNotices[notice]}<Button variant="outline" onClick={() => onSupport(usageNotices[notice])}>联系我们</Button></AlertDescription></Alert>)}
    {current?.unattributed && current.unattributed.slice_count > 0 && <Alert><AlertTitle>另有 {current.unattributed.points.toLocaleString('zh-CN')} 点历史费用待核对</AlertTitle><AlertDescription>{current.unattributed.reason}</AlertDescription></Alert>}
    <form aria-label="筛选用量记录" className="flex flex-wrap items-center gap-2" onSubmit={event => {event.preventDefault(); filter({search: search.trim()})}}><Select value={query.range} onValueChange={range => filter({range: range as UsageQuery['range']})}><SelectTrigger className="w-36" aria-label="用量时间范围"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="7d">最近7天</SelectItem><SelectItem value="30d">最近30天</SelectItem><SelectItem value="90d">最近90天</SelectItem><SelectItem value="all">全部</SelectItem></SelectContent></Select><Input aria-label="搜索文件名或发起人" className="min-w-0 basis-full sm:max-w-sm sm:basis-0 sm:flex-1" value={search} placeholder="搜索文件名或发起人" onChange={event => setSearch(event.target.value)}/><Button variant="outline" type="submit">搜索</Button>{!headerActions && refresh}</form>
    {loading && <ReadSkeleton label="正在读取企业用量…" kind={current ? 'inline' : 'table'}/>}{error && <Alert variant="destructive"><AlertTitle>用量读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取用量</Button></AlertDescription></Alert>}
    {current && <div className="min-w-0 space-y-3">
      {error && <p role="status">下方保留上次读取的记录，不代表当前余额或最新结算。</p>}
      <p className="text-xs text-muted-foreground">明细范围：{current.from_date || '全部历史'} 至 {current.through_date}</p>
      <Table aria-label="逐文件用量记录" className="min-w-[64rem]">
        <TableHeader><TableRow><TableHead>受理时间</TableHead><TableHead>实际扣费时间</TableHead><TableHead>飞书订单号</TableHead><TableHead>文件 / 审核版本</TableHead><TableHead>扣费状态</TableHead><TableHead>发起人</TableHead><TableHead>点数</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
        <TableBody>{current.entries.map(row => <TableRow key={row.usage_id} aria-label={`消费记录 ${row.usage_id}`}>
          <TableCell><time dateTime={row.occurred_at} title={row.occurred_at}>{timeText(row.occurred_at, current.summary.timezone)}</time></TableCell>
          <TableCell>{row.debited_at ? <time dateTime={row.debited_at} title={row.debited_at}>{timeText(row.debited_at, current.summary.timezone)}</time> : '—'}</TableCell>
          <TableCell className="max-w-44 whitespace-normal break-all">{row.orders.length ? row.orders.map(order => <p key={order.order_id}>{order.order_id}</p>) : <><p>—</p><p className="text-muted-foreground">{row.order_reason}</p></>}</TableCell>
          <TableCell className="min-w-48 max-w-72 whitespace-normal"><FileName name={row.filename}/>{row.audit_sequence !== null && <p className="mt-1 text-xs text-muted-foreground">第 {row.audit_sequence} 次审核</p>}</TableCell>
          <TableCell className="max-w-56 whitespace-normal"><Badge variant="outline">{states[row.settlement.state]}</Badge>{row.settlement.reason && <p className="mt-1 text-xs text-muted-foreground">{row.settlement.reason}</p>}</TableCell>
          <TableCell>{row.initiator}</TableCell>
          <TableCell className="whitespace-normal"><p>{row.points} 点</p><p className="text-xs text-muted-foreground">{row.allocation.free_points !== null && row.allocation.paid_points !== null ? `免费 ${row.allocation.free_points} · 付费 ${row.allocation.paid_points}` : row.allocation.reason}</p></TableCell>
          <TableCell className="max-w-48 whitespace-normal"><Button variant="ghost" size="sm" disabled={loading || Boolean(error) || !archiveAdapter || row.audit_access.state !== 'allowed' || !row.document_id} onClick={() => navigate(row.document_id, row.reference)}>打开该次审核</Button>{row.audit_access.state !== 'allowed' && <p className="text-xs text-muted-foreground">{row.audit_access.reason || '未取得这次审核的访问许可。'}</p>}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
      {!current.entries.length && !loading && !error && <p>当前筛选下没有消费记录。</p>}
      <footer className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">共 {current.total} 笔 · 第 {query.page}/{Math.max(1, Math.ceil(current.total / 10))} 页 · 每页10笔</p><div className="flex gap-2"><Button variant="outline" disabled={loading || Boolean(error) || query.page <= 1} onClick={() => filter({page: query.page - 1})}>上一页用量</Button><Button variant="outline" disabled={loading || Boolean(error) || query.page * 10 >= current.total} onClick={() => filter({page: query.page + 1})}>下一页用量</Button></div></footer>
    </div>}
  </section>
}
