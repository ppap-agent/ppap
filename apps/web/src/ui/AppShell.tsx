import type {ReactNode} from 'react'
import {BarChart3, ChevronDown, CirclePlus, Download, LogOut, PanelLeft, WifiOff} from 'lucide-react'
import brandIcon from '../assets/brand.svg'
import filesIcon from '../assets/files.svg'
import rulesIcon from '../assets/rules.svg'
import {Button} from '@/components/ui/button'
import {DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from '@/components/ui/dropdown-menu'
import {useInstallPrompt, useOnlineStatus} from '../pwa'

export const navigation = [
  {path: '/', label: '新建审核', testId: 'nav-home', icon: <CirclePlus className="size-5" aria-hidden="true" />},
  {path: '/audit-files', label: '审核结果', testId: 'nav-audit-files', icon: <img src={filesIcon} alt="" width={20} height={20} />},
  {path: '/rules', label: '规则库', testId: 'nav-rules', icon: <img src={rulesIcon} alt="" width={20} height={20} />},
]

function Mark() { return <img src={brandIcon} alt="" width={22} height={22} /> }

function initials(user: string) {
  const value = user.trim()
  return value ? value.slice(0, 1).toUpperCase() : 'U'
}

function AccountMenu({user, onLogout, compact = false, development = false, canInstall, onInstall}: {user: string; onLogout: () => void; compact?: boolean; development?: boolean; canInstall: boolean; onInstall: () => void}) {
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button
        type="button"
        variant={compact ? 'ghost' : 'outline'}
        size={compact ? 'profile-icon' : 'profile'}
        data-testid={compact ? undefined : 'session-user'}
        data-ui={compact ? 'account-button' : undefined}
        aria-label={user + '，账号菜单'}
        title={user}
      >
        <span aria-hidden="true" className="grid size-8 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">{initials(user)}</span>
        {!compact && <span className="min-w-0 flex-1 truncate text-sm font-medium">{user}</span>}
        {!compact && <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent
      align={compact ? 'end' : 'start'}
      side={compact ? 'bottom' : 'right'}
      className="w-72 max-w-[calc(100vw-1rem)]"
      data-ui={compact ? 'mobile-account' : 'account-menu'}
    >
      <DropdownMenuLabel className="mx-1 my-1">
        <div className="flex items-center gap-3 rounded-lg bg-muted/50 p-3">
          <span aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{initials(user)}</span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{user}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{development ? '本地测试身份' : '飞书账号'}</p>
          </div>
        </div>
      </DropdownMenuLabel>
      <DropdownMenuSeparator className="my-2" />
      <DropdownMenuGroup>
        <DropdownMenuItem asChild className="min-h-9">
          <a href="/usage" data-testid="nav-usage"><BarChart3 className="text-muted-foreground" aria-hidden="true" /><span>用量记录</span></a>
        </DropdownMenuItem>
        <DropdownMenuItem asChild className="min-h-9">
          <a href="/enter-legacy"><PanelLeft className="text-muted-foreground" aria-hidden="true" /><span>切换到旧版</span></a>
        </DropdownMenuItem>
        {canInstall && <DropdownMenuItem className="min-h-9" onSelect={onInstall}><Download className="text-muted-foreground" aria-hidden="true" /><span>安装到设备</span></DropdownMenuItem>}
      </DropdownMenuGroup>
      <DropdownMenuSeparator className="my-2" />
      <DropdownMenuItem variant="destructive" className="min-h-9" onSelect={onLogout}>
        <LogOut aria-hidden="true" /><span>退出登录</span>
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
}

export default function AppShell({
  activePath,
  user,
  development = false,
  onLogout,
  children,
}: {
  activePath: string
  user: string
  development?: boolean
  onLogout: () => void
  children: ReactNode
}) {
  const online = useOnlineStatus()
  const {canInstall, install} = useInstallPrompt()
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <nav aria-label="快速跳转"><a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-background focus:px-4 focus:py-2 focus:shadow-lg">跳到主要内容</a></nav>
      <header className="sticky top-0 z-40 flex min-h-14 items-center justify-between gap-2 border-b bg-background/95 px-4 backdrop-blur lg:hidden">
        <a href="/" aria-label="PPAP 首页" className="inline-flex min-h-9 items-center gap-2 text-foreground no-underline"><Mark /><strong className="text-sm font-semibold">PPAP</strong></a>
        {user ? <div data-ui="mobile-account"><AccountMenu user={user} onLogout={onLogout} compact development={development} canInstall={canInstall} onInstall={() => void install()} /></div> : null}
      </header>
      <div className="flex min-h-0 min-w-0 flex-1">
        <aside data-testid="pc-shell" className="hidden w-56 shrink-0 border-r bg-card lg:flex">
          <div className="flex min-h-0 flex-1 flex-col p-3">
            <div className="px-2 pb-6 pt-2"><a href="/" aria-label="PPAP 首页" className="inline-flex min-h-9 items-center gap-2 text-foreground no-underline"><img src={brandIcon} alt="" width={24} height={24} /><strong className="text-sm font-semibold">PPAP</strong></a></div>
            <nav id="main-navigation" aria-label="主导航" className="flex flex-col gap-1">
              {navigation.map(item => <a key={item.path} data-testid={item.testId} href={item.path} aria-label={item.label} title={item.label} aria-current={activePath === item.path ? 'page' : undefined} className={activePath === item.path ? 'flex min-h-10 items-center gap-3 rounded-lg bg-primary/10 px-3 text-sm font-medium text-primary no-underline' : 'flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium text-muted-foreground no-underline hover:bg-muted hover:text-foreground'}>{item.icon}<span>{item.label}</span></a>)}
            </nav>
          {user ? <div className="mt-auto border-t pt-3"><AccountMenu user={user} onLogout={onLogout} development={development} canInstall={canInstall} onInstall={() => void install()} /></div> : null}
          </div>
        </aside>
        <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 p-4 pb-24 lg:p-6 lg:pb-6 xl:p-8">
          {!online && <div role="status" aria-live="polite" className="mb-4 flex items-center gap-2 rounded-lg border border-chart-2 bg-secondary px-3 py-2 text-sm text-secondary-foreground"><WifiOff className="size-4 shrink-0" aria-hidden="true" /><span>离线模式：已打开的页面仍可查看；上传、审核和复核需要联网。</span></div>}
          {children}
        </main>
      </div>
      <nav aria-label="主导航" className="mobile-tab-bar fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 backdrop-blur lg:hidden">
        <div className="mx-auto grid max-w-lg grid-cols-3">
          {navigation.map(item => <a key={item.path} data-testid={item.testId} href={item.path} aria-current={activePath === item.path ? 'page' : undefined} className={activePath === item.path ? 'flex min-h-14 flex-col items-center justify-center gap-1 bg-primary/5 text-xs font-medium text-primary no-underline' : 'flex min-h-14 flex-col items-center justify-center gap-1 text-xs font-medium text-muted-foreground no-underline hover:bg-muted hover:text-foreground'}>{item.icon}<span>{item.label}</span></a>)}
        </div>
      </nav>
    </div>
  )
}
