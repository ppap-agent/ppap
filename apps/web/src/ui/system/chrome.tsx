import {useEffect, useRef, useState, type ReactNode} from 'react'
import {Button} from './controls'

export function AppFrame({children}: {children: ReactNode}) {
  return <div data-ui="app-frame">{children}</div>
}

export function AppBody({children}: {children: ReactNode}) {
  return <div data-ui="app-body">{children}</div>
}

export function MobileBar({children}: {children: ReactNode}) {
  return <header data-ui="mobile-bar">{children}</header>
}

export function BrandLink({label = 'PPAP 首页', mark}: {label?: string; mark: ReactNode}) {
  return (
    <a data-ui="brand" href="/" aria-label={label}>
      {mark}
      <strong data-ui="brand-name">PPAP</strong>
    </a>
  )
}

export function MobileContext({children}: {children: ReactNode}) {
  return <span data-ui="mobile-context">{children}</span>
}

export function SkipLink() {
  return <a data-ui="skip" href="#main-content">跳到主要内容</a>
}

export function Sidebar({children, testId}: {children: ReactNode; testId?: string}) {
  return (
    <aside data-ui="sidebar" data-testid={testId}>
      <div data-ui="sidebar-content">{children}</div>
    </aside>
  )
}

export function SidebarBrand({children}: {children: ReactNode}) {
  return <div data-ui="sidebar-brand">{children}</div>
}

export function EditionBanner({signedIn}: {signedIn: boolean}) {
  return (
    <div data-ui="edition" data-testid="edition-banner">
      <span>当前：新版</span>
      {signedIn ? <a data-ui="link" href="/enter-legacy">切换到旧版</a> : null}
    </div>
  )
}

export function PrimaryNav({
  activePath,
  items,
}: {
  activePath: string
  items: Array<{path: string; label: string; icon: ReactNode; testId: string; secondary?: boolean}>
}) {
  return (
    <nav id="main-navigation" data-ui="nav" aria-label="主导航">
      {items.map(item => (
        <a
          key={item.path}
          data-ui="nav-link"
          data-active={activePath === item.path ? 'true' : undefined}
          data-secondary={item.secondary ? 'true' : undefined}
          href={item.path}
          aria-label={item.label}
          title={item.label}
          aria-current={activePath === item.path ? 'page' : undefined}
          data-testid={item.testId}
        >
          {item.icon}
          <span data-ui="nav-label">{item.label}</span>
        </a>
      ))}
    </nav>
  )
}

export function Main({children}: {children: ReactNode}) {
  return <main id="main-content" data-ui="main" tabIndex={-1}>{children}</main>
}

export function AccountMenu({user, onLogout, compact = false}: {user: string; onLogout: () => void; compact?: boolean}) {
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

  const menuId = compact ? 'account-actions-mobile' : 'account-actions'
  return (
    <div ref={root} data-ui={compact ? 'mobile-account' : 'session'}>
      <button
        data-ui="account-button"
        type="button"
        data-testid={compact ? undefined : 'session-user'}
        aria-label={user + '，账号菜单'}
        title={user}
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen(value => !value)}
      >
        <span data-ui="avatar" aria-hidden="true">{user[0]}</span>
        {!compact && <span data-ui="account-name">{user}</span>}
        {!compact && <span data-ui="account-chevron" aria-hidden="true">▾</span>}
      </button>
      {open && (
        <div id={menuId} data-ui="account-actions">
          <Button variant="ghost" onClick={onLogout}>退出登录</Button>
        </div>
      )}
    </div>
  )
}
