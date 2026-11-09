import {isPreview} from './access'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch: projects/13261964020862199279/screens/fc53fb033c4c471ea70130a8e7b95e9a.
// Three account-workspace structures share the original case/check records.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, CircleHelp, LoaderCircle, RefreshCw} from 'lucide-react'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Input} from '../components/ui/input'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {exampleAllowance, readUsage, reconcileUsage, type CheckOwner, type UsageOutcome, type UsageRow} from './usage-state'
import {dateLabel, type UsageList, type QuotaEstimate} from '../api'
import {useRemote} from './remote'
const layouts = ['额度与明细', '明细优先', '待确认优先']
const stamp = (value: string) => new Date(value).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false})
export function UsageWorkspace({onOpen}: {onOpen: (id: string, owner?: CheckOwner) => void}) {
  return isPreview() ? <PreviewUsage onOpen={onOpen}/> : <ProductUsage/>
}
function ProductUsage() {
  const [offset, setOffset] = useState(0)
  const usage = useRemote<UsageList>(`/usage?limit=50&offset=${offset}`)
  const quota = useRemote<QuotaEstimate>('/integrations/feishu/usage/quota?target_count=0')
  return <section className="space-y-6">
    <header className="flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold">用量</h1><Button variant="outline" onClick={() => {usage.refresh(); quota.refresh()}}><RefreshCw/>刷新</Button></header>
    {quota.error && <p role="alert">{quota.error}</p>}
    {quota.loading && !quota.value && <ReadSkeleton label="正在读取可用额度…" kind="inline"/>}
    {quota.value && <p className="text-sm text-muted-foreground">{!quota.value.usage_charge_enabled ? '当前环境未启用扣费' : `可用额度：${quota.value.user_quota ?? '暂不可用'}`}</p>}
    {usage.error && <p role="alert">{usage.error}</p>}
    <Table><TableHeader><TableRow><TableHead>时间</TableHead><TableHead>点数</TableHead><TableHead>类型</TableHead><TableHead>审核</TableHead></TableRow></TableHeader><TableBody>{usage.loading && !usage.value ? <TableRow><TableCell colSpan={4}><ReadSkeleton label="正在读取用量…" kind="table" rows={5}/></TableCell></TableRow> : usage.value?.events.map(event => <TableRow key={event.id}><TableCell>{dateLabel(event.created_at)}</TableCell><TableCell>{event.consumed_points}</TableCell><TableCell>{event.is_free_trial ? '试用' : '标准用量'}</TableCell><TableCell>{event.round_id && <Button variant="ghost" asChild><a href={`/?round=${encodeURIComponent(event.round_id)}`}>查看审核<ArrowRight/></a></Button>}</TableCell></TableRow>)}</TableBody></Table>
    {usage.value && !usage.value.events.length && <p className="text-sm text-muted-foreground">暂无用量记录</p>}
    <div className="flex justify-end gap-2"><Button variant="outline" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</Button><Button variant="outline" disabled={usage.value?.next_offset == null} onClick={() => setOffset(usage.value!.next_offset!)}>下一页</Button></div>
  </section>
}
function PreviewUsage({onOpen}: {onOpen: (id: string, owner?: CheckOwner) => void}) {
  const [snapshot, setSnapshot] = useState(readUsage)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [outcome, setOutcome] = useState<UsageOutcome>('normal')
  const [readFailure, setReadFailure] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [layout, setLayout] = useState(() => Math.max(0, layouts.indexOf(new URLSearchParams(location.search).get('usageLayout') ?? '')))
  const from = new URLSearchParams(location.search).get('fromCase')
  const fromKind = new URLSearchParams(location.search).get('fromKind'), fromRound = new URLSearchParams(location.search).get('fromRound')
  const fromOwner: CheckOwner | undefined = (fromKind === 'changes' || fromKind === 'reassessment') && fromRound ? {kind: fromKind, roundId: fromRound} : undefined
  const unavailable = readFailure || snapshot.unavailable
  const unknown = unavailable || snapshot.incomplete
  const shown = snapshot.rows.filter(row => `${row.title} ${row.context} ${row.runId} ${row.request?.id ?? ''}`.toLowerCase().includes(query.trim().toLowerCase()) && (filter === 'all' || filter === 'attention' && row.attention || filter === 'settled' && row.actual !== null))
  function refresh() {if (!readFailure) setSnapshot(readUsage()); setMessage(readFailure ? '模拟读取失败。上次记录仍保留，暂不显示确定的可用额度。' : '已重新读取此浏览器的检查记录。')}
  useEffect(() => {
    const sync = () => setSnapshot(readUsage())
    window.addEventListener('focus', sync); window.addEventListener('storage', sync)
    return () => {window.removeEventListener('focus', sync); window.removeEventListener('storage', sync); if (timer.current) clearTimeout(timer.current)}
  }, [])
  function reconcile(row: UsageRow) {
    if (!row.request || busy) return
    setBusy(row.key); setMessage(row.request.phase === 'accepted' ? '正在核对原检查的结算结果…' : '正在核对原请求是否接收…')
    timer.current = setTimeout(() => {
      setMessage(reconcileUsage(row.caseId, row.runId, outcome))
      setSnapshot(readUsage()); setBusy(''); timer.current = null
    }, 700)
  }
  function choose(next: number) {setLayout(next); const url = new URL(location.href); url.searchParams.set('usageLayout', layouts[next]); history.replaceState(null, '', url)}
  useEffect(() => {
    const key = (event: KeyboardEvent) => {if (!isPreview() || !import.meta.env.DEV) return;
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement).closest('input,textarea,select,button,a,[role=dialog],[contenteditable=true]')) return
      choose((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key)
  })
  const summary = <section className={"flex items-center gap-10 pt-3 pr-0 pb-7 pl-0 border-b mb-5.5 [&_dl]:flex [&_dl]:gap-6.5 [&_dl]:flex-wrap [&_dd]:text-xl [&_dd]:font-semibold [&_dd]:tabular-nums [&_dd]:mt-1 max-xl:block max-xl:[&_dl]:mt-4.5 max-xl:[&_dl]:gap-7.5 max-sm:[&_dl]:gap-5 max-sm:[&_dd]:text-lg max-sm:[&_dd]:whitespace-nowrap"} aria-label="额度概览"><div className={"flex-1 [&>span]:text-muted-foreground [&>strong]:block [&>strong]:text-3xl [&>strong]:leading-tight [&>strong]:tracking-tight [&>strong]:tabular-nums [&>strong]:my-1 [&>strong]:mx-0 [&_strong_small]:text-sm [&_strong_small]:font-normal [&_strong_small]:tracking-wide [&_p]:text-sm max-sm:[&>strong]:text-3xl"}><span>可用额度</span><strong>{unknown ? '待确认' : snapshot.available?.toLocaleString()} {!unknown && <small>点</small>}</strong><p>{unknown ? '记录尚不完整，暂不能确认总余额。' : '已扣除结算用量和仍保留的预留。'}</p></div><dl><div><dt>示例总额度</dt><dd>{exampleAllowance.toLocaleString()} 点</dd></div><div><dt>已知结算</dt><dd>{snapshot.charged.toLocaleString()} 点</dd></div><div><dt>仍在预留</dt><dd>{snapshot.held.toLocaleString()} 点</dd></div></dl></section>
  const warning = unknown && <div className={"flex items-start gap-3 py-4 px-0 mb-5 border-b text-foreground [&>svg]:shrink-0 [&>svg]:mt-0.5 [&>div]:flex-1 [&_p]:text-sm max-sm:flex-wrap max-sm:[&>div]:min-w-50"} role="status"><CircleHelp size={20}/><div><strong>{unavailable ? '暂时无法读取完整记录' : '部分检查缺少可核对的用量'}</strong><p>{unavailable ? '下方只保留上次读取的信息，恢复读取后再确认额度。' : snapshot.unreadable ? `${snapshot.unreadable} 份本机记录无法读取。` : snapshot.duplicate ? '发现重复检查号，暂不提供确定的合计余额。' : '缺少用量的旧检查不计为零。已知结算与预留只是部分合计。'}</p></div><Button variant="outline" onClick={refresh}>重新读取</Button></div>
  function records(rows: UsageRow[], title: string) {
    return <section className={"my-6 mx-0 max-sm:mt-5"} aria-label={title}><h2>{title}<span>{rows.length}</span></h2>{rows.length ? <Table><TableHeader><TableRow><TableHead>检查与事项</TableHead><TableHead>用量状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.key}>
      <TableCell><strong>{row.title}</strong><small>{row.context}</small><small>{row.execution}</small><Collapsible className={"mt-2"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">{row.runId} · {stamp(row.at)}</Button></CollapsibleTrigger><CollapsibleContent><dl>{row.request && <><div><dt>请求</dt><dd>{row.request.id} · 第 {row.request.attempt} 次连接</dd></div><div><dt>审核依据</dt><dd>{row.request.plan.basis}</dd></div><div><dt>原件版本</dt><dd>{row.request.plan.files.map(file => <span key={file}>{file}</span>)}</dd></div>{row.request.settlement && <div><dt>用量核对</dt><dd>{stamp(row.request.settlement.at)}</dd></div>}</>}</dl><p>{row.detail}</p></CollapsibleContent></Collapsible></TableCell>
      <TableCell><strong className={row.attention ? "text-foreground" : ""}>{row.status}</strong><span>{row.actual !== null ? `结算 ${row.actual} 点` : row.held === null ? '金额尚不明确' : row.held > 0 ? `预留 ${row.held} 点` : '预留已释放'}</span></TableCell>
      <TableCell><div className={"flex items-stretch flex-col gap-2 max-sm:flex-row max-sm:justify-end"}><Button variant="ghost" onClick={() => onOpen(row.caseId, row.owner)}>返回事项<ArrowRight size={15}/></Button>{row.request && row.request.phase !== 'absent' && <Button variant="outline" disabled={Boolean(busy) || unavailable} onClick={() => reconcile(row)}>{busy === row.key ? <LoaderCircle size={15} className={""}/> : <RefreshCw size={15}/>}核对{row.request.phase === 'accepted' ? '结算' : '接收'}</Button>}</div></TableCell>
    </TableRow>)}</TableBody></Table> : <p className={"py-6.5 px-0"}>{query || filter !== 'all' ? '没有符合筛选条件的检查。' : '尚无检查记录。完成资料登记并开始检查后，用量会出现在这里。'}</p>}</section>
  }
  return <div className={["min-w-0 pb-12.5 text-foreground text-sm leading-normal [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-3 [&_h2]:ml-0 [&_h2>span]:ml-2.5 [&_h2>span]:text-muted-foreground [&_h2>span]:text-sm [&_h2>span]:font-normal [&_p]:my-2 [&_p]:mx-0 [&_p]:text-muted-foreground [&_dt]:text-muted-foreground [&_dt]:text-xs [&_table]:table-fixed [&_table]:w-full [&_th]:font-medium [&_th]:text-muted-foreground [&_th]:text-xs [&_th:first-child]:w-[52%] [&_th:last-child]:w-39 [&_td]:align-top [&_td]:py-4 [&_td]:px-2 [&_td]:whitespace-normal [&_td]:break-all [&_td:first-child]:pl-0 [&_th:first-child]:pl-0 [&_td>strong]:block [&_td>span]:block [&_td>small]:block [&_td>small]:text-xs [&_td>small]:text-muted-foreground [&_td>small]:my-1.5 [&_td>small]:mx-0 [&_td>span]:text-sm [&_td>span]:mt-1.5 max-xl:[&_th:first-child]:w-[48%] max-xl:[&_th:last-child]:w-34 max-sm:[&_th]:hidden max-sm:[&_tbody]:block max-sm:[&_tr]:block max-sm:[&_tr]:py-4 max-sm:[&_tr]:px-0 max-sm:[&_td]:block max-sm:[&_td]:w-full max-sm:[&_td]:pt-0 max-sm:[&_td]:pr-0 max-sm:[&_td]:pb-2.5 max-sm:[&_td]:pl-0 max-sm:[&_td:nth-child(2)]:flex max-sm:[&_td:nth-child(2)]:items-center max-sm:[&_td:nth-child(2)]:justify-between max-sm:[&_td:nth-child(2)]:gap-3 max-sm:[&_td>span]:m-0 max-sm:[&_thead]:hidden max-sm:pb-25",({"1":"[&_td]:py-3 max-sm:[&_td]:block max-sm:[&_td]:w-full max-sm:[&_td]:pt-0 max-sm:[&_td]:pr-0 max-sm:[&_td]:pb-2.5 max-sm:[&_td]:pl-0"} as Record<string,string>)[String(layout)] ?? "",""].filter(Boolean).join("")}>
    <header className={"flex items-center justify-between gap-5 mb-7 [&_h1]:m-0 [&_h1]:text-3xl [&_h1]:font-semibold [&_h1]:tracking-tight max-md:mb-5.5 max-md:[&_h1]:text-2xl"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>审核团队</div><h1>用量</h1></div>{from ? <Button variant="outline" onClick={() => onOpen(from, fromOwner)}><ArrowLeft size={16}/>返回原事项</Button> : <Button variant="outline" onClick={refresh}><RefreshCw size={16}/>重新读取</Button>}</header>
    {summary}{warning}
    <div className={"flex gap-2.5 flex-wrap mt-6 mr-0 mb-2.5 ml-0 max-sm:grid max-sm:grid-cols-1"}><Input aria-label="搜索事项或检查号" placeholder="搜索事项或检查号" value={query} onChange={event => setQuery(event.target.value)}/><NativeSelect aria-label="筛选用量状态" value={filter} onChange={event => setFilter(event.target.value)}><NativeSelectOption value="all">全部记录</NativeSelectOption><NativeSelectOption value="attention">待确认与待结算</NativeSelectOption><NativeSelectOption value="settled">已结算</NativeSelectOption></NativeSelect>{(query || filter !== 'all') && <Button variant="ghost" onClick={() => {setQuery(''); setFilter('all')}}>清除</Button>}</div>
    <p className={"min-h-5.5 text-sm empty:hidden"} role="status">{message}</p>
    {layout === 2 && filter === 'all' ? <>{records(shown.filter(row => row.attention), '待确认与待结算')}<Collapsible className={"mt-6"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">已确认记录 · {shown.filter(row => !row.attention).length}</Button></CollapsibleTrigger><CollapsibleContent>{records(shown.filter(row => !row.attention), '已确认')}</CollapsibleContent></Collapsible></> : records(shown, filter === 'settled' ? '已结算' : filter === 'attention' ? '待确认与待结算' : '检查明细')}
    {isPreview() && import.meta.env.DEV && <><Collapsible className={"mt-6"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">演示用量读取情形</Button></CollapsibleTrigger><CollapsibleContent><label>下一次核对结果<NativeSelect value={outcome} onChange={event => setOutcome(event.target.value as UsageOutcome)}><NativeSelectOption value="normal">正常返回（结束后结算）</NativeSelectOption><NativeSelectOption value="pending">结果尚未就绪</NativeSelectOption><NativeSelectOption value="unknown">结果未知</NativeSelectOption><NativeSelectOption value="failed">读取失败</NativeSelectOption><NativeSelectOption value="zero">明确结算为 0 点</NativeSelectOption></NativeSelect></label><Button variant="outline" onClick={() => {if (readFailure) setSnapshot(readUsage()); setReadFailure(!readFailure); setMessage('')}}>{readFailure ? '恢复记录读取' : '模拟记录读取失败'}</Button></CollapsibleContent></Collapsible><aside className={"fixed z-10 bottom-5 left-[50%] flex items-center gap-2.5 p-1.5 border rounded-lg bg-muted shadow-sm text-foreground text-xs whitespace-nowrap max-sm:bottom-3.5"} aria-label="用量布局切换"><Button variant="ghost" aria-label="上一种用量布局" onClick={() => choose((layout + 2) % 3)}><ArrowLeft size={16}/></Button><span>布局方案　{layout + 1} / 3 · {layouts[layout]}</span><Button variant="ghost" aria-label="下一种用量布局" onClick={() => choose((layout + 1) % 3)}><ArrowRight size={16}/></Button></aside></>}
  </div>
}
