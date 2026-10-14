// Stitch: projects/13261964020862199279/screens/ac0b64567e274960ab6ac83f4460ab93.
// Three entrance layouts share one mock session on intake.html (?accessLayout=...).
// This is an interaction fixture, not authentication or an authorization boundary.
import {createContext, useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowLeft, ArrowRight, ChevronDown, ClipboardCheck, FolderClock, Gauge, LoaderCircle, LogOut, RotateCcw, UserRound} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from '../src/components/ui/dropdown-menu'
import './access.css'
import {usageHref} from './usage-state'
import {LocalRecordsDialog, LocalSaveNotice} from './local-records'
import type {RecordValidators} from './local-records-state'

type Mode = 'entry' | 'signed-in' | 'expired' | 'signed-out' | 'connecting' | 'denied' | 'offline' | 'unknown' | 'cancelled'
type Outcome = 'success' | 'denied' | 'offline' | 'unknown' | 'waiting'
type Layout = 'center' | 'split' | 'resume'
const storageKey = 'ppap-greenfield:mock-access:v1'
const modes: Mode[] = ['entry', 'signed-in', 'expired', 'signed-out', 'connecting', 'denied', 'offline', 'unknown', 'cancelled']
const layouts: {id: Layout; label: string}[] = [{id: 'center', label: '专注进入'}, {id: 'split', label: '工作与入口'}, {id: 'resume', label: '继续工作'}]
const AccessContext = createContext<{lock: (reason: 'expired' | 'signed-out') => void; records: () => void} | null>(null)
const emptyValidators: RecordValidators = {}

function readMode(): Mode {
  try {
    const saved = localStorage.getItem(storageKey)
    return saved === 'connecting' ? 'unknown' : modes.includes(saved as Mode) ? saved as Mode : 'entry'
  } catch { return 'entry' }
}

export function PrototypeAccount({compact = false, className = ''}: {compact?: boolean; className?: string}) {
  const access = useContext(AccessContext)
  if (!access) return compact ? null : <div className="gf-account-static"><UserRound size={18}/><span>审核团队 · 独立演示</span></div>
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button variant="ghost" className={`gf-account-trigger ${compact ? 'gf-account-trigger--compact' : ''} ${className}`} aria-label="林悦，账号菜单">
      <span className="gf-account-avatar" aria-hidden="true">林</span>{!compact && <><span className="gf-account-name"><strong>林悦</strong><span>审核团队</span></span><ChevronDown size={16}/></>}
    </Button></DropdownMenuTrigger>
    <DropdownMenuContent className="gf-account-menu" align="end" sideOffset={8}>
      <DropdownMenuLabel><strong>林悦</strong><span>北辰汽车 · 质量审核团队</span><small>模拟身份，不代表正式批准权限</small></DropdownMenuLabel>
      <DropdownMenuSeparator/>
      <DropdownMenuItem asChild><a href={usageHref()}><Gauge/>用量</a></DropdownMenuItem>
      <DropdownMenuItem onSelect={access.records}><FolderClock/>本机记录</DropdownMenuItem>
      <DropdownMenuItem onSelect={() => access.lock('expired')}><RotateCcw/>演示会话失效</DropdownMenuItem>
      <DropdownMenuItem onSelect={() => access.lock('signed-out')}><LogOut/>退出登录</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
}

export function PrototypeAccess({children, recordValidators = emptyValidators}: {children: ReactNode; recordValidators?: RecordValidators}) {
  const [mode, setMode] = useState<Mode>(readMode)
  const [outcome, setOutcome] = useState<Outcome>('success')
  const [notice, setNotice] = useState('')
  const [saveError, setSaveError] = useState('')
  const [recordsOpen, setRecordsOpen] = useState(() => new URLSearchParams(location.search).get('records') === 'open')
  function showRecords(open: boolean) {setRecordsOpen(open); const url = new URL(location.href); if (open) url.searchParams.set('records', 'open'); else url.searchParams.delete('records'); history.replaceState(null, '', url)}
  const [layout, setLayout] = useState<Layout>(() => {
    const requested = new URLSearchParams(window.location.search).get('accessLayout')
    return layouts.find(item => item.id === requested)?.id ?? 'center'
  })
  const heading = useRef<HTMLHeadingElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const recovering = new URLSearchParams(window.location.search).has('case')
  const pending = mode === 'connecting'
  function stopTimer() { if (timer.current !== null) clearTimeout(timer.current); timer.current = null }
  function transition(next: Mode) {
    setMode(next)
    try { localStorage.setItem(storageKey, next); setSaveError('') }
    catch { setSaveError('登录状态未能保存在本机；刷新后可能需要重新进入。') }
  }
  function lock(reason: 'expired' | 'signed-out') {
    stopTimer()
    setOutcome('success')
    setNotice('')
    transition(reason)
  }
  function enter() {
    if (pending) return
    const checking = mode === 'unknown'
    setNotice('')
    transition('connecting')
    if (outcome === 'waiting' && !checking) return
    timer.current = setTimeout(() => {
      timer.current = null
      if (checking) {
        transition('entry')
        setNotice('未找到有效的模拟登录状态。请重新进入。')
      } else transition(outcome === 'success' ? 'signed-in' : outcome as Mode)
    }, 900)
  }
  function cancel() { stopTimer(); transition('cancelled') }
  function chooseLayout(next: Layout) {
    setLayout(next)
    const url = new URL(window.location.href)
    url.searchParams.set('accessLayout', next)
    window.history.replaceState(null, '', url)
  }
  useEffect(() => () => { if (timer.current !== null) clearTimeout(timer.current) }, [])
  useEffect(() => {
    if (mode === 'signed-in') {
      const title = document.querySelector<HTMLElement>('main h1')
      if (title) {title.tabIndex = -1; title.focus({preventScroll: true})}
    } else if (mode !== 'connecting') heading.current?.focus()
  }, [mode])
  useEffect(() => {
    if (mode === 'signed-in') return
    const cycle = (event: KeyboardEvent) => {
      if (!import.meta.env.DEV || event.altKey || event.metaKey || event.ctrlKey || event.shiftKey || event.target instanceof Element && event.target.closest('input, textarea, select, button, a, summary, [contenteditable]')) return
      const delta = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0
      if (delta) { event.preventDefault(); chooseLayout(layouts[(layouts.findIndex(item => item.id === layout) + delta + layouts.length) % layouts.length].id) }
    }
    window.addEventListener('keydown', cycle)
    return () => window.removeEventListener('keydown', cycle)
  }, [mode, layout])

  if (mode === 'signed-in') return <AccessContext.Provider value={{lock, records: () => showRecords(true)}}>
    {saveError && <p className="gf-access-save-error" role="alert">{saveError}</p>}<LocalSaveNotice validators={recordValidators} onOpen={() => showRecords(true)}/>{children}<LocalRecordsDialog open={recordsOpen} onOpenChange={showRecords} validators={recordValidators}/>
  </AccessContext.Provider>

  const messages: Record<Exclude<Mode, 'signed-in'>, {title: string; detail: string; action: string}> = {
    entry: {title: recovering ? '继续你的审核工作' : '进入审核工作台', detail: recovering ? '使用企业账号进入后，继续当前事项。' : '上传收到的资料，或继续尚未完成的审核。', action: '使用企业账号继续'},
    expired: {title: '请重新登录', detail: '会话已失效。重新进入后，恢复此浏览器保存的工作位置和记录。', action: '重新登录并继续'},
    'signed-out': {title: '已退出登录', detail: '此浏览器中已保存的工作记录仍保留。', action: '使用企业账号继续'},
    connecting: {title: '正在进入工作台', detail: '正在确认企业账号和审核团队。', action: '正在连接…'},
    denied: {title: '此账号未加入审核团队', detail: '请使用已获授权的企业账号，或联系企业管理员确认团队权限。', action: '返回登录入口'},
    offline: {title: '暂时无法连接登录服务', detail: '尚未确认登录状态。请检查网络连接，然后重试。', action: '重试连接'},
    unknown: {title: '登录结果尚未确认', detail: '先核对登录状态，再决定是否重新登录。当前没有开始新的审核。', action: '核对登录状态'},
    cancelled: {title: '已取消本次登录', detail: '没有进入工作台。你可以稍后再继续。', action: '返回登录入口'},
  }
  const message = messages[mode]
  const proceed = () => {
    if (mode === 'denied' || mode === 'cancelled') {setOutcome('success'); transition('entry')}
    else enter()
  }
  const intro = <div className="gf-access-intro"><p className="gf-eyebrow">质量审核团队</p><h2>让每个判断，<br/>都有依据。</h2><p>从收到的资料，到明确的审核决定。</p></div>
  const panel = <section className="gf-access-panel" aria-labelledby="access-title" aria-busy={pending}>
    <div className="gf-access-panel-head"><h1 id="access-title" ref={heading} tabIndex={-1}>{message.title}</h1><p>{message.detail}</p></div>
    <div className="gf-access-team"><span className="gf-account-avatar" aria-hidden="true">林</span><div><strong>北辰汽车</strong><span>林悦 · 质量审核团队</span></div></div>
    <Button className="gf-primary gf-access-primary" disabled={pending} onClick={proceed}>{pending ? <LoaderCircle className="animate-spin"/> : null}{message.action}{!pending && <ArrowRight size={16}/>}</Button>
    {pending && <Button variant="ghost" className="gf-access-cancel" onClick={cancel}>取消登录</Button>}
    <p className="gf-access-feedback" role="status">{pending ? '正在等待登录结果。' : notice}</p>
    {saveError && <p className="gf-access-save-error" role="alert">{saveError}</p>}
    <details className="gf-access-demo"><summary>交互原型 · 模拟身份</summary><p>未连接真实身份服务。林悦和企业身份均为示例；登录状态与事项记录仅存此浏览器，不提供真实权限隔离或跨设备恢复。请勿输入真实账号或敏感资料。</p><label>下次登录的模拟结果<select value={outcome} disabled={pending} onChange={event => setOutcome(event.target.value as Outcome)}><option value="success">成功进入</option><option value="denied">账号无团队权限</option><option value="offline">连接失败</option><option value="unknown">结果未知</option><option value="waiting">持续等待，可取消或刷新</option></select></label></details>
  </section>
  return <div className={`gf-app gf-access gf-access--${layout}`}>
    <header className="gf-access-brand"><ClipboardCheck aria-hidden="true"/><strong>PPAP</strong><span>审核工作台</span></header>
    <main className="gf-access-main">
      {layout === 'center' && <div className="gf-access-centered">{panel}</div>}
      {layout === 'split' && <div className="gf-access-split">{intro}{panel}</div>}
      {layout === 'resume' && <div className="gf-access-resume"><div className="gf-access-resume-heading"><span className="gf-eyebrow">开始与继续</span><h2>回到工作本身。</h2></div><div className="gf-access-resume-body">{panel}<aside><ClipboardCheck size={24} aria-hidden="true"/><h2>进入后继续</h2><p>已有事项保留各自的资料、要求与复核记录。新收到的文件，先上传，再核对。</p><p>新文件到了，不代表问题已经解决。</p></aside></div></div>}
    </main>
    <footer className="gf-access-footer">交互原型 · 无真实账号连接</footer>
    {import.meta.env.DEV && <aside className="gf-switcher" aria-label="入口布局比较"><button aria-label="上一种入口布局" onClick={() => chooseLayout(layouts[(layouts.findIndex(item => item.id === layout) + layouts.length - 1) % layouts.length].id)}><ArrowLeft size={16}/></button><span>{layouts.findIndex(item => item.id === layout) + 1} / 3 · {layouts.find(item => item.id === layout)?.label}</span><button aria-label="下一种入口布局" onClick={() => chooseLayout(layouts[(layouts.findIndex(item => item.id === layout) + 1) % layouts.length].id)}><ArrowRight size={16}/></button></aside>}
  </div>
}
