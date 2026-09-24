import type {ReactNode} from 'react'
import {CirclePlus} from 'lucide-react'
import brandIcon from '../assets/brand.svg'
import filesIcon from '../assets/files.svg'
import rulesIcon from '../assets/rules.svg'
import {AccountMenu, AppBody, AppFrame, BrandLink, Icon, Main, MobileBar, PrimaryNav, Sidebar, SidebarBrand, SkipLink} from './index'

export const navigation = [
  {path: '/', label: '新建审核', testId: 'nav-home', icon: <Icon icon={CirclePlus} size="lg" />},
  {path: '/audit-files', label: '审核结果', testId: 'nav-audit-files', icon: <img src={filesIcon} alt="" width={20} height={20} />},
  {path: '/rules', label: '规则库', testId: 'nav-rules', icon: <img src={rulesIcon} alt="" width={20} height={20} />},
]

function Mark() {
  return <img src={brandIcon} alt="" width={22} height={22} />
}

export default function AppShell({
  activePath,
  user,
  onLogout,
  children,
}: {
  activePath: string
  user: string
  onLogout: () => void
  children: ReactNode
}) {
  return (
    <AppFrame>
      <nav aria-label="快速跳转"><SkipLink /></nav>
      <MobileBar>
        <BrandLink mark={<Mark />} />
        {user ? <AccountMenu user={user} onLogout={onLogout} compact /> : null}
      </MobileBar>
      <AppBody>
        <Sidebar testId="pc-shell">
          <SidebarBrand>
            <BrandLink mark={<img src={brandIcon} alt="" width={24} height={24} />} />
          </SidebarBrand>
          <PrimaryNav activePath={activePath} items={navigation} />
          {user ? <AccountMenu user={user} onLogout={onLogout} /> : null}
        </Sidebar>
        <Main>{children}</Main>
      </AppBody>
    </AppFrame>
  )
}
