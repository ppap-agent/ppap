import {useEffect, useState} from 'react'
import {apiUrl, connect, feishuAppId, profile, request} from './api'
import {isFeishuHost, requestHostCode} from './hostLogin'
import Workbench from './Workbench'
import HistoryView from './HistoryView'
import RulesView from './RulesView'
import UsageView from './UsageView'
import {isTenantDenied, parseProductRoute, productHref, shellPath} from './navigation/route'
import AppShell from './ui/AppShell'
import {Button, Callout, Link, Page, PageHeader} from './ui'

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
  return (
    <AppShell activePath={activePath} user={user} onLogout={() => void logout()}>
      {authenticated ? (
        <>
          {error && <Page><Callout tone="error">{error}</Callout></Page>}
          {route.page === 'audit-history' ? <HistoryView />
            : route.page === 'rule-library' || route.page === 'rule-detail' || route.page === 'rule-import' ? <RulesView />
              : route.page === 'usage' ? <UsageView />
                : <Workbench onReviewing={setReviewing} />}
        </>
      ) : (
        <Page>
          {tenantDenied && <Callout tone="error">无法使用此入口。</Callout>}
          {error && <Callout tone="error">{error}</Callout>}
          {!ready && !error && <Callout tone="loading">正在连接…</Callout>}
          {ready && devPage && (
            development
              ? <PageHeader title="本地测试身份" actions={<Button type="button" variant="primary" data-testid="isolated-dev-login" onClick={() => void devLogin()}>使用隔离测试身份</Button>} />
              : <PageHeader title="此入口不可用" />
          )}
          {ready && !devPage && !user && (
            <PageHeader title="登录后开始审核" description="使用飞书账号访问你的资料与审核结果。" actions={<Link href={loginUrl} variant="primary">登录飞书</Link>} />
          )}
        </Page>
      )}
    </AppShell>
  )
}
