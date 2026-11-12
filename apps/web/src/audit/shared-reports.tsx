import {ReadSkeleton} from '../components/read-skeleton'
import {useEffect, useMemo, useRef, useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Item, ItemContent, ItemDescription, ItemGroup, ItemTitle} from '../components/ui/item'
import {readSharedArchive, sharedReportAdapter} from './native-adapter'
import {ReportPreview} from './report-preview'
import {conclusionLabel, resultTone} from './status-labels'
import {Badge} from '../components/ui/badge'

export function SharedReports({requestId}: {requestId: string}) {
  const [result, setResult] = useState<components['schemas']['AuditSharedReports'] | null>(null)
  const [selected, setSelected] = useState(''), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [downloading, setDownloading] = useState(false), [downloadError, setDownloadError] = useState('')
  const epoch = useRef(0)
  const adapter = useMemo(() => sharedReportAdapter(requestId), [requestId])
  useEffect(() => {
    let active = true
    epoch.current++; setResult(null); setError(''); setDownloading(false); setDownloadError('')
    void request<components['schemas']['AuditSharedReports']>(`/audit-report-shares/${encodeURIComponent(requestId)}/reports`).then(value => {
      if (active) {setResult(value); setSelected(value.reports[0]?.reference.report_id || '')}
    }).catch(failure => {
      if (!active) return
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      // Expired or not permitted needs the sender; anything else is worth another try.
      setError(status === 403 || status === 404 ? '这份分享已失效，或你没有查看权限。请使用原组织的飞书账号登录，或联系发送人。' : '暂时无法读取这份分享，请重试。')
    })
    return () => {active = false; epoch.current++}
  }, [requestId, revision])
  async function downloadZip() {
    const artifact = result?.archive
    if (!artifact || downloading) return
    const run = epoch.current
    setDownloading(true); setDownloadError('')
    let url = ''
    try {
      const blob = await readSharedArchive(requestId, artifact)
      if (epoch.current !== run) return
      url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = artifact.filename; link.click()
    } catch {if (epoch.current === run) setDownloadError('ZIP 暂时无法下载，请重试。')}
    finally {if (url) URL.revokeObjectURL(url); if (epoch.current === run) setDownloading(false)}
  }
  const current = result?.reports.find(row => row.reference.report_id === selected)
  return <main className="mx-auto max-w-7xl space-y-6 p-8"><header className="flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-semibold">审核报告</h1><div className="flex flex-wrap gap-2">{result?.archive && <Button variant="outline" disabled={downloading} onClick={() => void downloadZip()}>{downloading ? '正在读取 ZIP…' : '下载报告 ZIP'}</Button>}<Button variant="outline" asChild><a href="/workbench">返回审核工作台</a></Button></div></header>{downloadError && <p role="alert">{downloadError}</p>}{error ? <Alert variant="destructive"><AlertTitle>分享暂不可用</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" className="mt-2" onClick={() => setRevision(value => value + 1)}>重新读取</Button></AlertDescription></Alert> : !result ? <ReadSkeleton label="正在读取分享报告…" kind={'document'}/> : <div className="grid min-w-0 grid-cols-[18rem_minmax(0,1fr)] gap-6"><nav aria-label="分享的报告"><ItemGroup role="group">{result.reports.map(row => <Item key={row.reference.report_id} asChild variant={selected === row.reference.report_id ? 'muted' : 'default'}><button type="button" className="text-left" aria-current={selected === row.reference.report_id ? 'true' : undefined} onClick={() => setSelected(row.reference.report_id)}><ItemContent className="min-w-0"><ItemTitle wrap>{row.name}</ItemTitle><ItemDescription><Badge shape="square" variant={resultTone(row.conclusion)}>{conclusionLabel[row.conclusion] ?? '结果未知'}</Badge></ItemDescription></ItemContent></button></Item>)}</ItemGroup></nav>{current && <ReportPreview verification={current.checker_verification} name={current.name} rule={current.rule.title} ruleVersion={current.rule.id} reference={current.reference} report={current.report} ready issue="" adapter={adapter}/>}</div>}</main>
}
