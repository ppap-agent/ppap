// My receipts (#361): the person's own signing actions, newest first, each downloadable as a PDF. Opened from the account
// menu, a receipt notice, or a link an MCP client was given (`?receipt=` highlights that one).
import {useEffect, useRef, useState} from 'react'
import {apiUrl, request} from '../api'
import {iconArrowsRotate, iconDownload} from '../components/icons'
import {ReadSkeleton} from '../components/read-skeleton'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Icon} from '../components/ui/icon'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {cn} from '../lib/utils'
import {beijingTime, pointsText, type Receipt, type ReceiptList} from './receipts-contract'
import {openInPlace, readAuditRoute, taskAddress} from './routes'
import {host} from '../host'

const outcomeTone = {succeeded: 'success', failed: 'destructive'} as const
const readHighlight = () => new URL(location.href).searchParams.get('receipt')

function ReceiptRow({receipt, current}: {receipt: Receipt; current: boolean}) {
  const tone = outcomeTone[receipt.outcome.state as keyof typeof outcomeTone] ?? 'warning'
  // The row asked for by its link is the table's own selected row.
  return <TableRow aria-current={current || undefined} data-state={current ? 'selected' : undefined}>
    <TableCell label="回执编号" className="align-top whitespace-nowrap"><span className="tabular-nums">{receipt.number}</span></TableCell>
    <TableCell label="时间" className="align-top whitespace-nowrap"><time dateTime={receipt.at} className="text-muted-foreground tabular-nums">{beijingTime(receipt.at)}</time></TableCell>
    <TableCell lead className="min-w-40 align-top whitespace-normal"><p>{receipt.action}</p>{receipt.details.map((line, index) => <p key={index} className="mt-1 text-xs text-muted-foreground">{line}</p>)}</TableCell>
    <TableCell label="对象" className="align-top whitespace-nowrap">{receipt.task_id ? <a href={taskAddress(receipt.task_id)} onClick={openInPlace} className="text-primary underline-offset-4 hover:underline">{receipt.subject}</a> : receipt.subject}</TableCell>
    <TableCell label="经由" className="max-w-64 align-top whitespace-normal"><span className="text-muted-foreground">{receipt.via}</span></TableCell>
    <TableCell label="结果" className="align-top"><Badge variant={tone} title={receipt.outcome.error ?? undefined}>{receipt.outcome.text}</Badge></TableCell>
    <TableCell label="本轮扣点" className="align-top whitespace-nowrap text-right">{receipt.points && <span className="tabular-nums">{pointsText(receipt.points)}</span>}</TableCell>
    {host.browserDownload && <TableCell className="align-top text-right"><Button variant="link" size="sm" asChild><a href={`${apiUrl}/receipts/${encodeURIComponent(receipt.id)}/pdf`} download={`${receipt.number}.pdf`} aria-label={`下载回执 ${receipt.number}`}><Icon icon={iconDownload}/>PDF</a></Button></TableCell>}
  </TableRow>
}

export function MyReceipts({active}: {active: boolean}) {
  const [rows, setRows] = useState<Receipt[] | null>(null), [next, setNext] = useState<number | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [highlight, setHighlight] = useState(readHighlight), [asked, setAsked] = useState<Receipt | null>(null)
  const epoch = useRef(0), page = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!active) return
    const follow = () => {if (readAuditRoute(new URL(location.href)).surface === 'receipts') setHighlight(readHighlight())}
    follow(); window.addEventListener('popstate', follow)
    return () => window.removeEventListener('popstate', follow)
  }, [active])
  async function read(offset: number) {
    const run = ++epoch.current
    setLoading(true); setError('')
    try {
      const page = await request<ReceiptList>(`/receipts?${new URLSearchParams({offset: String(offset), limit: '20'})}`)
      if (epoch.current !== run) return
      setRows(current => offset && current ? [...current, ...page.receipts.filter(row => !current.some(seen => seen.id === row.id))] : page.receipts); setNext(page.next_offset)
    } catch (cause) {
      if (epoch.current === run) setError(cause instanceof Error ? cause.message : '回执暂时无法读取，请重试。')
    } finally {if (epoch.current === run) setLoading(false)}
  }
  // Every visit reads the list again: a signing done elsewhere since is a new receipt.
  useEffect(() => {if (active) void read(0)}, [active, reload])
  // A receipt asked for by its link is read on its own, so it shows even when it is older than the listed ones.
  useEffect(() => {
    setAsked(null)
    if (!active || !highlight) return
    let current = true
    void request<Receipt>(`/receipts/${encodeURIComponent(highlight)}`).then(found => {if (current) setAsked(found)}, () => undefined)
    return () => {current = false}
  }, [active, highlight])
  const listed = Boolean(highlight && rows?.some(row => row.id === highlight))
  useEffect(() => {if (listed || asked) page.current?.querySelector('tr[aria-current]')?.scrollIntoView({block: 'nearest'})}, [listed, asked])
  if (!active) return null
  const head = <TableHeader variant="muted"><TableRow interactive={false}><TableHead>回执编号</TableHead><TableHead>时间（北京时间）</TableHead><TableHead>操作</TableHead><TableHead>对象</TableHead><TableHead>经由</TableHead><TableHead>结果</TableHead><TableHead className="text-right">本轮扣点</TableHead>{host.browserDownload && <TableHead className="text-right">下载</TableHead>}</TableRow></TableHeader>
  return <section ref={page} className="min-w-0 space-y-4 rounded-lg bg-card p-5 max-xl:p-4" aria-label="我的回执">
    <header className="flex flex-wrap items-center justify-between gap-3"><h1 className="sr-only">我的回执</h1><p className="text-xs text-muted-foreground">你本人每次签字操作的记录：何时、经由什么、做了什么、结果。{host.browserDownload && '每张回执可下载 PDF。'}</p>
      <Button variant="outline" type="button" size="icon-sm" aria-label="刷新回执" disabled={loading} onClick={() => setReload(value => value + 1)}><Icon icon={iconArrowsRotate} className={cn(loading && 'animate-spin motion-reduce:animate-none')}/></Button></header>
    {asked && !listed && <div className="-mx-5 border-y max-xl:mx-0 max-xl:border-0"><Table stack aria-label="你要查看的回执" className="min-w-[60rem] max-xl:min-w-0">{head}<TableBody><ReceiptRow receipt={asked} current/></TableBody></Table></div>}
    {error && <Alert variant="destructive"><AlertTitle>回执读取未完成</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}>重新读取</Button></AlertDescription></Alert>}
    {rows === null ? loading && <ReadSkeleton label="正在读取回执…" kind="table"/> : rows.length === 0 ? <p className="py-12 text-center text-sm text-muted-foreground">还没有回执。开始审核、生成报告、确认规则修订等操作完成后，这里会留下回执。</p>
      : <div className="-mx-5 overflow-hidden border-t max-xl:mx-0 max-xl:border-0"><Table stack aria-label="回执列表" className="min-w-[60rem] max-xl:min-w-0">{head}<TableBody>{rows.map(row => <ReceiptRow key={row.id} receipt={row} current={row.id === highlight}/>)}</TableBody></Table></div>}
    {rows !== null && next !== null && <div className="flex justify-center"><Button variant="outline" size="sm" disabled={loading} onClick={() => void read(next)}>{loading ? '正在读取…' : '加载更多'}</Button></div>}
  </section>
}
