import {CheckerCatalog} from './checker-catalog'
import {useEffect, useState} from 'react'
import {RequirementLibraryPanel} from '../journey/requirement-library'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {readRuleVersions} from '../journey/product'
import {request, type RulesList, type RuleTypes} from '../api'
import type {components} from '../generated/openapi'
import {publishedLibraryAfterRead, requirementDirectory, type RequirementDirectoryCollection} from '../journey/requirement-directory'
import {message} from '../journey/remote'
import {useSavedDraft} from '../journey/saved-draft'
import {useAccess} from '../journey/access'
import {auditAddressChanged} from './routes'
import {readRulesArea} from './predefined-catalog'
import {Automation, attentionCount} from './automation'
import {buildTemplates, enterpriseTemplateKey} from './rule-templates'
import {rulesAddress, RulesTrail, SystemRuleDetail, TemplateDetail, TemplateList} from './rule-templates-view'
import {publishAutomationAttention} from './automation-attention'

/** One read of the checker catalog serves the whole rules page; it is never repeated per section. */
export type SuitesRead = {value: components['schemas']['SuiteCatalog'] | null; loading: boolean; error: string; revision: number}

export function AuditRules({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const principal = useAccess()?.principal
  return <RulesWorkspace key={principal ? `${principal.tenant_id}:${principal.user_id}` : 'preview'} onPublished={onPublished} active={active} headerActions={headerActions}/>
}

const routeParams = () => {
  const url = new URL(location.href)
  return {area: readRulesArea(url), template: url.searchParams.get('template') ?? '', systemRule: url.searchParams.get('systemRule') ?? ''}
}

function RulesWorkspace({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const library = useSavedDraft('requirement-library', () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [read, setRead] = useState<{loading: boolean; error: string; rules: RulesList['rules']; types: string[]}>({loading: true, error: '', rules: [], types: []})
  const [revision, setRevision] = useState(0)
  const [directory, setDirectory] = useState<RequirementDirectoryCollection[]>([])
  const [suites, setSuites] = useState<SuitesRead>({value: null, loading: true, error: '', revision: 0})
  const [suitesAttempt, setSuitesAttempt] = useState(0)
  const [route, setRoute] = useState(routeParams)
  const [, setBusy] = useState(false)
  useEffect(() => {
    const restore = () => setRoute(routeParams())
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setRead(previous => ({...previous, loading: true, error: ''}))
    // Uploaded rules are this page's own data; presets and type names only add to the templates, so their failure leaves the page usable.
    void Promise.all([
      request<components['schemas']['RuleLibrary']>('/rule-library', 'GET', undefined, controller.signal),
      request<RulesList>('/rules', 'GET', undefined, controller.signal).catch(() => ({rules: []})),
      request<RuleTypes>('/rule-types', 'GET', undefined, controller.signal).catch(() => ({types: []})),
    ]).then(([result, rules, types]) => {
      if (controller.signal.aborted) return
      setDirectory(publishedLibraryAfterRead(false, result.versions).groups)
      setRead({loading: false, error: '', rules: rules.rules, types: types.types})
    }).catch(cause => {
      if (controller.signal.aborted) return
      setDirectory(publishedLibraryAfterRead(true, []).groups)
      setRead({loading: false, error: message(cause), rules: [], types: []})
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
  useEffect(() => {if (!suites.loading) publishAutomationAttention(attentionCount(suites))}, [suites])
  const reloadSuites = () => setSuitesAttempt(value => value + 1)
  const catalog = suites.loading ? null : suites.value
  const templates = buildTemplates({rules: read.rules, library: requirementDirectory(collections, directory), catalog: suites.error ? 'failed' : catalog, standardTypes: read.types})
  const template = templates.find(item => item.key === route.template)
  const systemTemplate = route.systemRule ? templates.find(item => item.rows.some(row => row.target.kind === 'system' && row.target.id === route.systemRule)) : undefined
  if (route.area === 'automation') return <Automation active={active} suites={suites} onReload={reloadSuites} onBusyChange={setBusy}/>
  if (route.area === 'predefined') return <div className="space-y-6"><RulesTrail items={[{label: '自动审核', href: rulesAddress({rulesArea: 'automation'})}, {label: '系统程序目录'}]}/><CheckerCatalog active={active} suites={suites} onReload={reloadSuites} onBusyChange={setBusy}/></div>
  const listView = route.systemRule
    ? <SystemRuleDetail ruleId={route.systemRule} template={systemTemplate} row={systemTemplate?.rows.find(row => row.target.id === route.systemRule)}/>
    : route.template ? <TemplateDetail template={read.loading && !template ? undefined : template} catalogKnown={catalog !== null}/>
      : <TemplateList templates={templates} loading={read.loading} failed={Boolean(read.error)} catalogKnown={catalog !== null}/>
  return <RequirementLibraryPanel
    listView={listView}
    trail={version => {
      const key = version ? enterpriseTemplateKey(version.scope) : ''
      const owner = templates.find(item => item.key === key)
      const row = version && owner?.rows.find(item => item.type === version.documentType)
      return <RulesTrail items={[{label: '规则模板', href: rulesAddress({})}, ...owner ? [{label: owner.name, href: rulesAddress({template: owner.key})}] : [], {label: row?.label || version?.documentType || version?.title || '规则'}]}/>
    }}
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
  />
}
