// Stitch: projects/13261964020862199279/screens/5a80a77a4f524928b808862728d5677a (login)
// and screens/9a6cb6189e92450da32d95a31820b41c (connection failure).
import {createContext, useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowRight, ChevronDown, ClipboardCheck, FolderClock, Gauge, LogOut} from 'lucide-react'
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

export function AccountMenu({compact = false, className}: {compact?: boolean; className?: string}) {
  const access = useAccess()
  const name = access?.principal?.display_name || (access?.preview ? '林悦 · 演示' : '审核账号')
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button variant="ghost" className={className} size={compact ? 'profile-icon' : 'profile'} aria-label={`${name}，账号菜单`}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm text-foreground">{name.slice(0, 1)}</span>
      {!compact && <><span className={"min-w-0 flex-1 truncate text-left"}>{name}</span><ChevronDown/></>}
    </Button></DropdownMenuTrigger>
    <DropdownMenuContent align="end"><DropdownMenuLabel>{name}</DropdownMenuLabel><DropdownMenuSeparator/>
      <DropdownMenuItem asChild><a href="/?view=usage"><Gauge/>用量记录</a></DropdownMenuItem>
      <DropdownMenuItem onSelect={access?.records}><FolderClock/>本机草稿</DropdownMenuItem>
      <DropdownMenuItem onSelect={access?.logout}><LogOut/>退出登录</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
}

export function ProductAccess({children, recordValidators = emptyValidators}: {children: ReactNode; recordValidators?: RecordValidators}) {
  const preview = isPreview()
  const [principal, setPrincipal] = useState<PrincipalPublic | null>(null)
  const [ready, setReady] = useState(preview), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [canSignIn, setCanSignIn] = useState(false)
  const [records, setRecords] = useState(false), epoch = useRef(0)
  useEffect(() => {
    if (preview) return
    const current = ++epoch.current
    let active = true
    setReady(false); setCanSignIn(false)
    void (async () => {
      try {
        await connect()
        if (active && epoch.current === current) setCanSignIn(true)
        let user: PrincipalPublic | null = null
        try {user = await request<PrincipalPublic>('/me')}
        catch (cause) {
          if ((cause as {code?: string}).code !== 'unauthenticated') throw cause
          if (isFeishuHost() && feishuAppId && /Lark|Feishu/i.test(navigator.userAgent)) {
            try {
              const code = await requestHostCode(feishuAppId)
              user = await request<PrincipalPublic, HostLoginRequest>('/login/feishu/host', 'POST', {code})
            } catch { /* The browser OAuth entry remains available. */ }
          }
        }
        if (active && epoch.current === current) {setPrincipal(user); setError(''); setReady(true)}
      } catch (cause) {if (active) {setError(cause instanceof TypeError ? '暂时无法连接审核服务，请重试。' : cause instanceof Error ? cause.message : '无法连接审核服务'); setReady(true)}}
    })()
    return () => {active = false}
  }, [preview, revision])
  useEffect(() => {
    const expired = () => {epoch.current++; setPrincipal(null); setReady(true)}
    window.addEventListener('ppap-session-expired', expired)
    return () => window.removeEventListener('ppap-session-expired', expired)
  }, [])
  async function logout() {
    if (preview) {location.assign('/'); return}
    try {await request('/logout', 'POST'); epoch.current++; setPrincipal(null)}
    catch (cause) {setError(cause instanceof Error ? cause.message : '退出未完成')}
  }
  if (preview || ready && principal) {
    setDraftOwner(principal ? `${principal.tenant_id}:${principal.user_id}` : null)
    return <AccessContext.Provider value={{principal, preview, logout: () => void logout(), records: () => setRecords(true)}}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}{children}
    <LocalRecordsDialog open={records} onOpenChange={setRecords} validators={recordValidators}/>
  </AccessContext.Provider>
  }
  return <div className={"flex min-h-dvh flex-col bg-background text-foreground"}>
    <header className={"flex items-center gap-3 p-6"}><ClipboardCheck/><strong>PPAP</strong></header>
    <main className={"m-auto flex w-full max-w-md flex-col gap-5 px-6 pb-16"}>
      <h1 className={"text-2xl font-semibold tracking-tight"}>登录审核工作台</h1>
      {!ready ? <p role="status">正在确认登录状态…</p> : error ? <>
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
        <Button onClick={() => setRevision(value => value + 1)}>{canSignIn ? '重新读取登录状态' : '重试连接'}</Button>
        {canSignIn && <Button variant="outline" asChild><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>使用飞书登录<ArrowRight/></a></Button>}
      </> : <Button asChild><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>使用飞书登录<ArrowRight/></a></Button>}
    </main>
  </div>
}
