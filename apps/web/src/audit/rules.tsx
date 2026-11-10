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
import {readRulesArea, type RulesArea} from './predefined-catalog'
import {Disclosure} from '../components/disclosure'
import {Button} from '../components/ui/button'

/** One read of the checker catalog serves the whole rules page; it is slow, so it is never repeated per section. */
export type SuitesRead = {value: components['schemas']['SuiteCatalog'] | null; loading: boolean; error: string; revision: number}

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
  const [suites, setSuites] = useState<SuitesRead>({value: null, loading: true, error: '', revision: 0})
  const [suitesAttempt, setSuitesAttempt] = useState(0)
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
      setDirectory(publishedLibraryAfterRead(false, result.versions).groups)
      setRead({loading: false, error: ''})
    }).catch(cause => {
      if (controller.signal.aborted) return
      setDirectory(publishedLibraryAfterRead(true, []).groups)
      setRead({loading: false, error: message(cause)})
    })
    return () => controller.abort()
  }, [revision, active])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setSuites(previous => ({...previous, loading: true, error: ''}))
    void request<components['schemas']['SuiteCatalog']>('/suites', 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) setSuites(previous => ({value, loading: false, error: '', revision: previous.revision + 1}))
    }).catch(cause => {if (!controller.signal.aborted) setSuites(previous => ({value: null, loading: false, error: message(cause), revision: previous.revision + 1}))})
    return () => controller.abort()
  }, [active, revision, suitesAttempt])
  const reloadSuites = () => setSuitesAttempt(value => value + 1)
  function backToRules() {
    const url = new URL(location.href)
    url.searchParams.delete('checkerSuite')
    url.searchParams.delete('checkerPath')
    url.searchParams.delete('rulesArea')
    writeAuditAddress(url, 'push')
  }
  function openArea(next: Exclude<RulesArea, 'requirements'>) {
    const url = new URL(location.href)
    url.searchParams.delete('requirement'); url.searchParams.delete('reqVersion'); url.searchParams.delete('checkerSuite'); url.searchParams.delete('checkerPath'); url.searchParams.delete('rulesArea')
    if (next === 'predefined') url.searchParams.set('rulesArea', 'predefined'); else url.searchParams.set('checkerSuite', '')
    writeAuditAddress(url, 'push')
  }
  const checker = area === 'enterprise' || area === 'predefined'
  return <>
    {checker && <div className="mb-4"><Button variant="ghost" onClick={backToRules}>返回审核规则</Button></div>}
    {checker
      ? <CheckerCatalog active={active} suites={suites} onReload={reloadSuites} mode={area === 'predefined' ? 'predefined' : 'enterprise'} onBusyChange={setBusy}/>
      : <RequirementLibraryPanel
          catalogIntro={<div className="space-y-3">
            <RuleUpdates suites={suites} onReload={reloadSuites}/>
            <nav aria-label="检查" className="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="sm" disabled={busy} onClick={() => openArea('predefined')}>查看系统提供的检查</Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => openArea('enterprise')}>企业检查版本</Button>
            </nav>
            <Disclosure summary="覆盖情况"><RuleCoverage active={active} revision={revision} suites={suites}/></Disclosure>
          </div>}
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
