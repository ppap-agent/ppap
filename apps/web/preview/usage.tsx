// Stitch: projects/13261964020862199279/screens/fc53fb033c4c471ea70130a8e7b95e9a.
// Three account-workspace structures share the original case/check records.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, CircleHelp, LoaderCircle, RefreshCw} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Input} from '../src/components/ui/input'
import {NativeSelect, NativeSelectOption} from '../src/components/ui/native-select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../src/components/ui/table'
import {exampleAllowance, readUsage, reconcileUsage, type CheckOwner, type UsageOutcome, type UsageRow} from './usage-state'
import './usage.css'

const layouts = ['额度与明细', '明细优先', '待核对优先']
const stamp = (value: string) => new Date(value).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false})
export function UsageWorkspace({onOpen}: {onOpen: (id: string, owner?: CheckOwner) => void}) {
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
    setBusy(row.key); setMessage(row.request.phase === 'accepted' ? '正在核对原检查的结算回执…' : '正在核对原请求是否受理…')
    timer.current = setTimeout(() => {
      setMessage(reconcileUsage(row.caseId, row.runId, outcome))
      setSnapshot(readUsage()); setBusy(''); timer.current = null
    }, 700)
  }
  function choose(next: number) {setLayout(next); const url = new URL(location.href); url.searchParams.set('usageLayout', layouts[next]); history.replaceState(null, '', url)}
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement).closest('input,textarea,select,button,a,[role=dialog],[contenteditable=true]')) return
      choose((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key)
  })
  const summary = <section className="gf-usage-summary" aria-label="额度概览"><div className="gf-usage-balance"><span>可用额度</span><strong>{unknown ? '待核对' : snapshot.available?.toLocaleString()} {!unknown && <small>点</small>}</strong><p>{unknown ? '记录尚不完整，暂不能确认总余额。' : '已扣除结算用量和仍保留的预留。'}</p></div><dl><div><dt>示例总额度</dt><dd>{exampleAllowance.toLocaleString()} 点</dd></div><div><dt>已知结算</dt><dd>{snapshot.charged.toLocaleString()} 点</dd></div><div><dt>仍在预留</dt><dd>{snapshot.held.toLocaleString()} 点</dd></div></dl></section>
  const warning = unknown && <div className="gf-usage-warning" role="status"><CircleHelp size={20}/><div><strong>{unavailable ? '暂时无法读取完整记录' : '部分检查缺少可核对的用量'}</strong><p>{unavailable ? '下方只保留上次读取的信息，恢复读取后再确认额度。' : snapshot.unreadable ? `${snapshot.unreadable} 份本机记录无法读取。` : snapshot.duplicate ? '发现重复检查号，暂不提供确定的合计余额。' : '缺少用量的旧检查不计为零。已知结算与预留只是部分合计。'}</p></div><Button variant="outline" onClick={refresh}>重新读取</Button></div>
  function records(rows: UsageRow[], title: string) {
    return <section className="gf-usage-records" aria-label={title}><h2>{title}<span>{rows.length}</span></h2>{rows.length ? <Table><TableHeader><TableRow><TableHead>检查与事项</TableHead><TableHead>用量状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.key}>
      <TableCell><strong>{row.title}</strong><small>{row.context}</small><small>{row.execution}</small><details className="gf-usage-detail"><summary>{row.runId} · {stamp(row.at)}</summary><dl>{row.request && <><div><dt>请求</dt><dd>{row.request.id} · 第 {row.request.attempt} 次连接</dd></div><div><dt>审核依据</dt><dd>{row.request.plan.basis}</dd></div><div><dt>原件版本</dt><dd>{row.request.plan.files.map(file => <span key={file}>{file}</span>)}</dd></div>{row.request.settlement && <div><dt>用量核对</dt><dd>{stamp(row.request.settlement.at)}</dd></div>}</>}</dl><p>{row.detail}</p></details></TableCell>
      <TableCell><strong className={row.attention ? 'gf-usage-pending' : ''}>{row.status}</strong><span>{row.actual !== null ? `结算 ${row.actual} 点` : row.held === null ? '金额尚不明确' : row.held > 0 ? `预留 ${row.held} 点` : '预留已释放'}</span></TableCell>
      <TableCell><div className="gf-usage-actions"><Button variant="ghost" onClick={() => onOpen(row.caseId, row.owner)}>返回事项<ArrowRight size={15}/></Button>{row.request && row.request.phase !== 'absent' && <Button variant="outline" disabled={Boolean(busy) || unavailable} onClick={() => reconcile(row)}>{busy === row.key ? <LoaderCircle size={15} className="animate-spin"/> : <RefreshCw size={15}/>}核对{row.request.phase === 'accepted' ? '结算' : '受理'}</Button>}</div></TableCell>
    </TableRow>)}</TableBody></Table> : <p className="gf-usage-empty">{query || filter !== 'all' ? '没有符合筛选条件的检查。' : '尚无检查记录。完成资料登记并开始检查后，用量会出现在这里。'}</p>}</section>
  }
  return <div className={`gf-usage gf-usage--${layout}`}>
    <header className="gf-task-heading"><div><div className="gf-eyebrow">审核团队</div><h1>用量</h1></div>{from ? <Button variant="outline" onClick={() => onOpen(from, fromOwner)}><ArrowLeft size={16}/>返回原事项</Button> : <Button variant="outline" onClick={refresh}><RefreshCw size={16}/>重新读取</Button>}</header>
    {summary}{warning}
    <div className="gf-usage-toolbar"><Input aria-label="搜索事项或检查号" placeholder="搜索事项或检查号" value={query} onChange={event => setQuery(event.target.value)}/><NativeSelect aria-label="筛选用量状态" value={filter} onChange={event => setFilter(event.target.value)}><NativeSelectOption value="all">全部记录</NativeSelectOption><NativeSelectOption value="attention">待核对与待结算</NativeSelectOption><NativeSelectOption value="settled">已结算</NativeSelectOption></NativeSelect>{(query || filter !== 'all') && <Button variant="ghost" onClick={() => {setQuery(''); setFilter('all')}}>清除</Button>}</div>
    <p className="gf-usage-feedback" role="status">{message}</p>
    {layout === 2 && filter === 'all' ? <>{records(shown.filter(row => row.attention), '待核对与待结算')}<details className="gf-usage-closed"><summary>已确认记录 · {shown.filter(row => !row.attention).length}</summary>{records(shown.filter(row => !row.attention), '已确认')}</details></> : records(shown, filter === 'settled' ? '已结算' : filter === 'attention' ? '待核对与待结算' : '检查明细')}
    <details className="gf-usage-explain"><summary>示例额度如何计算</summary><p>此浏览器共用 2000 点模拟额度，没有真实价格或扣费。一个检查号只计一笔：未确定受理与未结算时保留预留；确认结算后用实际点数替换。核对只读取原检查，不重新开始审核。失败不自动退款。</p><p>资料只在本机；不提供跨设备、跨窗口原子扣费或团队余额保证。缺少用量的历史检查需后续接入，不能从完成状态推算价格。</p></details>
    {import.meta.env.DEV && <><details className="gf-usage-simulator"><summary>演示用量读取情形</summary><label>下一次核对结果<NativeSelect value={outcome} onChange={event => setOutcome(event.target.value as UsageOutcome)}><NativeSelectOption value="normal">正常返回（结束后结算）</NativeSelectOption><NativeSelectOption value="pending">回执尚未就绪</NativeSelectOption><NativeSelectOption value="unknown">回执未知</NativeSelectOption><NativeSelectOption value="failed">读取失败</NativeSelectOption><NativeSelectOption value="zero">明确结算为 0 点</NativeSelectOption></NativeSelect></label><Button variant="outline" onClick={() => {if (readFailure) setSnapshot(readUsage()); setReadFailure(!readFailure); setMessage('')}}>{readFailure ? '恢复记录读取' : '模拟记录读取失败'}</Button></details><aside className="gf-switcher" aria-label="用量布局切换"><button aria-label="上一种用量布局" onClick={() => choose((layout + 2) % 3)}><ArrowLeft size={16}/></button><span>布局方案　{layout + 1} / 3 · {layouts[layout]}</span><button aria-label="下一种用量布局" onClick={() => choose((layout + 1) % 3)}><ArrowRight size={16}/></button></aside></>}
  </div>
}
