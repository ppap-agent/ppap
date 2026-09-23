import {useEffect, useRef, useState, type ReactNode} from 'react'
import {ChevronDown, CirclePlus} from 'lucide-react'
import brandIcon from '../assets/brand.svg'
import filesIcon from '../assets/files.svg'
import rulesIcon from '../assets/rules.svg'
import usageIcon from '../assets/usage.svg'

export const navigation = [
  {path: '/', label: '新建审核', icon: null},
  {path: '/audit-files', label: '审核结果', icon: filesIcon},
  {path: '/rules', label: '规则库', icon: rulesIcon},
  {path: '/usage', label: '用量记录', icon: usageIcon},
]

export function navigationTitle(path: string) {
  return navigation.find(link => link.path === path)?.label || 'PPAP'
}

function AccountMenu({user, onLogout, compact = false}: {user: string; onLogout: () => void; compact?: boolean}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function dismiss(event: Event) {
      if (event instanceof KeyboardEvent) {
        if (event.key === 'Escape') setOpen(false)
        return
      }
      const target = event.target
      if (!(target instanceof Node) || !root.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', dismiss)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', dismiss)
    }
  }, [])

  return (
    <div ref={root} className={compact ? 'mobile-account' : 'pc-shell__session'}>
      <button
        type="button"
        className="account-button"
        data-testid={compact ? undefined : 'session-user'}
        aria-label={user + '，账号菜单'}
        title={user}
        aria-expanded={open}
        aria-controls={compact ? 'account-actions-mobile' : 'account-actions'}
        onClick={() => setOpen(value => !value)}
      >
        <span className="avatar" aria-hidden="true">{user[0]}</span>
        {!compact && <span>{user}</span>}
        {!compact && <span aria-hidden="true"><ChevronDown size={16} /></span>}
      </button>
      {open && (
        <div id={compact ? 'account-actions-mobile' : 'account-actions'} className="account-actions">
          <button type="button" onClick={onLogout}>退出登录</button>
        </div>
      )}
    </div>
  )
}

export default function AppShell({
  activePath,
  title,
  user,
  onLogout,
  children,
}: {
  activePath: string
  title: string
  user: string
  onLogout: () => void
  children: ReactNode
}) {
  return (
    <div className="app-frame">
      <header className="mobile-product-bar">
        <a className="pc-brand" href="/" aria-label="PPAP 首页">
          <img src={brandIcon} alt="" width={22} height={22} />
          <strong>PPAP</strong>
        </a>
        <span className="mobile-context">{title}</span>
        {user && <a className="edition-mobile-link" href="/enter-legacy">旧版</a>}
        {user && <AccountMenu user={user} onLogout={onLogout} compact />}
      </header>
      <div className="app-body">
        <a className="skip-link" href="#main-content">跳到主要内容</a>
        <aside className="pc-shell" data-testid="pc-shell">
          <div className="pc-shell__content">
            <div className="pc-shell__brand">
              <a className="pc-brand" href="/" aria-label="PPAP 首页">
                <img src={brandIcon} alt="" width={24} height={24} />
                <strong>PPAP</strong>
              </a>
              <div className="edition-banner" data-testid="edition-banner">
                <span>当前：新版</span>
                {user ? <a href="/enter-legacy">切换到旧版</a> : null}
              </div>
            </div>
            <nav id="main-navigation" className="pc-shell__nav" aria-label="主导航">
              {navigation.map(link => (
                <a
                  key={link.path}
                  href={link.path}
                  aria-label={link.label}
                  title={link.label}
                  aria-current={activePath === link.path ? 'page' : undefined}
                  className={(activePath === link.path ? 'is-active' : '') + (link.path === '/usage' ? ' nav-secondary' : '')}
                  data-testid={link.path === '/rules' ? 'nav-rules' : link.path === '/usage' ? 'nav-usage' : link.path === '/audit-files' ? 'nav-audit-files' : 'nav-home'}
                >
                  {link.icon ? <img src={link.icon} alt="" width={20} height={20} /> : <CirclePlus size={20} aria-hidden="true" />}
                  <span>{link.label}</span>
                </a>
              ))}
            </nav>
            {user && <AccountMenu user={user} onLogout={onLogout} />}
          </div>
        </aside>
        <main id="main-content" className="pc-main" tabIndex={-1}>
          <h1 className="sr-only">{title}</h1>
          {children}
        </main>
      </div>
    </div>
  )
}
