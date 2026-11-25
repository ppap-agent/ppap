// Stitch: projects/13261964020862199279/screens/5a80a77a4f524928b808862728d5677a (login)
// and screens/9a6cb6189e92450da32d95a31820b41c (connection failure).
import {createContext, useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {iconArrowRight, iconChevronDown, iconReceipt, iconRightFromBracket, iconWandMagicSparkles} from '../components/icons'
import {Icon} from '../components/ui/icon'
import {apiUrl, connect, feishuAppId, request, type HostLoginRequest, type PrincipalPublic} from '../api'
import {feishuHostReady, openedInFeishu, requestHostCode} from '../hostLogin'
import {IntentBanner} from './intent'
import {ReceiptNotice} from './receipt-notice'
import {openInPlace, receiptsAddress} from '../audit/routes'
import {ReadSkeleton} from '../components/read-skeleton'
import {Button} from '../components/ui/button'
import {Alert, AlertDescription} from '../components/ui/alert'
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from '../components/ui/dropdown-menu'

const AccessContext = createContext<{principal: PrincipalPublic; logout: () => void} | null>(null)
export const useAccess = () => useContext(AccessContext)
const loginErrors: Record<string, string> = {
  cancelled: '已取消飞书授权。需要登录后才能继续审核。',
  tenant_not_allowed: '当前企业尚未开通此审核服务，请切换飞书账号或联系管理员。',
}
const NOT_RELEASED = loginErrors.tenant_not_allowed

export function AccountMenu({compact = false, className}: {compact?: boolean; className?: string}) {
  const access = useAccess()
  const name = access?.principal.display_name || '审核账号'
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button variant="ghost" className={className} size={compact ? 'profile-icon' : 'profile'} aria-label={`${name}，账号菜单`}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm text-foreground">{name.slice(0, 1)}</span>
      {!compact && <><span className={"min-w-0 flex-1 truncate text-left"}>{name}</span><Icon icon={iconChevronDown}/></>}
    </Button></DropdownMenuTrigger>
    <DropdownMenuContent align="end"><DropdownMenuLabel>{name}</DropdownMenuLabel><DropdownMenuSeparator/>
      <DropdownMenuItem asChild><a href={receiptsAddress()} onClick={openInPlace}><Icon icon={iconReceipt}/>我的回执</a></DropdownMenuItem>
      <DropdownMenuItem onSelect={access?.logout}><Icon icon={iconRightFromBracket}/>退出登录</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
}

export function ProductAccess({children}: {children: ReactNode}) {
  const [loginError] = useState(() => {
    const reason = new URL(location.href).searchParams.get('auth_error')
    return reason ? Object.hasOwn(loginErrors, reason) ? loginErrors[reason] : '飞书登录未完成，请重新登录。' : ''
  })
  const [principal, setPrincipal] = useState<PrincipalPublic | null>(null)
  const [ready, setReady] = useState(false), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [canSignIn, setCanSignIn] = useState(false)
  // Inside the Feishu client the browser OAuth page does not come back: signing in there is the client's own authorization.
  const [inFeishu] = useState(() => openedInFeishu())
  const epoch = useRef(0)
  useEffect(() => {
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
        let hostError = ''
        try {user = await request<PrincipalPublic>('/me')}
        catch (cause) {
          if (!active || epoch.current !== current) return
          if ((cause as {code?: string}).code !== 'unauthenticated') throw cause
          if (!loginError && config.login === 'feishu' && feishuAppId && await feishuHostReady()) {
            try {
              const code = await requestHostCode(feishuAppId)
              if (!active || epoch.current !== current) return
              user = await request<PrincipalPublic, HostLoginRequest>('/login/feishu/host', 'POST', {code})
            } catch (cause) {
              if (!active || epoch.current !== current) return
              // The reason stays on the page: a refusal inside the Feishu client cannot be read anywhere else.
              hostError = (cause as {code?: string}).code === 'tenant_not_allowed' ? NOT_RELEASED : `飞书内登录未完成，请重试。原因：${cause instanceof Error ? cause.message : String(cause)}`
            }
          } else if (!loginError && inFeishu) hostError = '飞书登录组件未加载，请重试。'
        }
        if (active && epoch.current === current) {setPrincipal(user); setError(user ? '' : loginError || hostError); setReady(true)}
      } catch (cause) {if (active && epoch.current === current) {setError(cause instanceof TypeError ? '暂时无法连接审核服务，请重试。' : cause instanceof Error ? cause.message : '无法连接审核服务'); setReady(true)}}
    })()
    return () => {active = false}
  }, [revision, loginError, inFeishu])
  useEffect(() => {
    const expired = () => {if (principal) {epoch.current++; setPrincipal(null); setReady(true)}}
    window.addEventListener('ppap-session-expired', expired)
    return () => window.removeEventListener('ppap-session-expired', expired)
  }, [principal])
  async function logout() {
    try {await request('/logout', 'POST'); epoch.current++; setPrincipal(null)}
    catch (cause) {setError(cause instanceof Error ? cause.message : '退出未完成')}
  }
  if (ready && principal) {
    return <AccessContext.Provider value={{principal, logout: () => void logout()}}>
    {children}<IntentBanner/><ReceiptNotice/>{error && <div className="fixed right-4 bottom-4 z-40 max-w-md"><Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert></div>}
  </AccessContext.Provider>
  }
  return <div className={"flex min-h-dvh flex-col bg-background text-foreground"}>
    <header className="flex items-center gap-2 p-6 text-base font-semibold"><span className="flex size-6.5 shrink-0 items-center justify-center rounded-md bg-linear-135 from-brand-light to-primary text-primary-foreground"><Icon icon={iconWandMagicSparkles} aria-hidden="true" className="size-3.5"/></span>PPAP Audit</header>
    <main className={"m-auto flex w-full max-w-md flex-col gap-5 px-6 pb-16"}>
      {/* A signed-in user should not see a sign-in screen while the session is still being read. */}
      {!ready ? <ReadSkeleton label="正在确认登录状态…" kind="inline"/> : <div className="space-y-2"><h1 className={"text-xl font-semibold tracking-tight"}>登录 PPAP Audit</h1><p className="text-sm text-muted-foreground">按客户规则逐项审核 PPAP 文件，结果保留原件证据与报告。登录后回到当前页面。</p></div>}
      {!ready ? null : error ? <>
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
        {/* After a cancelled or failed sign-in, signing in again is the way forward; reading the service again is secondary. */}
        {canSignIn && <SignIn inFeishu={inFeishu} retry={() => setRevision(value => value + 1)}/>}
        {!inFeishu && <Button variant={canSignIn ? 'outline' : 'default'} onClick={() => setRevision(value => value + 1)}>重试</Button>}
        {/* An enterprise not yet released to this version keeps using the previous one. */}
        {error === NOT_RELEASED && <Button variant="outline" asChild><a href={`${apiUrl}/enter-legacy`}>回到原版 PPAP</a></Button>}
      </> : canSignIn ? <SignIn inFeishu={inFeishu} retry={() => setRevision(value => value + 1)}/> : <>
        <Alert><AlertDescription>当前无法使用飞书登录，请稍后重试。</AlertDescription></Alert>
        <Button onClick={() => setRevision(value => value + 1)}>重试</Button>
      </>}
    </main>
  </div>
}

function SignIn({inFeishu, retry}: {inFeishu: boolean; retry: () => void}) {
  if (inFeishu) return <Button onClick={retry}>使用飞书登录<Icon icon={iconArrowRight}/></Button>
  return <Button asChild><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>使用飞书登录<Icon icon={iconArrowRight}/></a></Button>
}
