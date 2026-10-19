import {useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import '../index.css'
import {deepOffered, request} from '../api'
import {sourceLocation} from '../helpers'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {ProductAccess} from '../journey/access'
import {isRequirementLibrary} from '../journey/requirement-library-state'
import {RemoteSourcePane} from '../journey/remote-source'
import {AuditIntake, type AuditIntakeAdapter} from './intake'
import {createNativeAuditAdapter, nativeArchiveAdapter, nativeDisplayAdapter, nativeFeedbackAdapter, nativeReportAdapter, nativeReviewAdapter, nativeSharingAdapter, nativeSupportAdapter, nativeTemplateAdapter, nativeTemplatePermissionAdapter, nativeUsageAdapter} from './native-adapter'
import type {OriginalRenderer} from './review-content'
import {AuditRoundReader, type ReadRound} from './round-reader'
import {AuditReviewProvider} from './review-editor'
import {validCatalog} from './template-contract'
import {SharedReports} from './shared-reports'
import {AuditRules} from './rules'

const renderOriginal: OriginalRenderer = (file, evidence, roundId) => <div className="flex h-[65dvh] min-h-96 min-w-0"><RemoteSourcePane file={{file_id: file.file_id, file_version_id: file.id, original_filename: file.original_filename, role: 'target', rule_version_id: null}} roundId={roundId} focus={evidence?.locator ? {location: sourceLocation({locator: evidence.locator}), requestId: 0} : undefined}/></div>

function NativeAudit() {
  const [adapter, setAdapter] = useState<AuditIntakeAdapter | null>(null)
  const [error, setError] = useState(''), [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    void nativeTemplateAdapter.readCatalog().then(result => {
      if (!active) return
      if (!validCatalog(result)) throw new Error('客户规则目录暂时无法读取，请重新读取。')
      const customers = result.customers.map(row => row.name)
      setAdapter(current => current ? {...current, customers: customers.map(name => ({value: name, label: name}))} : createNativeAuditAdapter({deepOffered, customers})); setError('')
    }).catch(cause => {if (active) setError(cause instanceof Error ? cause.message : '无法读取审核规则。')})
    return () => {active = false}
  }, [revision])
  if (adapter) return <>{error && <Alert variant="destructive"><AlertTitle>客户规则目录未能刷新</AlertTitle><AlertDescription><p>已保存的规则和当前待审文件仍保留。</p><Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新读取客户目录</Button></AlertDescription></Alert>}<AuditIntake adapter={adapter} rules={active => <AuditRules active={active} onPublished={() => setRevision(value => value + 1)}/>} archiveAdapter={nativeArchiveAdapter} usageAdapter={nativeUsageAdapter} displayAdapter={nativeDisplayAdapter} reportAdapter={nativeReportAdapter} sharingAdapter={nativeSharingAdapter} supportAdapter={nativeSupportAdapter} feedbackAdapter={nativeFeedbackAdapter} templateAdapter={nativeTemplateAdapter} templatePermissionAdapter={nativeTemplatePermissionAdapter} renderOriginal={renderOriginal}/></>
  return <main className="mx-auto max-w-xl space-y-4 p-6">{error ? <><Alert variant="destructive"><AlertTitle>暂时无法打开审核</AlertTitle><AlertDescription>{error}</AlertDescription></Alert><Button onClick={() => setRevision(value => value + 1)}>重新读取</Button></> : <p role="status">正在读取审核规则…</p>}</main>
}

const readRound: ReadRound = (id, signal) => request(`/rounds/${encodeURIComponent(id)}?read_view=true`, 'GET', undefined, signal)
const roundId = new URL(location.href).searchParams.get('round')
const shareId = new URL(location.href).searchParams.get('share')
createRoot(document.getElementById('app')!).render(<ProductAccess recordValidators={{'requirement-library': isRequirementLibrary}} recordsReturnTo="/audit?view=requirements"><AuditReviewProvider adapter={nativeReviewAdapter}>{shareId ? <SharedReports key={shareId} requestId={shareId}/> : roundId ? <AuditRoundReader key={roundId} roundId={roundId} readRound={readRound} renderOriginal={renderOriginal}/> : <NativeAudit/>}</AuditReviewProvider></ProductAccess>)
