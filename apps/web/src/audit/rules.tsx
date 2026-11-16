import {useEffect, useState} from 'react'
import {RequirementLibraryPanel} from '../journey/requirement-library'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {readRuleVersions} from '../journey/product'
import {request, ruleUploadEnabled, type RulesList, type RuleTypes} from '../api'
import type {components} from '../generated/openapi'
import {publishedLibraryAfterRead, requirementDirectory, type RequirementDirectoryCollection} from '../journey/requirement-directory'
import {message} from '../journey/remote'
import {useSavedDraft} from '../journey/saved-draft'
import {useAccess} from '../journey/access'
import {auditAddressChanged} from './routes'
import {buildTemplates, enterpriseTemplateKey} from './rule-templates'
import {rulesAddress, RulesTrail, SystemRuleDetail, TemplateBrowser} from './rule-templates-view'
import {ProgramStatus} from './program-status'
import {writeAuditAddress} from './routes'

/** One read of the program catalog labels every customer-rule row; it is never repeated per row. */
export type SuitesRead = {value: components['schemas']['SuiteCatalog'] | null; loading: boolean; error: string; revision: number}

export function AuditRules({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const principal = useAccess()?.principal
  return <RulesWorkspace key={principal ? `${principal.tenant_id}:${principal.user_id}` : 'preview'} onPublished={onPublished} active={active} headerActions={headerActions}/>
}

const routeParams = () => {
  const url = new URL(location.href)
  return {template: url.searchParams.get('template') ?? '', systemRule: url.searchParams.get('systemRule') ?? ''}
}

function RulesWorkspace({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const library = useSavedDraft('requirement-library', () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [read, setRead] = useState<{loading: boolean; error: string; rules: RulesList['rules']; types: string[]}>({loading: true, error: '', rules: [], types: []})
  const [revision, setRevision] = useState(0)
  const [directory, setDirectory] = useState<RequirementDirectoryCollection[]>([])
  const [suites, setSuites] = useState<SuitesRead>({value: null, loading: true, error: '', revision: 0})
  const [route, setRoute] = useState(routeParams)
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
  }, [active, revision])
  const catalog = suites.loading ? null : suites.value
  const templates = buildTemplates({rules: read.rules, library: requirementDirectory(collections, directory), catalog: suites.error ? 'failed' : catalog, standardTypes: read.types})
  const systemTemplate = route.systemRule ? templates.find(item => item.rows.some(row => row.target.kind === 'system' && row.target.id === route.systemRule)) : undefined
  const systemRow = systemTemplate?.rows.find(row => row.target.id === route.systemRule)
  // A platform program is adopted on its rule's page; a related program opens the rule that owns it.
  const suite = systemRow?.automation.suiteKey ? catalog?.suites.find(item => item.suite_key === systemRow.automation.suiteKey) : undefined
  const related = (key: string) => {
    const owner = templates.flatMap(item => item.rows).find(row => row.automation.suiteKey === key)
    return owner && {label: owner.label, open: () => writeAuditAddress(owner.target.kind === 'system' ? rulesAddress({systemRule: owner.target.id}) : rulesAddress({requirement: owner.target.id}), 'push')}
  }
  // Adopting a program changes which rules count as effective, so the catalog and the customer rules are read again.
  const programFor = (suiteKey: string, key = suiteKey) => <ProgramStatus key={key} suiteKey={suiteKey} related={related} onChanged={() => setRevision(value => value + 1)}/>
  const program = active && suite ? programFor(suite.suite_key) : undefined
  const listView = route.systemRule
    ? <SystemRuleDetail ruleId={route.systemRule} template={systemTemplate} row={systemRow} program={program}/>
    : <TemplateBrowser templates={templates} selectedKey={route.template} loading={read.loading} failed={Boolean(read.error)}/>
  return <RequirementLibraryPanel
    listView={listView}
    trail={version => {
      // The page bar already leads back to the library; the trail names only the customer, as on a system rule.
      const key = version ? enterpriseTemplateKey(version.scope) : ''
      const owner = templates.find(item => item.key === key)
      return owner ? <RulesTrail items={[{label: owner.name, href: rulesAddress({template: owner.key})}]}/> : null
    }}
    program={(suiteKey, versionId) => programFor(suiteKey, versionId)}
    headerActions={headerActions}
    title="审核规则库"
    uploadLabel="上传规则"
    editable={ruleUploadEnabled}
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
