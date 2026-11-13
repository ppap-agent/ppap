import {useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import '../index.css'
import {personalDriveUpload, request} from '../api'
import {sourceLocation} from '../helpers'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {ProductAccess} from '../journey/access'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {RemoteSourcePane} from '../journey/remote-source'
import {AuditIntake, type AuditIntakeAdapter} from './intake'
import {createNativeTemplateLibraryAdapter} from './template-library-adapter'
import {createNativeAuditAdapter, nativeArchiveAdapter, nativeDisplayAdapter, nativeReportAdapter, nativeReviewAdapter, nativeSharingAdapter, nativeSupportAdapter, nativeUsageAdapter, readAuditCustomers} from './native-adapter'
import type {OriginalRenderer} from './review-content'
import type {ReadRound} from './workspace'
import {AuditReviewProvider} from './review-editor'
import {SharedReports} from './shared-reports'
import {AuditRules} from './rules'
import {canonicalAddress, readAuditRoute} from './routes'
import {Toaster} from '../components/toast'

const renderOriginal: OriginalRenderer = (file, evidence, roundId) => <div className="flex h-[65dvh] min-h-96 min-w-0"><RemoteSourcePane file={{file_id: file.file_id, file_version_id: file.id, original_filename: file.original_filename, role: 'target', rule_version_id: null}} roundId={roundId} focus={evidence?.locator ? {location: sourceLocation({locator: evidence.locator}), requestId: 0} : undefined}/></div>

function NativeAudit() {
  const [adapter, setAdapter] = useState<AuditIntakeAdapter>(() => ({...createNativeAuditAdapter({personalDriveUpload, customers: []}), readRound}))
  const [error, setError] = useState(''), [revision, setRevision] = useState(0)
  useEffect(() => {
    const address = new URL(location.href)
    const route = readAuditRoute(address)
    if (!revision && (route.taskId || address.searchParams.has('round') || route.surface !== 'audit')) return
    let active = true
    void readAuditCustomers().then(result => {
      if (!active) return
      if (!Array.isArray(result.customers) || result.customers.some(row => !row.customer_id || !row.name) || new Set(result.customers.map(row => row.customer_id)).size !== result.customers.length) throw new Error('客户规则目录暂时无法读取，请重新读取。')
      const customers = result.customers.map(row => row.name)
      setAdapter(current => ({...current, customers: customers.map(name => ({value: name, label: name}))})); setError('')
    }).catch(cause => {if (active) setError(cause instanceof Error ? cause.message : '无法读取审核规则。')})
    return () => {active = false}
  }, [revision])
  // The notice follows the page in reading order, so "跳到主要内容" stays the first stop.
  return <><AuditIntake adapter={adapter} templateAdapter={templateAdapter} onRefreshCustomers={() => setRevision(value => value + 1)} rules={(active, headerActions) => <AuditRules headerActions={headerActions} active={active} onPublished={() => setRevision(value => value + 1)}/>} archiveAdapter={nativeArchiveAdapter} usageAdapter={nativeUsageAdapter} displayAdapter={nativeDisplayAdapter} reportAdapter={nativeReportAdapter} sharingAdapter={nativeSharingAdapter} supportAdapter={nativeSupportAdapter} renderOriginal={renderOriginal}/>{error && <div className="fixed right-4 bottom-4 z-40 max-w-md"><Alert variant="destructive"><AlertTitle>客户规则目录未能刷新</AlertTitle><AlertDescription><p>已保存的规则和当前待审文件仍保留。</p><Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新读取客户目录</Button></AlertDescription></Alert></div>}</>
}

const templateAdapter = createNativeTemplateLibraryAdapter({personalDriveUpload})
const readRound: ReadRound = (id, signal) => request(`/rounds/${encodeURIComponent(id)}?read_view=true`, 'GET', undefined, signal)
// Old Deep entry links (retired with Deep audits, #322) open a new ordinary task.
if (['prepareDeep', 'deepDraft', 'deepAccept'].some(key => new URL(location.href).searchParams.has(key))) location.replace('/tasks/new')
const shareId = new URL(location.href).searchParams.get('share')
history.replaceState(history.state, '', canonicalAddress(new URL(location.href)))
createRoot(document.getElementById('app')!).render(<ProductAccess recordValidators={{'requirement-library': isRequirementLibrary}} recordsReturnTo="/requirements"><AuditReviewProvider adapter={nativeReviewAdapter}>{shareId ? <SharedReports key={shareId} requestId={shareId}/> : <NativeAudit/>}<Toaster/></AuditReviewProvider></ProductAccess>)
