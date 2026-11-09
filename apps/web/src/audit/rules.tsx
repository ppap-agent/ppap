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

export function AuditRules({onPublished, active, headerActions}: {onPublished: () => void; active: boolean; headerActions?: HTMLElement | null}) {
  const library = useSavedDraft('requirement-library', () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [read, setRead] = useState({loading: true, error: ''})
  const [revision, setRevision] = useState(0)
  const [directory, setDirectory] = useState<RequirementDirectoryCollection[]>([])
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
  return <RequirementLibraryPanel catalogIntro={<><RuleUpdates active={active} revision={revision}/><RuleCoverage active={active} revision={revision}/></>} catalogFooter={<CheckerCatalog active={active} revision={revision}/>} headerActions={headerActions} title="审核规则" uploadLabel="上传规则" automaticMatching active={active} collections={collections} onChange={setCollections} directory={directory} readVersions={readRuleVersions} readRevision={revision} uses={[]} onOpen={() => {}} saveError={library.saveError} loading={read.loading} readError={read.error} onReload={() => setRevision(value => value + 1)} onPublished={() => {setRevision(value => value + 1); onPublished()}}/>
}
