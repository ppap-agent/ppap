import {useEffect, useState, type ReactNode} from 'react'
import {apiUrl, connect, feishuAppId, profile, request} from './api'
import {isFeishuHost, requestHostCode} from './hostLogin'
import Workbench from './Workbench'
import HistoryView from './HistoryView'
import RulesView from './RulesView'
import UsageView from './UsageView'
import {isTenantDenied, parseProductRoute, productHref, shellPath} from './navigation/route'
import AppShell from './ui/AppShell'
import {PageLayout} from './ui/PageLayout'
import {Alert, AlertDescription} from '@/components/ui/alert'
import {Button} from '@/components/ui/button'

const route = parseProductRoute(location.pathname, location.search)

export default function App() {
  const [reviewing, setReviewing] = useState(route.page === 'audit-round')
  const activePath = shellPath(route, reviewing)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [user, setUser] = useState('')
  const [loginUrl, setLoginUrl] = useState('#')
  const [development, setDevelopment] = useState(false)
  const tenantDenied = isTenantDenied(location.search)
  const devPage = route.page === 'dev'

  useEffect(() => {
    if (!tenantDenied) return
    document.cookie = 'ppap_edition=; Path=/; Max-Age=0; SameSite=Lax'
    location.replace(productHref({page: 'audit-new'}))
  }, [tenantDenied])

  useEffect(() => {
    void (async () => {
      try {
        await connect()
        setDevelopment(profile === 'isolated-dev')
        setLoginUrl(apiUrl + '/login/feishu?return_to=' + encodeURIComponent(location.href))
        try {
          const me = await request<{display_name: string}>('/me')
          setUser(me.display_name || '已登录用户')
        } catch {
          if (isFeishuHost() && feishuAppId) {
            try {
              const code = await requestHostCode(feishuAppId)
              const me = await request<{display_name: string}>('/login/feishu/host', 'POST', {code})
              setUser(me.display_name || '已登录用户')
            } catch { /* Browser OAuth remains available. */ }
          }
        }
        setReady(true)
      } catch { setError('暂时无法连接服务，请稍后刷新。') }
    })()
  }, [])

  async function logout() {
    try {
      await request('/logout', 'POST')
      for (const key of Object.keys(sessionStorage)) if (key.startsWith('ppap-view:')) sessionStorage.removeItem(key)
      location.reload()
    } catch { setError('退出失败，请稍后重试。') }
  }

  async function devLogin() {
    try {
      await request('/isolated-dev/sessions', 'POST', {subject: 'alice'})
      location.href = productHref({page: 'audit-new'})
    } catch { setError('无法创建本地测试会话。') }
  }

  const authenticated = ready && !devPage && !!user
  const page = (content: ReactNode) => <PageLayout>{content}</PageLayout>
  const message = (content: ReactNode, destructive = false) => <Alert variant={destructive ? 'destructive' : 'default'}><AlertDescription>{content}</AlertDescription></Alert>
  return (
    <AppShell activePath={activePath} user={user} development={development} onLogout={() => void logout()}>
      {authenticated ? (
        <>
          {error && page(message(error, true))}
          {route.page === 'audit-history' ? <HistoryView />
            : route.page === 'rule-library' || route.page === 'rule-detail' || route.page === 'rule-import' ? <RulesView />
              : route.page === 'usage' ? <UsageView />
                : <Workbench onReviewing={setReviewing} />}
        </>
      ) : (
        page(<>
          {tenantDenied && message('无法使用此入口。', true)}
          {error && message(error, true)}
          {!ready && !error && message('正在连接…')}
          {ready && devPage && (
            development
              ? <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><h1 className="text-2xl font-semibold tracking-tight">本地测试身份</h1><Button type="button" data-testid="isolated-dev-login" onClick={() => void devLogin()}>使用隔离测试身份</Button></header>
              : <h1 className="text-2xl font-semibold tracking-tight">此入口不可用</h1>
          )}
          {ready && !devPage && !user && (
            <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div><h1 className="text-2xl font-semibold tracking-tight">登录后开始审核</h1><p className="mt-1 max-w-prose text-sm text-muted-foreground">使用飞书账号访问你的资料与审核结果。</p></div><Button asChild><a href={loginUrl}>登录飞书</a></Button></header>
          )}
        </>)
      )}
    </AppShell>
  )
}
