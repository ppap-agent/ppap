import {RuleUpdates} from './rule-updates'
import {CheckerCatalog} from './checker-catalog'
import {useEffect, useState} from 'react'
import {RequirementLibraryPanel} from '../journey/requirement-library'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {readRuleVersions} from '../journey/product'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {groupRequirementDirectory, type RequirementDirectoryCollection} from '../journey/requirement-directory'
import {message} from '../journey/remote'
import {useSavedDraft} from '../journey/saved-draft'
import {RuleCoverage} from './rule-coverage'
import {useAccess} from '../journey/access'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {auditAddressChanged, writeAuditAddress} from './routes'
import {readRulesArea, rulesAreaAddress, type RulesArea} from './predefined-catalog'

export function AuditRules({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const principal = useAccess()?.principal
  return <RulesWorkspace key={principal ? `${principal.tenant_id}:${principal.user_id}` : 'preview'} onPublished={onPublished} active={active} headerActions={headerActions}/>
}

function RulesWorkspace({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const library = useSavedDraft('requirement-library', () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [read, setRead] = useState({loading: true, error: ''})
  const [revision, setRevision] = useState(0)
  const [directory, setDirectory] = useState<RequirementDirectoryCollection[]>([])
  const [area, setArea] = useState(() => readRulesArea(new URL(location.href)))
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const restore = () => setArea(readRulesArea(new URL(location.href)))
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setRead({loading: true, error: ''})
    void request<components['schemas']['RuleLibrary']>('/rule-library', 'GET', undefined, controller.signal).then(result => {
      if (controller.signal.aborted) return
      setDirectory(groupRequirementDirectory(result.versions))
      setRead({loading: false, error: ''})
    }).catch(cause => {if (!controller.signal.aborted) setRead({loading: false, error: message(cause)})})
    return () => controller.abort()
  }, [revision, active])
  return <>{area !== 'requirements' && <h1 className="sr-only">审核规则</h1>}<Tabs value={area} onValueChange={value => {if (!busy) writeAuditAddress(rulesAreaAddress(new URL(location.href), value as RulesArea), 'push')}} className="min-w-0">
    <TabsList aria-label="审核规则工作区" variant="line" className="h-auto max-w-full flex-wrap justify-start">
      <TabsTrigger value="requirements" disabled={busy}>客户要求</TabsTrigger>
      <TabsTrigger value="enterprise" disabled={busy}>企业检查版本</TabsTrigger>
      <TabsTrigger value="predefined" disabled={busy}>预定义检查</TabsTrigger>
    </TabsList>
    <TabsContent value={area} className="min-w-0">
      {area === 'requirements'
        ? <RequirementLibraryPanel catalogIntro={<><RuleUpdates active={active} revision={revision}/><RuleCoverage active={active} revision={revision}/></>} headerActions={headerActions} title="客户要求" uploadLabel="上传规则" automaticMatching active={active} collections={collections} onChange={setCollections} directory={directory} readVersions={readRuleVersions} readRevision={revision} uses={[]} onOpen={() => {}} saveError={library.saveError} loading={read.loading} readError={read.error} onReload={() => setRevision(value => value + 1)} onPublished={() => {setRevision(value => value + 1); onPublished()}}/>
        : <CheckerCatalog active={active} revision={revision} mode={area} onBusyChange={setBusy}/>}
    </TabsContent>
  </Tabs></>
}
