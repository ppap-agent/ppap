import {useEffect, useState} from 'react'
import {RequirementLibraryPanel} from '../journey/requirement-library'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {loadRuleCollections} from '../journey/product'
import {message} from '../journey/remote'
import {useSavedDraft} from '../journey/saved-draft'

export function AuditRules({onPublished, active}: {onPublished: () => void; active: boolean}) {
  const library = useSavedDraft('requirement-library', () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [read, setRead] = useState({loading: true, error: ''})
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setRead({loading: true, error: ''})
    void loadRuleCollections().then(rows => {
      if (!active) return
      setCollections(current => [...current.filter(item => item.draft), ...rows.filter(row => !current.some(item => item.draft && item.versions.some(version => version.id === row.id)))])
      setRead({loading: false, error: ''})
    }).catch(cause => {if (active) setRead({loading: false, error: message(cause)})})
    return () => {active = false}
  }, [revision])
  return <RequirementLibraryPanel title="审核规则" uploadLabel="上传规则" automaticMatching active={active} collections={collections} onChange={setCollections} uses={[]} onOpen={() => {}} saveError={library.saveError} loading={read.loading} readError={read.error} onReload={() => setRevision(value => value + 1)} onPublished={onPublished}/>
}
