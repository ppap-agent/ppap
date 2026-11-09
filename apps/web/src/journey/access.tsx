// Stitch: projects/13261964020862199279/screens/5a80a77a4f524928b808862728d5677a (login)
// and screens/9a6cb6189e92450da32d95a31820b41c (connection failure).
import {createContext, useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowRight, ChevronDown, ClipboardCheck, FolderClock, Gauge, Loader2, LogOut} from 'lucide-react'
import {apiUrl, connect, feishuAppId, request, type HostLoginRequest, type PrincipalPublic} from '../api'
import {isFeishuHost, requestHostCode} from '../hostLogin'
import {Button} from '../components/ui/button'
import {Alert, AlertDescription} from '../components/ui/alert'
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from '../components/ui/dropdown-menu'
import {LocalRecordsDialog} from './local-records'
import type {RecordValidators} from './local-records-state'
import {setDraftOwner} from './saved-draft'

export const isPreview = () => location.pathname.startsWith('/preview/') || location.pathname === '/__preview'
const AccessContext = createContext<{principal: PrincipalPublic | null; preview: boolean; logout: () => void; records: () => void} | null>(null)
export const useAccess = () => useContext(AccessContext)
const emptyValidators: RecordValidators = {}
const loginErrors: Record<string, string> = {
  cancelled: '已取消飞书授权。需要登录后才能继续审核。',
  tenant_not_allowed: '当前企业尚未开通此审核服务，请切换飞书账号或联系管理员。',
}

export function AccountMenu({compact = false, className, onUsage, showLocalRecords = true}: {compact?: boolean; className?: string; onUsage?: () => void; showLocalRecords?: boolean}) {
  const access = useAccess()
  const name = access?.principal?.display_name || (access?.preview ? '林悦 · 演示' : '审核账号')
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button variant="ghost" className={className} size={compact ? 'profile-icon' : 'profile'} aria-label={`${name}，账号菜单`}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm text-foreground">{name.slice(0, 1)}</span>
      {!compact && <><span className={"min-w-0 flex-1 truncate text-left"}>{name}</span><ChevronDown/></>}
    </Button></DropdownMenuTrigger>
    <DropdownMenuContent align="end"><DropdownMenuLabel>{name}</DropdownMenuLabel><DropdownMenuSeparator/>
      {onUsage ? <DropdownMenuItem onSelect={onUsage}><Gauge/>用量记录</DropdownMenuItem> : <DropdownMenuItem asChild><a href="/?view=usage"><Gauge/>用量记录</a></DropdownMenuItem>}
      {showLocalRecords && <DropdownMenuItem onSelect={access?.records}><FolderClock/>本机草稿</DropdownMenuItem>}
      <DropdownMenuItem onSelect={access?.logout}><LogOut/>退出登录</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
}

export function ProductAccess({children, recordValidators = emptyValidators, recordsReturnTo}: {children: ReactNode; recordValidators?: RecordValidators; recordsReturnTo?: string}) {
  const preview = isPreview()
  const [loginError] = useState(() => {
    const reason = new URL(location.href).searchParams.get('auth_error')
    return reason ? Object.hasOwn(loginErrors, reason) ? loginErrors[reason] : '飞书登录未完成，请重新登录。' : ''
  })
  const [principal, setPrincipal] = useState<PrincipalPublic | null>(null)
  const [ready, setReady] = useState(preview), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [canSignIn, setCanSignIn] = useState(false)
  const [records, setRecords] = useState(false), epoch = useRef(0)
  useEffect(() => {
    if (preview) return
    const url = new URL(location.href)
    if (url.searchParams.has('auth_error')) {url.searchParams.delete('auth_error'); history.replaceState(history.state, '', url)}
    const current = ++epoch.current
    let active = true
    setReady(false); setCanSignIn(false)
    void (async () => {
      try {
        const config = await connect()
        if (!active || epoch.current !== current) return
        setCanSignIn(config.login === 'feishu')
        let user: PrincipalPublic | null = null
        try {user = await request<PrincipalPublic>('/me')}
        catch (cause) {
          if (!active || epoch.current !== current) return
          if ((cause as {code?: string}).code !== 'unauthenticated') throw cause
          if (!loginError && config.login === 'feishu' && isFeishuHost() && feishuAppId && /Lark|Feishu/i.test(navigator.userAgent)) {
            try {
              const code = await requestHostCode(feishuAppId)
              if (!active || epoch.current !== current) return
              user = await request<PrincipalPublic, HostLoginRequest>('/login/feishu/host', 'POST', {code})
            } catch { /* The browser OAuth entry remains available. */ }
          }
        }
        if (active && epoch.current === current) {setPrincipal(user); setError(user ? '' : loginError); setReady(true)}
      } catch (cause) {if (active && epoch.current === current) {setError(cause instanceof TypeError ? '暂时无法连接审核服务，请重试。' : cause instanceof Error ? cause.message : '无法连接审核服务'); setReady(true)}}
    })()
    return () => {active = false}
  }, [preview, revision, loginError])
  useEffect(() => {
    const expired = () => {if (principal) {epoch.current++; setPrincipal(null); setReady(true)}}
    window.addEventListener('ppap-session-expired', expired)
    return () => window.removeEventListener('ppap-session-expired', expired)
  }, [principal])
  async function logout() {
    if (preview) {location.assign('/'); return}
    try {await request('/logout', 'POST'); epoch.current++; setPrincipal(null)}
    catch (cause) {setError(cause instanceof Error ? cause.message : '退出未完成')}
  }
  if (preview || ready && principal) {
    setDraftOwner(principal ? `${principal.tenant_id}:${principal.user_id}` : null)
    return <AccessContext.Provider value={{principal, preview, logout: () => void logout(), records: () => setRecords(true)}}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}{children}
    <LocalRecordsDialog open={records} onOpenChange={setRecords} validators={recordValidators} returnTo={recordsReturnTo}/>
  </AccessContext.Provider>
  }
  return <div className={"flex min-h-dvh flex-col bg-background text-foreground"}>
    <header className={"flex items-center gap-3 p-6"}><ClipboardCheck/><strong>PPAP</strong></header>
    <main className={"m-auto flex w-full max-w-md flex-col gap-5 px-6 pb-16"}>
      {/* A signed-in user should not see a sign-in screen while the session is still being read. */}
      {!ready ? <p role="status" className="flex items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none"/>正在确认登录状态…</p> : <div className="space-y-2"><h1 className={"text-2xl font-semibold tracking-tight"}>登录审核工作台</h1><p className="text-sm text-muted-foreground">按客户规则逐项审核 PPAP 文件，结果保留原件证据与报告。登录后回到当前页面。</p></div>}
      {!ready ? null : error ? <>
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
        <Button onClick={() => setRevision(value => value + 1)}>{canSignIn ? '重试' : '重试'}</Button>
        {canSignIn && <Button variant="outline" asChild><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>使用飞书登录<ArrowRight/></a></Button>}
      </> : canSignIn ? <Button asChild><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>使用飞书登录<ArrowRight/></a></Button> : <>
        <Alert><AlertDescription>当前无法使用飞书登录，请稍后重试。</AlertDescription></Alert>
        <Button onClick={() => setRevision(value => value + 1)}>重试</Button>
      </>}
    </main>
  </div>
}
