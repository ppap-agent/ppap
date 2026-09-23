import type {ReactNode} from 'react'
import {CirclePlus} from 'lucide-react'
import brandIcon from '../assets/brand.svg'
import filesIcon from '../assets/files.svg'
import rulesIcon from '../assets/rules.svg'
import usageIcon from '../assets/usage.svg'
import {AccountMenu, AppBody, AppFrame, BrandLink, EditionBanner, Link, Main, MobileBar, MobileContext, PrimaryNav, Sidebar, SidebarBrand, SkipLink, VisuallyHidden} from './index'

export const navigation = [
  {path: '/', label: '新建审核', testId: 'nav-home', icon: <CirclePlus size={20} aria-hidden="true" />},
  {path: '/audit-files', label: '审核结果', testId: 'nav-audit-files', icon: <img src={filesIcon} alt="" width={20} height={20} />},
  {path: '/rules', label: '规则库', testId: 'nav-rules', icon: <img src={rulesIcon} alt="" width={20} height={20} />},
  {path: '/usage', label: '用量记录', testId: 'nav-usage', secondary: true, icon: <img src={usageIcon} alt="" width={20} height={20} />},
]

export function navigationTitle(path: string) {
  return navigation.find(link => link.path === path)?.label || 'PPAP'
}

function Mark() {
  return <img src={brandIcon} alt="" width={22} height={22} />
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
    <AppFrame>
      <MobileBar>
        <BrandLink mark={<Mark />} />
        <MobileContext>{title}</MobileContext>
        {user ? <Link href="/enter-legacy">旧版</Link> : null}
        {user ? <AccountMenu user={user} onLogout={onLogout} compact /> : null}
      </MobileBar>
      <AppBody>
        <SkipLink />
        <Sidebar testId="pc-shell">
          <SidebarBrand>
            <BrandLink mark={<img src={brandIcon} alt="" width={24} height={24} />} />
            <EditionBanner signedIn={!!user} />
          </SidebarBrand>
          <PrimaryNav activePath={activePath} items={navigation} />
          {user ? <AccountMenu user={user} onLogout={onLogout} /> : null}
        </Sidebar>
        <Main>
          <VisuallyHidden as="h1">{title}</VisuallyHidden>
          {children}
        </Main>
      </AppBody>
    </AppFrame>
  )
}
