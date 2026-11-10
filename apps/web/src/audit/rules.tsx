import {RuleUpdates} from './rule-updates'
import {CheckerCatalog} from './checker-catalog'
import {useEffect, useState} from 'react'
import {RequirementLibraryPanel} from '../journey/requirement-library'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {readRuleVersions} from '../journey/product'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {publishedLibraryAfterRead, type RequirementDirectoryCollection} from '../journey/requirement-directory'
import {message} from '../journey/remote'
import {useSavedDraft} from '../journey/saved-draft'
import {RuleCoverage} from './rule-coverage'
import {useAccess} from '../journey/access'
import {auditAddressChanged, writeAuditAddress} from './routes'
import {readRulesArea} from './predefined-catalog'
import {Disclosure} from '../components/disclosure'
import {Button} from '../components/ui/button'
import {buildRuleCatalog, type CatalogRow} from './rules-catalog'

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
  const [entries, setEntries] = useState<components['schemas']['RuleLibraryEntry'][]>([])
  const [suites, setSuites] = useState<{official: {path: string; title: string}[]; enterprise: {suiteKey: string; title: string; ready: boolean}[]; error: string}>({official: [], enterprise: [], error: ''})
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
      const next = publishedLibraryAfterRead(false, result.versions)
      setEntries(next.versions)
      setDirectory(next.groups)
      setRead({loading: false, error: ''})
    }).catch(cause => {
      if (controller.signal.aborted) return
      const next = publishedLibraryAfterRead(true, [])
      setEntries(next.versions)
      setDirectory(next.groups)
      setRead({loading: false, error: message(cause)})
    })
    return () => controller.abort()
  }, [revision, active])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void request<components['schemas']['SuiteCatalog']>('/suites', 'GET', undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return
      setSuites({
        official: (value.predefined_error ? [] : value.predefined ?? []).map(item => ({path: item.path, title: item.title})),
        enterprise: (value.suites ?? []).map(item => ({suiteKey: item.suite_key, title: item.title, ready: Boolean(item.current_revision_id)})),
        error: value.predefined_error ? '系统提供的检查目录不可用。' : '',
      })
    }).catch(cause => {if (!controller.signal.aborted) setSuites({official: [], enterprise: [], error: message(cause)})})
    return () => controller.abort()
  }, [active, revision])
  function backToRules() {
    const url = new URL(location.href)
    url.searchParams.delete('checkerSuite')
    url.searchParams.delete('checkerPath')
    url.searchParams.delete('rulesArea')
    writeAuditAddress(url, 'push')
  }
  function openCatalog(row: CatalogRow) {
    if (busy || row.sourceKind === 'publishedRule') return
    const url = new URL(location.href)
    url.searchParams.delete('requirement'); url.searchParams.delete('reqVersion'); url.searchParams.delete('reqTab')
    if (row.sourceKind === 'officialSuite') {url.searchParams.set('checkerPath', row.href); url.searchParams.delete('checkerSuite')}
    else {url.searchParams.set('checkerSuite', row.href); url.searchParams.delete('checkerPath')}
    writeAuditAddress(url, 'push')
  }
  function openPredefined() {
    const url = new URL(location.href)
    url.searchParams.delete('requirement'); url.searchParams.delete('reqVersion'); url.searchParams.delete('checkerSuite'); url.searchParams.delete('checkerPath')
    url.searchParams.set('rulesArea', 'predefined')
    writeAuditAddress(url, 'push')
  }
  const catalogRows = buildRuleCatalog({
    rules: entries.map(row => ({id: row.id, title: row.title, documentType: row.document_type, customer: row.scope.allCustomers ? '' : row.scope.customer, part: row.scope.allParts ? '' : row.scope.part, suiteKey: row.suite_key ?? null})),
    official: suites.official,
    enterprise: suites.enterprise,
  }).filter(row => row.sourceKind !== 'publishedRule')
  const checker = area === 'enterprise' || area === 'predefined'
  return <>
    {checker && <div className="mb-4"><Button variant="ghost" onClick={backToRules}>返回审核规则</Button></div>}
    {checker
      ? <CheckerCatalog active={active} revision={revision} mode={area === 'predefined' ? 'predefined' : 'enterprise'} onBusyChange={setBusy}/>
      : <RequirementLibraryPanel
          catalogIntro={<div className="space-y-3"><RuleUpdates active={active} revision={revision}/><Disclosure summary="覆盖情况"><RuleCoverage active={active} revision={revision}/></Disclosure><div className="flex flex-wrap items-center justify-between gap-2"><Button variant="outline" size="sm" disabled={busy} onClick={openPredefined}>查看系统提供的检查</Button>{suites.error && <p role="alert" className="text-sm text-destructive">{suites.error}</p>}</div></div>}
          catalogRows={catalogRows}
          onOpenCatalog={openCatalog}
          headerActions={headerActions}
          title="审核规则"
          uploadLabel="上传规则"
          automaticMatching
          active={active}
          collections={collections}
          onChange={setCollections}
          directory={directory}
          readVersions={readRuleVersions}
          readRevision={revision}
          uses={[]}
          onOpen={() => {}}
          saveError={library.saveError}
          loading={read.loading}
          readError={read.error}
          onReload={() => setRevision(value => value + 1)}
          onPublished={() => {setRevision(value => value + 1); onPublished()}}
        />}
  </>
}
