import {useEffect, useMemo, useState} from 'react'
import {apiUrl, connect, feishuAppId, profile, request} from './api'
import {isFeishuHost, requestHostCode} from './hostLogin'
import Workbench from './Workbench'
import HistoryView from './HistoryView'
import RulesView from './RulesView'
import UsageView from './UsageView'
import AppShell, {navigationTitle} from './ui/AppShell'

const page = location.pathname.replace(/\/$/, '') || '/'

export default function App() {
  const [reviewing, setReviewing] = useState(page === '/' && new URLSearchParams(location.search).has('round'))
  const activePath = reviewing ? '/audit-files' : page
  const title = useMemo(() => navigationTitle(activePath), [activePath])
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [user, setUser] = useState('')
  const [loginUrl, setLoginUrl] = useState('#')
  const [development, setDevelopment] = useState(false)
  const tenantDenied = new URLSearchParams(location.search).get('error') === 'tenant_not_allowed'
  const devPage = page === '/dev'

  useEffect(() => {
    if (!tenantDenied) return
    document.cookie = 'ppap_edition=; Path=/; Max-Age=0; SameSite=Lax'
    location.replace('/')
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
      location.href = '/'
    } catch { setError('无法创建本地测试会话。') }
  }

  return (
    <AppShell activePath={activePath} title={title} user={user} onLogout={() => void logout()}>
      {tenantDenied && <p className="error" role="alert">无法使用此入口。</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!ready && !error && <p className="loading-state" role="status">正在连接…</p>}
      {ready && devPage && (
        <section className="empty-state">
          {development
            ? <><h2>本地测试身份</h2><button type="button" className="pc-primary" data-testid="isolated-dev-login" onClick={() => void devLogin()}>使用隔离测试身份</button></>
            : <p>此入口不可用。</p>}
        </section>
      )}
      {ready && !devPage && !user && (
        <section className="sign-in">
          <h2>登录后开始审核</h2>
          <p className="pc-muted">使用飞书账号访问你的资料与审核结果。</p>
          <a className="button pc-primary" href={loginUrl}>登录飞书</a>
        </section>
      )}
      {ready && !devPage && !!user && (
        page === '/audit-files' ? <HistoryView />
          : page === '/rules' ? <RulesView />
            : page === '/usage' ? <UsageView />
              : <Workbench onReviewing={setReviewing} />
      )}
    </AppShell>
  )
}
