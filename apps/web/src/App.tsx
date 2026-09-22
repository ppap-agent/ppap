import {useEffect, useMemo, useRef, useState} from 'react'
import {apiUrl, connect, feishuAppId, profile, request} from './api'
import {isFeishuHost, requestHostCode} from './hostLogin'
import Workbench from './Workbench'
import HistoryView from './HistoryView'
import RulesView from './RulesView'
import UsageView from './UsageView'
import {CirclePlus, ChevronDown} from 'lucide-react'
import filesIcon from './assets/files.svg'
import rulesIcon from './assets/rules.svg'
import usageIcon from './assets/usage.svg'

const page = location.pathname.replace(/\/$/, '') || '/'
const navigation = [
  {path: '/', label: '新建审核', icon: null},
  {path: '/audit-files', label: '审核结果', icon: filesIcon},
  {path: '/rules', label: '规则库', icon: rulesIcon},
  {path: '/usage', label: '用量记录', icon: usageIcon},
]

export default function App() {
  const [reviewing, setReviewing] = useState(page === '/' && new URLSearchParams(location.search).has('round'))
  const activePath = reviewing ? '/audit-files' : page
  const title = useMemo(() => navigation.find(link => link.path === activePath)?.label || 'PPAP', [activePath])
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [user, setUser] = useState('')
  const [accountOpen, setAccountOpen] = useState(false)
  const [loginUrl, setLoginUrl] = useState('#')
  const [development, setDevelopment] = useState(false)
  const tenantDenied = new URLSearchParams(location.search).get('error') === 'tenant_not_allowed'
  const shell = useRef<HTMLElement>(null)
  const devPage = page === '/dev'

  useEffect(() => {
    function dismissMenus(event: Event) {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return
      if (event instanceof KeyboardEvent || !shell.current?.contains(event.target as Node)) {
        setAccountOpen(false)
      }
    }
    document.addEventListener('pointerdown', dismissMenus)
    document.addEventListener('keydown', dismissMenus)
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
    return () => {
      document.removeEventListener('pointerdown', dismissMenus)
      document.removeEventListener('keydown', dismissMenus)
    }
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
    <div className="app-frame">
      <div className="edition-banner" data-testid="edition-banner" role="status">
        <strong>当前：新版</strong>
        <span>预览环境，不扣点。仅海岸线团队可登录。</span>
        <a href="/enter-legacy">切换到旧版</a>
      </div>
      <div className="app-body">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside ref={shell} className="pc-shell" data-testid="pc-shell">
        <div className="mobile-bar"><span>{title}</span></div>
        <div className="pc-shell__content">
          <nav id="main-navigation" className="pc-shell__nav" aria-label="主导航">
            {navigation.map(link => (
              <a key={link.path} href={link.path} aria-label={link.label} title={link.label} aria-current={activePath === link.path ? 'page' : undefined} className={(activePath === link.path ? 'is-active' : '') + (link.path === '/usage' ? ' nav-secondary' : '')} data-testid={link.path === '/rules' ? 'nav-rules' : link.path === '/usage' ? 'nav-usage' : link.path === '/audit-files' ? 'nav-audit-files' : 'nav-home'}>
                {link.icon ? <img src={link.icon} alt="" width={20} height={20} /> : <CirclePlus size={20} aria-hidden="true" />}
                <span>{link.label}</span>
              </a>
            ))}
          </nav>
          {user && (
            <div className="pc-shell__session">
              <button type="button" className="account-button" data-testid="session-user" aria-label={user + '，账号菜单'} title={user} aria-expanded={accountOpen} aria-controls="account-actions" onClick={() => setAccountOpen(open => !open)}>
                <span className="avatar" aria-hidden="true">{user[0]}</span>
                <span>{user}</span>
                <span aria-hidden="true"><ChevronDown size={16} /></span>
              </button>
              {accountOpen && <div id="account-actions" className="account-actions"><button type="button" onClick={() => void logout()}>退出登录</button></div>}
            </div>
          )}
        </div>
      </aside>
      <main id="main-content" className="pc-main" tabIndex={-1}>
        <h1 className="sr-only">{title}</h1>
        {tenantDenied && <p className="error" role="alert">当前仅海岸线团队可使用新版。请切换到旧版工作台。</p>}
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
      </main>
      </div>
    </div>
  )
}
